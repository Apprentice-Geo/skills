from __future__ import annotations

from pathlib import Path

import pytest

from scripts import remove_summary_job
from scripts.summary_job import JobValidationError


@pytest.fixture
def results_dir(workspace_tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = workspace_tmp_path / "results"
    monkeypatch.setattr(remove_summary_job, "RESULTS_DIR", path)
    return path


@pytest.mark.parametrize("video_id", ["BV1Test123", "BV1Test123_p2"])
def test_remove_deletes_complete_job_directory(
    results_dir: Path, video_id: str
) -> None:
    job_dir = results_dir / video_id
    job_dir.mkdir(parents=True)
    job_path = job_dir / "summary_job.json"
    job_path.write_text("invalid job is still removable", encoding="utf-8")
    nested = job_dir / "resource" / "audio.m4a"
    nested.parent.mkdir()
    nested.write_bytes(b"audio")

    returned_path, removed = remove_summary_job.remove_summary_job(job_path.resolve())

    assert returned_path == job_path.resolve()
    assert removed is True
    assert not job_dir.exists()


def test_remove_is_idempotent_when_job_is_absent(results_dir: Path) -> None:
    job_path = (results_dir / "BV1Test123" / "summary_job.json").resolve()

    returned_path, removed = remove_summary_job.remove_summary_job(job_path)

    assert returned_path == job_path
    assert removed is False


@pytest.mark.parametrize(
    "job_path",
    [
        Path("relative/summary_job.json"),
        Path(__file__).resolve(),
    ],
)
def test_remove_rejects_unsafe_paths(results_dir: Path, job_path: Path) -> None:
    with pytest.raises(JobValidationError):
        remove_summary_job.remove_summary_job(job_path)


def test_remove_rejects_non_video_directory_name(results_dir: Path) -> None:
    job_path = (results_dir / "not-a-video-id" / "summary_job.json").resolve()

    with pytest.raises(JobValidationError):
        remove_summary_job.remove_summary_job(job_path)
