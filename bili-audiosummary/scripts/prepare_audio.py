from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from scripts import fetch_audio
from scripts.process_logging import (
    LoggingSession,
    create_timestamped_log_path,
    filesystem_cli,
    get_logger,
    result,
    status,
)
from scripts.runtime_options import FetchOptions
from scripts.runtime_paths import RuntimePaths, add_data_dir_argument
from scripts.summary_job import (
    JobValidationError,
    job_lock,
    load_job,
    publish_job,
    relative_path,
    require_configured_job_path,
    resolve_local_path,
)
from scripts.utils import read_json, write_json_atomic

logger = get_logger(__name__)


def _prepare_job_audio_unlocked(
    job_path: Path,
    job: dict[str, Any],
    *,
    paths: RuntimePaths,
    cookies: Path | None = None,
) -> dict[str, Any]:
    existing_audio = job["resources"]["audio"]
    if existing_audio is not None:
        audio_path = resolve_local_path(job_path, existing_audio, "resources.audio")
        if not audio_path.is_file():
            raise JobValidationError(f"Registered audio does not exist: {audio_path}")
        return job

    manifest_path = resolve_local_path(
        job_path, job["resources"]["fetch_manifest"], "resources.fetch_manifest"
    )
    manifest = read_json(manifest_path)
    video_id = job["video"]["bvid"]
    if not isinstance(manifest, dict) or manifest.get("id") != video_id:
        raise JobValidationError("Fetch manifest does not match the summary job")
    resource_dir = resolve_local_path(job_path, "resource", "audio resource directory")
    url = fetch_audio.build_canonical_url({"id": video_id}, video_id)
    options = FetchOptions(url=url, paths=paths, cookies=cookies)
    status(logger, "[Stage] Prepare audio for transcription")
    audio_files = fetch_audio.download_audio(url, resource_dir, video_id, options)
    if not audio_files:
        raise RuntimeError(
            "No usable audio file is available for external transcription."
        )
    # 发布前验证全部路径；下载失败或路径无效时不改变现有任务。
    for audio_path in audio_files:
        relative_path(audio_path, job_path.parent)
        if not audio_path.is_file():
            raise JobValidationError(f"Downloaded audio does not exist: {audio_path}")
    updated = {
        **job,
        "resources": {
            **job["resources"],
            "audio": relative_path(audio_files[0], job_path.parent),
        },
    }
    write_json_atomic(
        manifest_path,
        {
            **manifest,
            "audio_files": [path.resolve().as_posix() for path in audio_files],
        },
    )
    publish_job(job_path, updated)
    return updated


def prepare_audio(
    job_path: Path,
    *,
    paths: RuntimePaths | None = None,
    cookies: Path | None = None,
) -> dict[str, Any]:
    paths = paths or RuntimePaths.resolve()
    require_configured_job_path(job_path.absolute(), paths.results_dir)
    job_path = job_path.resolve()
    with job_lock(job_path):
        job = load_job(job_path)
        if job["status"] not in {"needs_transcription", "prompt_ready", "complete"}:
            raise JobValidationError(
                f"cannot prepare audio for summary job from status {job['status']!r}"
            )
        return _prepare_job_audio_unlocked(job_path, job, paths=paths, cookies=cookies)


@filesystem_cli
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Prepare audio for an existing summary job without replacing its input."
    )
    parser.add_argument("job", type=Path, help="Path to summary_job.json.")
    parser.add_argument("--cookies", type=Path, help="Netscape-format Cookie file.")
    add_data_dir_argument(parser)
    args = parser.parse_args(argv)
    paths = RuntimePaths.resolve(args.data_dir)
    log_path = create_timestamped_log_path(paths.logs_dir, "prepare-audio")
    with LoggingSession(log_path) as session:
        try:
            job = prepare_audio(args.job, paths=paths, cookies=args.cookies)
        except Exception as exc:
            session.report_failure(exc)
            return 2 if isinstance(exc, fetch_audio.CookieRequiredError) else 1
        result(logger, "Summary Job: %s", args.job.resolve())
        result(
            logger, "Audio: %s", args.job.resolve().parent / job["resources"]["audio"]
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
