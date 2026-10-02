from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolate_process_logs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUBTITLE_CREATOR_DATA_DIR", str(tmp_path))
