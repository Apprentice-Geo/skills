from pathlib import Path

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
