import tomllib
from pathlib import Path

import pytest

from scripts.process_logging import SetupError
from scripts.setup.environment import (
    SetupPaths,
    assert_python_312,
    configure_environment,
    create_log_path,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_UV_INDEX = "https://pypi.tuna.tsinghua.edu.cn/simple"


def test_configure_environment_uses_project_local_caches(
    workspace_tmp_path: Path,
) -> None:
    paths = SetupPaths.from_root(workspace_tmp_path)
    environ: dict[str, str] = {}

    configure_environment(paths, environ)

    assert environ["UV_CACHE_DIR"] == str(paths.uv_cache_dir)
    assert paths.logs_dir.is_dir()
    assert paths.results_dir.is_dir()


def test_configure_environment_preserves_explicit_uv_cache(
    workspace_tmp_path: Path,
) -> None:
    paths = SetupPaths.from_root(workspace_tmp_path)
    custom_uv_cache = workspace_tmp_path / "custom-uv"
    environ = {"UV_CACHE_DIR": str(custom_uv_cache)}

    configure_environment(paths, environ)

    assert environ["UV_CACHE_DIR"] == str(custom_uv_cache)


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


def test_pyproject_uses_only_the_default_uv_index() -> None:
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text("utf-8"))

    indexes = pyproject["tool"]["uv"]["index"]
    assert indexes == [
        {
            "name": "tsinghua-pypi",
            "url": DEFAULT_UV_INDEX,
            "default": True,
        }
    ]
