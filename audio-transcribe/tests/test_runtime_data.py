from __future__ import annotations

import errno
import logging
import os
import subprocess
import warnings
from pathlib import Path

import pytest

from scripts import check_dependencies
from scripts import process_logging as logs
from scripts.runtime_paths import DATA_DIR_ENV, RuntimePaths
from scripts.setup import bootstrap


def test_runtime_paths_precedence_and_invocation_snapshot(tmp_path, monkeypatch):
    monkeypatch.delenv(DATA_DIR_ENV, raising=False)
    assert RuntimePaths.resolve(root=tmp_path).data_dir == tmp_path.resolve()
    monkeypatch.setenv(DATA_DIR_ENV, str(tmp_path / "environment"))
    paths = RuntimePaths.resolve(Path("relative-data"))
    assert paths.data_dir == Path("relative-data").resolve()
    environment_paths = RuntimePaths.resolve()
    monkeypatch.setenv(DATA_DIR_ENV, str(tmp_path / "next-invocation"))
    assert environment_paths.data_dir == (tmp_path / "environment").resolve()
    assert RuntimePaths.resolve().data_dir == (tmp_path / "next-invocation").resolve()
    environment = {}
    paths.configure_uv(environment)
    assert environment["UV_CACHE_DIR"] == str(paths.uv_cache_dir)
    environment["UV_CACHE_DIR"] = "explicit-cache"
    paths.configure_uv(environment)
    assert environment["UV_CACHE_DIR"] == "explicit-cache"
    assert not paths.data_dir.exists()


@pytest.mark.parametrize("operation", ["mkdir", "open", "capture"])
def test_log_start_failure_restores_all_logger_state(tmp_path, monkeypatch, operation):
    logger = logs.get_logger()
    state = (list(logger.handlers), logger.level, logger.propagate, logger.disabled)
    warning_hook = warnings.showwarning
    warning_logger = logging.getLogger("py.warnings")
    warning_handlers = list(warning_logger.handlers)
    path = tmp_path / ".cache" / "logs" / "failure.log"
    session = logs.LoggingSession(path)

    def deny(*args, **kwargs):
        raise PermissionError(errno.EACCES, "permission denied", str(path))

    if operation == "mkdir":
        monkeypatch.setattr(Path, "mkdir", deny)
    elif operation == "open":
        monkeypatch.setattr(logging, "FileHandler", deny)
    else:
        original_capture = session.capture_logger

        def fail_capture(name):
            original_capture(name)
            deny()

        monkeypatch.setattr(session, "capture_logger", fail_capture)
    with pytest.raises(OSError):
        session.start()
    assert (
        list(logger.handlers),
        logger.level,
        logger.propagate,
        logger.disabled,
    ) == state
    assert warnings.showwarning is warning_hook
    assert warning_logger.handlers == warning_handlers
    assert logs.LoggingSession.current() is None
    # 部分初始化创建了文件时也不得留下打开的句柄。
    if path.exists():
        path.unlink()


def test_move_log_reopen_failure_is_concise_and_closes_session(
    tmp_path, monkeypatch, capsys
):
    path = tmp_path / "original.log"
    session = logs.LoggingSession(path).start()
    destination = tmp_path / "job"
    target = destination / path.name
    original = session._make_file_handler

    def fail_reopen(path):
        if path == target:
            raise PermissionError(errno.EACCES, "permission denied", str(path))
        return original(path)

    monkeypatch.setattr(session, "_make_file_handler", fail_reopen)
    try:
        with pytest.raises(OSError) as raised:
            session.move_to(destination)
        session.report_failure(raised.value)
    finally:
        session.close()
    terminal = capsys.readouterr()
    assert terminal.out == ""
    assert str(target) in terminal.err
    assert "Full log:" not in terminal.err
    assert "Traceback" not in terminal.err
    assert logs.LoggingSession.current() is None
    assert target.is_file()
    target.unlink()


@pytest.mark.parametrize("entry", ["check", "setup"])
def test_cli_stops_before_business_when_log_cannot_start(
    tmp_path, monkeypatch, capsys, entry
):
    data_dir = tmp_path / "external"

    def deny(*args, **kwargs):
        raise PermissionError(errno.EACCES, "permission denied")

    monkeypatch.setattr(logs.LoggingSession, "_make_file_handler", deny)

    def unexpected(*args, **kwargs):
        pytest.fail("business or dependency installation ran after log startup failure")

    monkeypatch.setattr(check_dependencies, "run_check", unexpected)
    monkeypatch.setattr(logs.ProcessLogger, "run", unexpected)
    main = check_dependencies.main if entry == "check" else bootstrap.main
    assert main(["--data-dir", str(data_dir)]) == 1
    terminal = capsys.readouterr()
    assert terminal.out == ""
    assert str(data_dir / ".cache" / "logs") in terminal.err
    assert "PermissionError" in terminal.err and "13" in terminal.err
    assert "log not created" in terminal.err
    assert "Full log:" not in terminal.err and "Traceback" not in terminal.err
    assert logs.LoggingSession.current() is None
    assert not data_dir.exists()


@pytest.mark.parametrize("operation", ["write", "replace"])
def test_check_report_publish_failure_returns_failure_without_false_path(
    tmp_path, monkeypatch, capsys, operation
):
    def ready(*args, **kwargs):
        return {
            "skill": "test",
            "overall_status": "ready",
            "checks": [],
            "providers": {},
        }

    monkeypatch.setattr(check_dependencies, "run_check", ready)
    original_write = Path.write_text

    def deny_write(path, *args, **kwargs):
        if path.suffix == ".tmp":
            raise PermissionError(errno.EACCES, "report denied", str(path))
        return original_write(path, *args, **kwargs)

    def deny_replace(*args, **kwargs):
        raise PermissionError(errno.EACCES, "report denied")

    if operation == "write":
        monkeypatch.setattr(Path, "write_text", deny_write)
    else:
        monkeypatch.setattr(logs.os, "replace", deny_replace)
    assert check_dependencies.main(["--data-dir", str(tmp_path)]) == 1
    terminal = capsys.readouterr()
    assert "JSON report:" not in terminal.out
    assert "publish check report" in terminal.err
    assert str(tmp_path / ".cache" / "logs") in terminal.err
    assert "Full log:" in terminal.err and "Traceback" not in terminal.err
    assert len(list((tmp_path / ".cache" / "logs").glob("*.log"))) == 1
    assert not list(tmp_path.rglob("*.json"))
    assert not list(tmp_path.rglob("*.tmp"))
    assert logs.LoggingSession.current() is None


def test_check_uses_writable_data_without_writing_skill_root(tmp_path, monkeypatch):
    source = tmp_path / "read-only-source"
    data = tmp_path / "data"
    source.mkdir()
    source_file = source / "source.txt"
    source_file.write_text("unchanged")

    def ready(*args, **kwargs):
        return {
            "skill": "test",
            "overall_status": "ready",
            "checks": [],
            "providers": {},
        }

    monkeypatch.setattr(check_dependencies, "run_check", ready)
    original_mkdir = Path.mkdir

    def restricted_mkdir(path, *args, **kwargs):
        if path.is_relative_to(source):
            raise PermissionError(errno.EACCES, "source is read-only", str(path))
        return original_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", restricted_mkdir)
    assert (
        check_dependencies.main(["--root", str(source), "--data-dir", str(data)]) == 0
    )
    assert sorted(path.name for path in source.iterdir()) == ["source.txt"]
    assert len(list((data / ".cache" / "logs").glob("*.json"))) == 1


@pytest.mark.skipif(os.name != "nt", reason="Windows launcher")
@pytest.mark.parametrize("cli_override", [False, True])
@pytest.mark.parametrize("profile", ["cpu", "qwen3-asr"])
def test_setup_launcher_resolves_data_before_uv_and_cwd(
    tmp_path, cli_override, profile
):
    root = Path(__file__).resolve().parents[1]
    command_dir = tmp_path / "bin"
    command_dir.mkdir()
    launch_log = tmp_path / "launch.log"
    (command_dir / "uv.cmd").write_text(
        '@echo off\n>>"%LAUNCH_LOG%" echo %* [CACHE=%UV_CACHE_DIR%]\n', encoding="ascii"
    )
    environment = os.environ.copy()
    environment["PATH"] = os.pathsep.join(
        (
            str(command_dir),
            str(
                Path(
                    environment.get(
                        "SystemRoot", environment.get("SYSTEMROOT", r"C:\Windows")
                    )
                )
                / "System32"
            ),
        )
    )
    environment["PATHEXT"] = ".COM;.EXE;.BAT;.CMD"
    environment["LAUNCH_LOG"] = str(launch_log)
    environment[DATA_DIR_ENV] = str(tmp_path / "environment")
    environment.pop("UV_CACHE_DIR", None)
    command = [str(root / "scripts/setup/setup_windows.bat")]
    command.extend(["--environment", profile])
    if cli_override:
        command.extend(["--data-dir", "relative data"])
    completed = subprocess.run(
        [environment.get("COMSPEC", "cmd.exe"), "/d", "/c", *command],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    selected = tmp_path / ("relative data" if cli_override else "environment")
    invocations = launch_log.read_text()
    assert str(selected / ".cache" / "uv") in invocations
    assert f'--data-dir "{selected}"' in invocations
    assert f"--environment {profile}" in invocations
    assert not selected.exists()


def test_transcribe_cli_results_override_keeps_log_in_data(
    tmp_path, monkeypatch, capsys
):
    from scripts import transcribe

    data = tmp_path / "data"
    results = tmp_path / "separate-results"

    def fake_run(audio, **kwargs):
        assert kwargs["results_dir"] == results.resolve()
        assert kwargs["log_path"].parent == data / ".cache" / "logs"
        assert logs.LoggingSession.current() is not None
        return transcribe.TranscribeOutcome(results / "manifest.json", None)

    monkeypatch.setattr(transcribe, "run_transcribe", fake_run)
    assert (
        transcribe.main(
            ["audio.wav", "--data-dir", str(data), "--results-dir", str(results)]
        )
        == 0
    )
    assert list((data / ".cache" / "logs").glob("*.log"))
    assert not results.exists()
    assert "manifest:" in capsys.readouterr().out


def test_transcribe_log_failure_does_not_decode_or_publish(
    tmp_path, monkeypatch, capsys
):
    from scripts import transcribe

    def deny(*args, **kwargs):
        raise PermissionError(errno.EACCES, "permission denied")

    def unexpected(*args, **kwargs):
        pytest.fail("transcription ran after log startup failure")

    monkeypatch.setattr(logs.LoggingSession, "_make_file_handler", deny)
    monkeypatch.setattr(transcribe, "run_transcribe", unexpected)
    assert transcribe.main(["audio.wav", "--data-dir", str(tmp_path)]) == 1
    terminal = capsys.readouterr()
    assert "log not created" in terminal.err
    assert "Full log:" not in terminal.err and "Traceback" not in terminal.err
    assert terminal.out == ""


def test_setup_paths_move_only_data_not_environment_or_models(tmp_path):
    from scripts.setup.environment import SetupPaths, configure_environment

    source = tmp_path / "source"
    data = tmp_path / "data"
    paths = SetupPaths.from_root(source, data)
    assert paths.logs_dir == data / ".cache" / "logs"
    assert paths.results_dir == data / "results"
    assert paths.models_dir == source / "models"
    assert paths.venv_dir == source / ".venv"
    environment = {}
    configure_environment(paths, environment)
    assert not source.exists() and not data.exists()
    assert environment["UV_CACHE_DIR"] == str(data / ".cache" / "uv")


@pytest.mark.usefixtures("installed_models")
def test_storage_location_does_not_change_transcript_bytes_or_identity(tmp_path):
    from scripts.asr.alignment import AlignedTranscript, AlignmentItem
    from scripts.transcribe import run_transcribe

    audio = tmp_path / "audio.bin"
    audio.write_bytes(b"same source audio")

    def engine(samples, request, execution):
        return AlignedTranscript("hello", (AlignmentItem("hello", 0.0, 0.5, 0.9),))

    first = run_transcribe(
        audio,
        language="en",
        provider="faster-whisper",
        results_dir=tmp_path / "first",
        decoder=lambda _path: [0.0] * 16000,
        engine=engine,
    ).manifest_path
    second = run_transcribe(
        audio,
        language="en",
        provider="faster-whisper",
        results_dir=tmp_path / "second",
        decoder=lambda _path: [0.0] * 16000,
        engine=engine,
    ).manifest_path
    assert first.parent.name == second.parent.name
    assert first.read_bytes() == second.read_bytes()
    assert (first.parent / "transcript.json").read_bytes() == (
        second.parent / "transcript.json"
    ).read_bytes()
