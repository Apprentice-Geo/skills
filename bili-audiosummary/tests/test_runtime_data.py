from __future__ import annotations

import errno
import logging
import os
import subprocess
import sys
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


def test_external_pipeline_resume_complete_remove(tmp_path, monkeypatch):
    import argparse

    from scripts import (
        complete_summary,
        continue_summary,
        remove_summary_job,
        run_pipeline,
    )
    from scripts.summary_job import JobValidationError
    from scripts.utils import read_json
    from tests.test_continue_summary import transcription
    from tests.test_run_pipeline import make_fetch_result

    data = tmp_path / "external-data"
    data.mkdir()
    monkeypatch.setenv(DATA_DIR_ENV, str(data))
    fetch = make_fetch_result(data)

    def fake_fetch(options):
        assert options.output_dir == data / "results"
        return fetch

    monkeypatch.setattr(run_pipeline.fetch_audio, "run_fetch", fake_fetch)
    outcome = run_pipeline.run_pipeline(
        argparse.Namespace(
            url="https://www.bilibili.com/video/BVTEST/", language="en", data_dir=data
        )
    )
    job_path = outcome["job_path"]
    assert job_path == data / "results" / "BVTEST" / "summary_job.json"
    monkeypatch.setattr(
        continue_summary,
        "load_transcription",
        lambda _path: transcription(
            continue_summary._file_sha256(fetch["audio_files"][0])
        ),
    )
    resumed = continue_summary.continue_summary(
        job_path, tmp_path / "absent-manifest.json", results_dir=data / "results"
    )
    assert resumed["status"] == "prompt_ready"
    # 完成导入后恢复只读取本地 snapshot。
    monkeypatch.setattr(
        continue_summary,
        "load_transcription",
        lambda _path: pytest.fail("upstream reloaded"),
    )
    assert (
        continue_summary.continue_summary(
            job_path, tmp_path / "absent-manifest.json", results_dir=data / "results"
        )
        == resumed
    )
    summary = job_path.parent / resumed["prompt"]["summary_path"]
    summary.write_text("# Summary\n\nEnglish only text.\n")
    completed, validation = complete_summary.complete_summary(
        job_path, results_dir=data / "results"
    )
    assert validation.ok and completed["status"] == "complete"
    assert read_json(job_path)["status"] == "complete"
    monkeypatch.setenv(DATA_DIR_ENV, str(tmp_path / "different-data"))
    with pytest.raises(JobValidationError, match="configured results"):
        continue_summary.continue_summary(job_path, tmp_path / "manifest.json")
    with pytest.raises(JobValidationError, match="configured results"):
        complete_summary.complete_summary(job_path)
    with pytest.raises(JobValidationError):
        remove_summary_job.remove_summary_job(
            job_path, results_dir=tmp_path / "other" / "results"
        )
    returned, removed = remove_summary_job.remove_summary_job(
        job_path, results_dir=data / "results"
    )
    assert removed and returned == job_path
    assert not job_path.parent.exists()


def test_fetch_context_default_and_explicit_output_are_independent(tmp_path):
    from scripts.runtime_options import FetchOptions

    paths = RuntimePaths.resolve(tmp_path / "data")
    assert FetchOptions(url="url", paths=paths).output_dir == paths.results_dir
    output = tmp_path / "downloads"
    options = FetchOptions(url="url", paths=paths, output_dir=output)
    assert options.output_dir == output.resolve()
    assert options.paths.logs_dir == paths.logs_dir


@pytest.mark.parametrize(
    "module_name,arguments",
    [
        (
            "run_pipeline",
            ["https://www.bilibili.com/video/BVTEST/", "--language", "en"],
        ),
        ("fetch_audio", ["https://www.bilibili.com/video/BVTEST/"]),
        ("subtitle_transcript", ["subtitle.srt", "--manifest", "manifest.json"]),
        ("validate_summary", ["summary.md"]),
        ("continue_summary", ["job.json", "--transcription-manifest", "manifest.json"]),
        ("complete_summary", ["job.json"]),
        ("remove_summary_job", ["job.json"]),
    ],
)
def test_bili_workflow_start_failure_preserves_cookie_exit_semantics(
    tmp_path, monkeypatch, capsys, module_name, arguments
):
    import importlib

    module = importlib.import_module(f"scripts.{module_name}")

    def deny(*args, **kwargs):
        raise PermissionError(errno.EACCES, "permission denied")

    monkeypatch.setattr(logs.LoggingSession, "_make_file_handler", deny)
    monkeypatch.setattr(
        sys, "argv", [module_name, *arguments, "--data-dir", str(tmp_path / "data")]
    )
    assert module.main() == 1
    terminal = capsys.readouterr()
    assert terminal.out == ""
    assert "log not created" in terminal.err
    assert "Traceback" not in terminal.err and "Full log:" not in terminal.err


@pytest.mark.parametrize("link_type", ["symlink", "junction"])
def test_remove_rejects_link_without_touching_other_jobs(
    tmp_path, monkeypatch, link_type
):
    from scripts import remove_summary_job as removal

    data = tmp_path / "data"
    target = data / "results" / "BVTEST"
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
        removal.remove_summary_job(
            target / "summary_job.json", results_dir=data / "results"
        )
    assert marker.read_text() == "keep"
