import re
import shutil
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

repo_root_text = str(REPO_ROOT)
if repo_root_text not in sys.path:
    sys.path.insert(0, repo_root_text)


@pytest.fixture
def workspace_tmp_path(request: pytest.FixtureRequest) -> Path:
    root = REPO_ROOT / "tmp" / "workspace"
    root.mkdir(parents=True, exist_ok=True)
    name = re.sub(r"[^0-9A-Za-z_.-]+", "_", request.node.name)
    path = root / name
    if path.exists():
        shutil.rmtree(path)
    path.mkdir()
    return path
