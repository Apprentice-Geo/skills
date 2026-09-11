from pathlib import Path

import pytest

from scripts import process_logging


@pytest.fixture(autouse=True)
def isolate_process_logs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(process_logging, "SKILL_DIR", tmp_path)
