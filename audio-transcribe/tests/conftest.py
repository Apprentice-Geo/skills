import json
import re
import shutil
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

repo_root_text = str(REPO_ROOT)
if repo_root_text not in sys.path:
    sys.path.insert(0, repo_root_text)


@pytest.fixture
def installed_models(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Tiny marked installations for tests that mock inference, not validation."""
    from scripts.model_artifacts import LANGUAGE_ID_REQUIRED_FILES
    from scripts.model_identity import MODEL_REVISIONS

    root = tmp_path / "models"
    for provider, identity in MODEL_REVISIONS.items():
        directory = root / identity["logical_id"]
        directory.mkdir(parents=True)
        (directory / ".model_identity.json").write_text(
            json.dumps({key: identity[key] for key in ("repo", "revision")}),
            encoding="utf-8",
        )
        filenames = (
            LANGUAGE_ID_REQUIRED_FILES
            if provider == "language-id"
            else ("model.bin",)
            if provider == "faster-whisper"
            else ("model.safetensors",)
        )
        for filename in filenames:
            (directory / filename).write_bytes(b"fake model")
        if provider == "qwen3-asr":
            aligner = root / identity["aligner_logical_id"]
            aligner.mkdir()
            (aligner / ".model_identity.json").write_text(
                json.dumps(
                    {key: identity[f"aligner_{key}"] for key in ("repo", "revision")}
                ),
                encoding="utf-8",
            )
            (aligner / "model.safetensors").write_bytes(b"fake aligner")
    monkeypatch.setattr("scripts.transcribe.MODELS_DIR", root)
    monkeypatch.setattr(
        "scripts.asr.providers.whisper.DEFAULT_WHISPER_MODEL_DIR",
        root / "faster-whisper-small",
    )
    monkeypatch.setattr(
        "scripts.asr.providers.qwen3_asr.QWEN3_ASR_MODEL_DIR", root / "qwen3-asr-0.6b"
    )
    monkeypatch.setattr(
        "scripts.asr.providers.qwen3_asr.QWEN3_ASR_ALIGNER_MODEL_DIR",
        root / "qwen3-forcedaligner-0.6b",
    )
    return root


@pytest.fixture
def workspace_tmp_path(request: pytest.FixtureRequest) -> Path:
    root = REPO_ROOT / "tmp" / "workspace"
    root.mkdir(parents=True, exist_ok=True)
    name = re.sub(r"[^0-9A-Za-z_.-]+", "_", request.node.name)
    path = root / name
    if path.exists():
        shutil.rmtree(path)
    path.mkdir()
    return path
