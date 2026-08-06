import json
from pathlib import Path

from scripts.model_artifacts import model_has_weights
from scripts.model_identity import MODEL_REVISIONS
from scripts.setup import install_model
from scripts.setup.download_models import download_model


def test_setup_and_variant_identity_share_pinned_revisions() -> None:
    assert (
        install_model.WHISPER_MODEL_REVISION
        == (MODEL_REVISIONS["faster-whisper"]["revision"])
    )
    assert (
        install_model.QWEN3_ASR_MODEL_REVISION
        == MODEL_REVISIONS["qwen3-asr"]["revision"]
    )
    assert (
        install_model.QWEN3_ASR_ALIGNER_MODEL_REVISION
        == (MODEL_REVISIONS["qwen3-asr"]["aligner_revision"])
    )
    assert (
        install_model.LANGUAGE_ID_MODEL_REVISION
        == (MODEL_REVISIONS["language-id"]["revision"])
    )


def test_existing_weights_are_reused_only_with_matching_revision_marker(
    workspace_tmp_path: Path,
) -> None:
    model_dir = workspace_tmp_path / "model"
    model_dir.mkdir()
    (model_dir / "model.bin").write_bytes(b"weights")
    calls: list[list[object]] = []

    class Logger:
        def run(self, command, _description, *, env):
            del env
            calls.append(list(command))
            Path(command[-1]).mkdir(exist_ok=True)
            (Path(command[-1]) / "model.bin").write_bytes(b"weights")

    logger = Logger()
    kwargs = {
        "python": Path("python.exe"),
        "repo_id": "owner/model",
        "revision": "a" * 40,
        "model_dir": model_dir,
        "weight_patterns": ("model.bin",),
        "logger": logger,
        "env": {},
    }

    assert download_model(**kwargs) is True
    assert len(calls) == 1
    assert download_model(**kwargs) is False
    assert len(calls) == 1

    kwargs["revision"] = "b" * 40
    assert download_model(**kwargs) is True
    assert len(calls) == 2


def test_failed_download_keeps_previous_model(workspace_tmp_path: Path) -> None:
    model_dir = workspace_tmp_path / "model"
    model_dir.mkdir()
    (model_dir / "model.bin").write_bytes(b"old")
    (model_dir / ".model_identity.json").write_text(
        '{"repo": "owner/model", "revision": "a"}', encoding="utf-8"
    )

    class Logger:
        def run(self, *_args, **_kwargs):
            raise RuntimeError("download failed")

    try:
        download_model(
            Path("python.exe"),
            "owner/model",
            "b",
            model_dir,
            ("model.bin",),
            Logger(),
            {},
        )
    except RuntimeError:
        pass
    else:
        raise AssertionError("download should fail")
    assert (model_dir / "model.bin").read_bytes() == b"old"


def test_sharded_model_requires_index_and_all_shards(workspace_tmp_path: Path) -> None:
    model_dir = workspace_tmp_path / "model"
    model_dir.mkdir()
    (model_dir / "model-00001-of-00002.safetensors").write_bytes(b"one")
    assert not model_has_weights(model_dir, ("model*.safetensors",))
    (model_dir / "model.safetensors.index.json").write_text(
        '{"weight_map": {"a": "model-00001-of-00002.safetensors", "b": "model-00002-of-00002.safetensors"}}',
        encoding="utf-8",
    )
    assert not model_has_weights(model_dir, ("model*.safetensors",))
    (model_dir / "model-00002-of-00002.safetensors").write_bytes(b"two")
    assert model_has_weights(model_dir, ("model*.safetensors",))


def test_malformed_shard_index_is_treated_as_incomplete(
    workspace_tmp_path: Path,
) -> None:
    model_dir = workspace_tmp_path / "model"
    model_dir.mkdir()
    (model_dir / "model-00001-of-00001.safetensors").write_bytes(b"one")

    for weight_map in (
        {"a": None},
        {"a": 1},
        {},
    ):
        (model_dir / "model.safetensors.index.json").write_text(
            json.dumps({"weight_map": weight_map}), encoding="utf-8"
        )
        assert not model_has_weights(model_dir, ("model*.safetensors",))


def test_shard_index_cannot_escape_model_directory(
    workspace_tmp_path: Path,
) -> None:
    model_dir = workspace_tmp_path / "model"
    model_dir.mkdir()
    (model_dir / "model-00001-of-00001.safetensors").write_bytes(b"one")
    (workspace_tmp_path / "outside.safetensors").write_bytes(b"outside")
    index_path = model_dir / "model.safetensors.index.json"

    for shard_name in (
        "../outside.safetensors",
        str(workspace_tmp_path / "outside.safetensors"),
        "",
    ):
        index_path.write_text(
            json.dumps({"weight_map": {"a": shard_name}}),
            encoding="utf-8",
        )
        assert not model_has_weights(model_dir, ("model*.safetensors",))


def test_single_safetensors_model_does_not_require_shard_index(
    workspace_tmp_path: Path,
) -> None:
    model_dir = workspace_tmp_path / "model"
    model_dir.mkdir()
    (model_dir / "model.safetensors").write_bytes(b"weights")

    assert model_has_weights(model_dir, ("model*.safetensors",))
