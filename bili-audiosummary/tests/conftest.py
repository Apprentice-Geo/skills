import sys
import types
import re
import shutil
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
    root = REPO_ROOT / "test_workspace"
    root.mkdir(exist_ok=True)
    name = re.sub(r"[^0-9A-Za-z_.-]+", "_", request.node.name)
    path = root / name
    if path.exists():
        shutil.rmtree(path)
    path.mkdir()
    return path


@pytest.fixture
def sample_srt_path(workspace_tmp_path: Path) -> Path:
    path = workspace_tmp_path / "BVTEST.zh-Hans.srt"
    path.write_text(
        "\ufeff1\n"
        "00:00:01,000 --> 00:00:03,500\n"
        "第一句话。\n"
        "\n"
        "2\n"
        "00:00:04,000 --> 00:00:07,250\n"
        "第二句话\n"
        "换行继续。\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture
def invalid_srt_path(workspace_tmp_path: Path) -> Path:
    path = workspace_tmp_path / "BVTEST.invalid.zh-Hans.srt"
    path.write_text("not a subtitle file\n", encoding="utf-8")
    return path


@pytest.fixture
def manifest_payload() -> dict:
    return {
        "id": "BVTEST",
        "title": "测试视频",
        "url": "https://www.bilibili.com/video/BVTEST/",
    }


@pytest.fixture
def metadata_payload() -> dict:
    return {
        "uploader": "测试作者",
        "duration_string": "00:07",
    }
