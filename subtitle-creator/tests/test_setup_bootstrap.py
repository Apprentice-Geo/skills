from pathlib import Path

import pytest

from scripts.process_logging import ProcessResult, SetupError
from scripts.setup import bootstrap


class FakeProcessLogger:
    instances: list["FakeProcessLogger"] = []
    failure_description: str | None = None

    def __init__(self, log_path: Path) -> None:
        self.log_path = log_path
        self.commands: list[tuple[list[str], str, Path | None]] = []
        self.failures: list[BaseException] = []
        self.closed = False
        self.instances.append(self)

    def result(self, _message: str, *_args: object) -> None:
        pass

    def step(self, _current: int, _total: int, _message: str) -> None:
        pass

    def run(self, command, description: str, *, env=None, cwd=None, check=True) -> ProcessResult:
        command_text = [str(item) for item in command]
        self.commands.append((command_text, description, cwd))
        if description == self.failure_description:
            raise SetupError(f"{description} failed.")
        return ProcessResult(0, "")

    def report_failure(self, exc: BaseException) -> None:
        self.failures.append(exc)

    def close(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def reset_fake_logger() -> None:
    FakeProcessLogger.instances.clear()
    FakeProcessLogger.failure_description = None


def test_bootstrap_syncs_and_verifies_contract(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(bootstrap, "ProcessLogger", FakeProcessLogger)

    returned = bootstrap.run_setup(tmp_path)

    logger = FakeProcessLogger.instances[0]
    assert returned == logger.log_path
    assert [entry[:2] for entry in logger.commands] == [
        (["uv", "sync", "--python", "3.12", "--no-dev"], "Sync runtime dependencies"),
        (
            [
                "uv",
                "run",
                "--python",
                "3.12",
                "--no-sync",
                "python",
                "-c",
                "import audio_transcribe_contract",
            ],
            "Verify transcription contract import",
        ),
    ]
    assert all(entry[2] == tmp_path.resolve() for entry in logger.commands)
    assert logger.closed is True


@pytest.mark.parametrize(
    "description", ["Sync runtime dependencies", "Verify transcription contract import"]
)
def test_bootstrap_reports_sync_and_import_failures(
    monkeypatch, tmp_path: Path, description: str
) -> None:
    FakeProcessLogger.failure_description = description
    monkeypatch.setattr(bootstrap, "ProcessLogger", FakeProcessLogger)

    with pytest.raises(SetupError, match=description):
        bootstrap.run_setup(tmp_path)

    logger = FakeProcessLogger.instances[0]
    assert len(logger.failures) == 1
    assert logger.closed is True
