from types import SimpleNamespace

import pytest

from scripts import io_utils


def test_staging_collision_does_not_reuse_existing_directory(tmp_path, monkeypatch):
    occupied = tmp_path / ".publication-occupied"
    occupied.mkdir()
    marker = occupied / "keep.txt"
    marker.write_text("keep")
    names = iter(["occupied", "new"])
    monkeypatch.setattr(io_utils, "uuid4", lambda: SimpleNamespace(hex=next(names)))
    with io_utils.staging_directory(tmp_path, prefix=".publication-") as staging:
        assert staging != occupied
        (staging / "manifest.json").write_text("staged")
    assert not staging.exists()
    assert marker.read_text() == "keep"


def test_staging_failure_cleans_only_its_own_directory(tmp_path):
    marker = tmp_path / "keep.txt"
    marker.write_text("keep")
    with pytest.raises(RuntimeError, match="failed"):
        with io_utils.staging_directory(tmp_path, prefix=".publication-") as staging:
            (staging / "partial.json").write_text("partial")
            raise RuntimeError("failed")
    assert not staging.exists()
    assert marker.read_text() == "keep"
