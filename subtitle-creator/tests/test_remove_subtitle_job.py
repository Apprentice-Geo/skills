from __future__ import annotations

from pathlib import Path

import pytest

from scripts import remove_subtitle_job, subtitle_job
from scripts.subtitle_job import SubtitleJobError


@pytest.fixture
def results_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "results"
    monkeypatch.setattr(subtitle_job, "RESULTS_DIR", path)
    monkeypatch.setattr(remove_subtitle_job, "RESULTS_DIR", path)
    return path


def test_remove_deletes_complete_job_directory(results_dir: Path) -> None:
    job_dir = results_dir / ("a" * 64)
    job_dir.mkdir(parents=True)
    job_path = job_dir / "subtitle_job.json"
    job_path.write_text("invalid job is still removable", encoding="utf-8")
    nested = job_dir / "residue" / "temporary.txt"
    nested.parent.mkdir()
    nested.write_text("residue", encoding="utf-8")

    returned_path, removed = remove_subtitle_job.remove_subtitle_job(job_path.resolve())

    assert returned_path == job_path.resolve()
    assert removed is True
    assert not job_dir.exists()


def test_remove_is_idempotent_when_job_is_absent(results_dir: Path) -> None:
    job_path = (results_dir / ("b" * 64) / "subtitle_job.json").resolve()

    returned_path, removed = remove_subtitle_job.remove_subtitle_job(job_path)

    assert returned_path == job_path
    assert removed is False


@pytest.mark.parametrize(
    "job_path",
    [
        Path("relative/subtitle_job.json"),
        Path(__file__).resolve(),
    ],
)
def test_remove_rejects_unsafe_paths(results_dir: Path, job_path: Path) -> None:
    with pytest.raises(SubtitleJobError):
        remove_subtitle_job.remove_subtitle_job(job_path)


def test_remove_rejects_non_job_directory_name(results_dir: Path) -> None:
    job_path = (results_dir / "not-a-job-id" / "subtitle_job.json").resolve()

    with pytest.raises(SubtitleJobError):
        remove_subtitle_job.remove_subtitle_job(job_path)
