import logging
import sys
import warnings
from pathlib import Path

import pytest

from scripts.process_logging import (
    LoggingSession,
    detail,
    error,
    exception,
    get_logger,
    result,
    status,
    warning,
)
from scripts.process_logging import ProcessLogger, SetupError


def test_logging_session_routes_console_and_keeps_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    log_path = tmp_path / "process.log"
    with LoggingSession(log_path):
        logger = get_logger(__name__)
        detail(logger, "file only")
        status(logger, "status line")
        result(logger, "result line")
        warning(logger, "warning line")
        error(logger, "error line")
        try:
            raise RuntimeError("traceback detail")
        except RuntimeError:
            exception(logger, "process failed")

    terminal = capsys.readouterr()
    assert terminal.out == "status line\nresult line\n"
    assert terminal.err == "warning line\nerror line\n"
    log_text = log_path.read_text(encoding="utf-8")
    for message in ("file only", "status line", "result line", "warning line", "error line"):
        assert message in log_text
    assert "Traceback (most recent call last)" in log_text
    assert "RuntimeError: traceback detail" in log_text


def test_logging_session_move_and_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original_path = tmp_path / ".cache" / "logs" / "process.log"
    result_dir = tmp_path / "results" / "job"
    with LoggingSession(original_path) as session:
        detail(get_logger(__name__), "before move")
        moved_path = session.move_to(result_dir)
        detail(get_logger(__name__), "after move")
    assert moved_path == result_dir / "process.log"
    assert not original_path.exists()
    assert "before move" in moved_path.read_text(encoding="utf-8")
    assert "after move" in moved_path.read_text(encoding="utf-8")

    failed_path = tmp_path / ".cache" / "logs" / "failed.log"
    monkeypatch.setattr(
        "scripts.process_logging.shutil.move",
        lambda *_args: (_ for _ in ()).throw(OSError("denied")),
    )
    with LoggingSession(failed_path) as session:
        retained_path = session.move_to(result_dir)
        detail(get_logger(__name__), "continued")
    assert retained_path == failed_path
    retained = failed_path.read_text(encoding="utf-8")
    assert "Unable to move log" in retained
    assert "OSError: denied" in retained
    assert "continued" in retained


def test_session_captures_warnings_and_restores_state(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    log_path = tmp_path / "warnings.log"
    external = logging.getLogger("subtitle-test-external")
    original = (list(external.handlers), external.level, external.propagate, external.disabled)
    original_showwarning = warnings.showwarning
    try:
        with LoggingSession(log_path) as session:
            session.capture_logger(external.name)
            external.warning("third-party warning")
            warnings.warn("python warning", FutureWarning, stacklevel=1)
        terminal = capsys.readouterr()
        assert terminal.out == ""
        assert terminal.err == ""
        log_text = log_path.read_text(encoding="utf-8")
        assert "third-party warning" in log_text
        assert "python warning" in log_text
        assert (
            external.handlers,
            external.level,
            external.propagate,
            external.disabled,
        ) == original
        assert warnings.showwarning is original_showwarning
    finally:
        external.handlers[:] = original[0]
        external.setLevel(original[1])
        external.propagate = original[2]
        external.disabled = original[3]


def test_failure_is_concise_on_stderr_and_session_rejects_nesting(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    log_path = tmp_path / "failure.log"
    outer = LoggingSession(log_path).start()
    try:
        assert outer.start() is outer
        with pytest.raises(RuntimeError, match="already active"):
            LoggingSession(tmp_path / "nested.log").start()
        try:
            raise RuntimeError("fatal detail")
        except RuntimeError as exc:
            outer.report_failure(exc)
    finally:
        outer.close()

    terminal = capsys.readouterr()
    assert terminal.out == ""
    assert terminal.err == f"Error: fatal detail\nFull log: {log_path}\n"
    assert "Traceback" not in terminal.err
    assert "Traceback (most recent call last)" in log_path.read_text(encoding="utf-8")


def test_process_failure_output_stays_in_log(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    log_path = tmp_path / "setup.log"
    with ProcessLogger(log_path) as logger:
        with pytest.raises(SetupError, match="Sync dependencies"):
            logger.run(
                [
                    sys.executable,
                    "-c",
                    "import sys; print('private failure', file=sys.stderr); raise SystemExit(2)",
                ],
                "Sync dependencies",
            )

    terminal = capsys.readouterr()
    assert terminal.out == ""
    assert terminal.err == ""
    assert "private failure" in log_path.read_text(encoding="utf-8")
