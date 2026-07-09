import sys
from pathlib import Path

import pytest

from scripts.process_logging import ProcessLogger, SetupError


def test_process_logger_keeps_success_output_out_of_terminal(
    workspace_tmp_path: Path,
    capsys,
) -> None:
    log_path = workspace_tmp_path / "setup.log"
    logger = ProcessLogger(log_path)

    logger.step(1, 2, "Install dependencies")
    result = logger.run(
        [
            sys.executable,
            "-c",
            "import sys; print('normal stdout'); print('normal stderr', file=sys.stderr)",
        ],
        "Install dependencies",
    )

    terminal = capsys.readouterr()
    assert terminal.out == "[1/2] Install dependencies\n"
    assert terminal.err == ""
    assert result.returncode == 0
    log_text = log_path.read_text(encoding="utf-8")
    assert "normal stdout" in log_text
    assert "normal stderr" in log_text


def test_process_logger_replays_failed_command_output(
    workspace_tmp_path: Path,
    capsys,
) -> None:
    log_path = workspace_tmp_path / "setup.log"
    logger = ProcessLogger(log_path)

    with pytest.raises(SetupError, match="Download model"):
        logger.run(
            [
                sys.executable,
                "-c",
                "import sys; print('fatal detail', file=sys.stderr); raise SystemExit(2)",
            ],
            "Download model",
        )

    terminal = capsys.readouterr()
    assert "fatal detail" in terminal.err
    assert str(log_path) in terminal.err
    assert "fatal detail" in log_path.read_text(encoding="utf-8")
