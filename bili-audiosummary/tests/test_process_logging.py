import sys
from pathlib import Path

from process_logging import LoggingSession, YtDlpLogger, get_logger


def test_logging_session_filters_terminal_and_keeps_traceback(
    workspace_tmp_path: Path,
    capsys,
) -> None:
    log_path = workspace_tmp_path / "pipeline.log"

    with LoggingSession(log_path):
        logger = get_logger(__name__)
        logger.info("file only")
        logger.info("terminal line", extra={"terminal": True})
        try:
            raise RuntimeError("traceback detail")
        except RuntimeError:
            logger.exception("pipeline failed")

    terminal = capsys.readouterr()
    assert terminal.out == "terminal line\n"
    assert terminal.err == ""
    log_text = log_path.read_text(encoding="utf-8")
    assert "file only" in log_text
    assert "terminal line" in log_text
    assert "Traceback (most recent call last)" in log_text
    assert "RuntimeError: traceback detail" in log_text


def test_logging_session_moves_active_log(
    workspace_tmp_path: Path,
) -> None:
    original_path = workspace_tmp_path / ".cache" / "logs" / "pipeline.log"
    result_dir = workspace_tmp_path / "results" / "BVTEST"

    with LoggingSession(original_path) as session:
        get_logger(__name__).info("before move")
        moved_path = session.move_to(result_dir)
        get_logger(__name__).info("after move")

    assert moved_path == result_dir / "pipeline.log"
    assert not original_path.exists()
    log_text = moved_path.read_text(encoding="utf-8")
    assert "before move" in log_text
    assert "after move" in log_text


def test_logging_session_keeps_original_log_when_move_fails(
    workspace_tmp_path: Path,
    mocker,
) -> None:
    original_path = workspace_tmp_path / ".cache" / "logs" / "pipeline.log"
    result_dir = workspace_tmp_path / "results" / "BVTEST"
    mocker.patch("process_logging.shutil.move", side_effect=OSError("move denied"))

    with LoggingSession(original_path) as session:
        moved_path = session.move_to(result_dir)
        get_logger(__name__).info("continued after move failure")

    assert moved_path == original_path
    log_text = original_path.read_text(encoding="utf-8")
    assert "Unable to move log" in log_text
    assert "OSError: move denied" in log_text
    assert "continued after move failure" in log_text


def test_ytdlp_logger_writes_to_file_without_terminal_output(
    workspace_tmp_path: Path,
    capsys,
) -> None:
    log_path = workspace_tmp_path / "fetch.log"

    with LoggingSession(log_path):
        logger = YtDlpLogger(get_logger("fetch_audio"))
        logger.debug("[BiliBili] Downloading webpage")
        logger.warning("third-party warning")
        logger.error("third-party error")

    terminal = capsys.readouterr()
    assert terminal.out == ""
    assert terminal.err == ""
    log_text = log_path.read_text(encoding="utf-8")
    assert "[BiliBili] Downloading webpage" in log_text
    assert "third-party warning" in log_text
    assert "third-party error" in log_text


def test_logging_session_replays_failure_and_log_path(
    workspace_tmp_path: Path,
    capsys,
) -> None:
    log_path = workspace_tmp_path / "pipeline.log"

    with LoggingSession(log_path) as session:
        try:
            raise RuntimeError("fatal detail")
        except RuntimeError as exc:
            session.report_failure(exc)

    terminal = capsys.readouterr()
    assert terminal.out == ""
    assert "RuntimeError: fatal detail" in terminal.err
    assert f"Full log: {log_path}" in terminal.err
    log_text = log_path.read_text(encoding="utf-8")
    assert "Traceback (most recent call last)" in log_text
    assert "RuntimeError: fatal detail" in log_text
