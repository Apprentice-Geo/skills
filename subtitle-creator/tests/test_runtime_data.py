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
    assert (list(logger.handlers), logger.level, logger.propagate, logger.disabled) == state
    assert warnings.showwarning is warning_hook
    assert warning_logger.handlers == warning_handlers
    assert logs.LoggingSession.current() is None
    # 部分初始化创建了文件时也不得留下打开的句柄。
    if path.exists():
        path.unlink()


def test_move_log_reopen_failure_is_concise_and_closes_session(tmp_path, monkeypatch, capsys):
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
def test_cli_stops_before_business_when_log_cannot_start(tmp_path, monkeypatch, capsys, entry):
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
        return {"skill": "test", "overall_status": "ready", "checks": [], "providers": {}}

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
        return {"skill": "test", "overall_status": "ready", "checks": [], "providers": {}}

    monkeypatch.setattr(check_dependencies, "run_check", ready)
    original_mkdir = Path.mkdir

    def restricted_mkdir(path, *args, **kwargs):
        if path.is_relative_to(source):
            raise PermissionError(errno.EACCES, "source is read-only", str(path))
        return original_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", restricted_mkdir)
    assert check_dependencies.main(["--root", str(source), "--data-dir", str(data)]) == 0
    assert sorted(path.name for path in source.iterdir()) == ["source.txt"]
    assert len(list((data / ".cache" / "logs").glob("*.json"))) == 1


@pytest.mark.skipif(os.name != "nt", reason="Windows launcher")
@pytest.mark.parametrize("cli_override", [False, True])
def test_setup_launcher_resolves_data_before_uv_and_cwd(tmp_path, cli_override):
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
                Path(environment.get("SystemRoot", environment.get("SYSTEMROOT", r"C:\Windows")))
                / "System32"
            ),
        )
    )
    environment["PATHEXT"] = ".COM;.EXE;.BAT;.CMD"
    environment["LAUNCH_LOG"] = str(launch_log)
    environment[DATA_DIR_ENV] = str(tmp_path / "environment")
    environment.pop("UV_CACHE_DIR", None)
    command = [str(root / "scripts/setup/setup_windows.bat")]
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
    assert not selected.exists()


def test_external_subtitle_create_attach_finalize_resume_remove(tmp_path, monkeypatch):
    from scripts import (
        bind_transcription,
        generate_srt,
        open_subtitle_job,
        remove_subtitle_job,
    )
    from scripts.subtitle_job import SubtitleJobError, read_json_object, sha256_file
    from scripts.transcription_input import TranscriptionInput

    data = tmp_path / "external-data"
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"audio")
    audio_id = sha256_file(audio)
    upstream = tmp_path / "upstream" / "manifest.json"
    upstream.parent.mkdir()
    upstream.write_text("opaque")
    snapshot = TranscriptionInput(
        audio_id=audio_id,
        provider="fake",
        language="en",
        duration=2.0,
        segments=[{"id": 0, "start": 0.0, "end": 1.0, "text": "hello"}],
    )
    monkeypatch.setattr(bind_transcription, "load_transcription", lambda _path: snapshot)
    common = ["--data-dir", str(data)]
    assert open_subtitle_job.main([str(audio), *common]) == 0
    job_path = data / "results" / audio_id / "subtitle_job.json"
    assert open_subtitle_job.main([str(audio), *common]) == 0
    assert (
        bind_transcription.main([str(job_path), "--transcription-manifest", str(upstream), *common])
        == 0
    )
    upstream.unlink()
    assert generate_srt.main([str(job_path), *common]) == 0
    assert generate_srt.main([str(job_path), *common]) == 0
    assert read_json_object(job_path)["status"] == "transcription_bound"
    # 切换目录不能误删另一根目录的任务。
    with pytest.raises(SubtitleJobError):
        remove_subtitle_job.remove_subtitle_job(
            job_path, results_dir=tmp_path / "other" / "results"
        )
    assert remove_subtitle_job.main([str(job_path), *common]) == 0
    assert not job_path.parent.exists()
    assert list((data / ".cache" / "logs").glob("remove-subtitle-job-*.log"))


@pytest.mark.parametrize(
    "module_name,arguments",
    [
        ("open_subtitle_job", ["audio.wav"]),
        ("bind_transcription", ["job.json", "--transcription-manifest", "manifest.json"]),
        ("generate_srt", ["job.json"]),
        ("remove_subtitle_job", ["job.json"]),
        ("transcript", ["reset", "job.json", "--id", "all"]),
    ],
)
def test_subtitle_workflow_start_failure_stops_before_job(
    tmp_path, monkeypatch, capsys, module_name, arguments
):
    import importlib

    module = importlib.import_module(f"scripts.{module_name}")

    def deny(*args, **kwargs):
        raise PermissionError(errno.EACCES, "permission denied")

    monkeypatch.setattr(logs.LoggingSession, "_make_file_handler", deny)
    assert module.main([*arguments, "--data-dir", str(tmp_path / "data")]) == 1
    terminal = capsys.readouterr()
    assert terminal.out == ""
    assert "log not created" in terminal.err
    assert "Traceback" not in terminal.err and "Full log:" not in terminal.err


@pytest.mark.parametrize("link_type", ["symlink", "junction"])
def test_remove_rejects_link_without_touching_other_jobs(tmp_path, monkeypatch, link_type):
    from scripts import remove_subtitle_job as removal

    data = tmp_path / "data"
    target = data / "results" / "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    target.mkdir(parents=True)
    marker = target / "keep.txt"
    marker.write_text("keep")
    original_symlink = Path.is_symlink
    if link_type == "symlink":
        monkeypatch.setattr(
            Path, "is_symlink", lambda path: path == target or original_symlink(path)
        )
    else:
        monkeypatch.setattr(removal, "_is_junction", lambda path: path == target)
    with pytest.raises(ValueError, match="link or junction"):
        removal.remove_subtitle_job(target / "subtitle_job.json", results_dir=data / "results")
    assert marker.read_text() == "keep"
