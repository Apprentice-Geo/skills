import re
import shutil
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

repo_root_text = str(REPO_ROOT)
if repo_root_text not in sys.path:
    sys.path.insert(0, repo_root_text)


try:
    import faster_whisper  # noqa: F401
except ImportError:
    faster_whisper_stub = types.ModuleType("faster_whisper")

    class WhisperModel:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

    faster_whisper_stub.WhisperModel = WhisperModel
    sys.modules["faster_whisper"] = faster_whisper_stub


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
