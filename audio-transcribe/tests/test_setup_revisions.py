import ast
import json
import tomllib
from pathlib import Path

import pytest

from scripts.model_artifacts import model_has_weights
from scripts.model_identity import MODEL_REVISIONS
from scripts.process_logging import SetupError
from scripts.setup import install_model
from scripts.setup.download_models import download_model
from scripts.setup.install_core import verify_cpu_pytorch_build


def test_dependency_profiles_and_sources_are_explicit_and_mutually_exclusive() -> None:
    root = Path(__file__).resolve().parents[1]
    config = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    project = config["project"]
    extras = project["optional-dependencies"]

    for dependency in ("speechbrain", "torch", "torchaudio"):
        assert dependency not in project["dependencies"]
        assert dependency in extras["cpu"]
        assert dependency in extras["qwen3-asr"]

    uv = config["tool"]["uv"]
    assert uv["sources"]["audio-transcribe-contract"] == {
        "path": "packages/audio-transcribe-contract"
    }
    assert uv["sources"]["torch"] == [
        {"index": "pytorch-cpu", "extra": "cpu"},
        {"index": "pytorch-cu126", "extra": "qwen3-asr"},
    ]
    assert uv["sources"]["torchaudio"] == uv["sources"]["torch"]
    assert uv["conflicts"] == [[{"extra": "cpu"}, {"extra": "qwen3-asr"}]]
    indexes = {entry["name"]: entry for entry in uv["index"]}
    assert indexes["pytorch-cpu"]["explicit"] is True
    assert indexes["pytorch-cpu"]["url"] == "https://download.pytorch.org/whl/cpu"
    assert indexes["pytorch-cu126"]["explicit"] is True
    assert indexes["pytorch-cu126"]["url"] == ("https://download.pytorch.org/whl/cu126")
    lock = (root / "uv.lock").read_text(encoding="utf-8")
    assert 'registry = "https://download.pytorch.org/whl/cpu"' in lock
    assert 'registry = "https://download.pytorch.org/whl/cu126"' in lock


def test_default_setup_selects_cpu_extra_and_never_all_extras() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (root / "scripts" / "setup" / "bootstrap.py").read_text(encoding="utf-8")

    tree = ast.parse(source)
    list_literals = [
        [element.value for element in node.elts]
        for node in ast.walk(tree)
        if isinstance(node, ast.List)
        and all(isinstance(element, ast.Constant) for element in node.elts)
    ]
    assert [
        "uv",
        "sync",
        "--python",
        "3.12",
        "--no-dev",
        "--extra",
        "cpu",
    ] in list_literals
    assert "--all-extras" not in source


@pytest.mark.parametrize(
    ("cuda_build", "raises"),
    [(None, False), ("12.6", True)],
)
def test_default_setup_requires_cpu_pytorch_build(cuda_build, raises) -> None:
    class Logger:
        def run(self, *_args, **_kwargs):
            payload = {
                "version": "2.7.1",
                "cuda_build": cuda_build,
                "cuda_available": False,
            }
            return type("Result", (), {"output": json.dumps(payload)})()

    if raises:
        with pytest.raises(SetupError, match="requires the CPU PyTorch build"):
            verify_cpu_pytorch_build(Path("python.exe"), Logger(), {})
    else:
        verify_cpu_pytorch_build(Path("python.exe"), Logger(), {})


@pytest.mark.parametrize(
    ("cuda_build", "cuda_available", "error"),
    [
        (None, False, "CUDA-enabled PyTorch build"),
        ("12.6", False, "available CUDA GPU"),
    ],
)
def test_qwen_environment_rejects_unready_cuda_before_model_download(
    cuda_build, cuda_available, error
) -> None:
    events: list[str] = []

    class Logger:
        def run(self, command, description, **_kwargs):
            events.append(description)
            if "CUDA environment" in description:
                payload = {
                    "version": "2.7.1",
                    "cuda_build": cuda_build,
                    "cuda_available": cuda_available,
                }
                return type("Result", (), {"output": json.dumps(payload)})()
            return type("Result", (), {"output": ""})()

    with pytest.raises(SetupError, match=error):
        install_model.verify_qwen3_asr_environment(Path("python.exe"), Logger())

    assert events == ["Verify Qwen3-ASR imports", "Verify Qwen3-ASR CUDA environment"]


def test_qwen_environment_accepts_cuda_build_with_available_gpu() -> None:
    class Logger:
        def run(self, _command, description, **_kwargs):
            output = ""
            if "CUDA environment" in description:
                output = json.dumps(
                    {
                        "version": "2.7.1+cu126",
                        "cuda_build": "12.6",
                        "cuda_available": True,
                    }
                )
            return type("Result", (), {"output": output})()

    install_model.verify_qwen3_asr_environment(Path("python.exe"), Logger())


def test_setup_and_config_identity_share_pinned_revisions() -> None:
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
