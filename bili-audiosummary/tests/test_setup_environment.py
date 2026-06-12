import sys
from pathlib import Path

import pytest

from scripts.setup.environment import (
    SetupPaths,
    assert_python_312,
    configure_environment,
    create_log_path,
    ensure_virtual_environment,
)
from scripts.setup.process_logging import ProcessLogger, SetupError


def test_configure_environment_uses_project_local_caches(
    workspace_tmp_path: Path,
) -> None:
    paths = SetupPaths.from_root(workspace_tmp_path)
    environ = {
        "PIP_INDEX_URL": "https://example.invalid/simple",
        "HF_ENDPOINT": "https://example.invalid/hf",
    }

    configure_environment(paths, environ)

    assert environ["UV_CACHE_DIR"] == str(paths.uv_cache_dir)
    assert environ["HF_HOME"] == str(paths.hf_home)
    assert environ["HUGGINGFACE_HUB_CACHE"] == str(paths.hf_hub_cache)
    assert environ["PIP_INDEX_URL"] == "https://example.invalid/simple"
    assert environ["HF_ENDPOINT"] == "https://example.invalid/hf"
    assert paths.logs_dir.is_dir()
    assert paths.models_dir.is_dir()
    assert paths.results_dir.is_dir()


def test_configure_environment_derives_hub_cache_from_explicit_hf_home(
    workspace_tmp_path: Path,
) -> None:
    paths = SetupPaths.from_root(workspace_tmp_path)
    custom_hf_home = workspace_tmp_path / "custom-hf"
    environ = {"HF_HOME": str(custom_hf_home)}

    configure_environment(paths, environ)

    assert environ["HF_HOME"] == str(custom_hf_home)
    assert environ["HUGGINGFACE_HUB_CACHE"] == str(custom_hf_home / "hub")


def test_assert_python_312_rejects_other_minor_versions() -> None:
    with pytest.raises(SetupError, match="Python 3.12"):
        assert_python_312((3, 13, 0), "Existing .venv")


def test_create_log_path_uses_setup_timestamp_name(
    workspace_tmp_path: Path,
) -> None:
    paths = SetupPaths.from_root(workspace_tmp_path)

    log_path = create_log_path(paths)

    assert log_path.parent == paths.logs_dir
    assert log_path.name.startswith("setup-")
    assert log_path.name.endswith(".log")


def test_ensure_virtual_environment_creates_and_reuses_python_312(
    workspace_tmp_path: Path,
) -> None:
    paths = SetupPaths.from_root(workspace_tmp_path)
    configure_environment(paths, {})
    logger = ProcessLogger(paths.logs_dir / "setup.log")

    ensure_virtual_environment(paths, Path(sys.executable), logger)
    first_mtime = paths.venv_python.stat().st_mtime_ns
    ensure_virtual_environment(paths, Path(sys.executable), logger)

    assert paths.venv_python.is_file()
    assert paths.venv_python.stat().st_mtime_ns == first_mtime


def test_existing_non_312_virtual_environment_is_not_deleted(
    workspace_tmp_path: Path,
    monkeypatch,
) -> None:
    paths = SetupPaths.from_root(workspace_tmp_path)
    paths.venv_python.parent.mkdir(parents=True)
    paths.venv_python.write_bytes(b"existing")
    logger = ProcessLogger(workspace_tmp_path / "setup.log")
    monkeypatch.setattr(
        "scripts.setup.environment.read_python_version",
        lambda *_args, **_kwargs: (3, 11, 9),
    )

    with pytest.raises(SetupError, match="Existing .venv"):
        ensure_virtual_environment(paths, Path(sys.executable), logger)

    assert paths.venv_python.read_bytes() == b"existing"
