from __future__ import annotations

from pathlib import Path
from typing import Mapping, Sequence

from scripts.io_utils import read_json, write_json_atomic
from scripts.model_artifacts import model_has_required_files, model_has_weights
from scripts.process_logging import ProcessLogger, SetupError

DOWNLOAD_SCRIPT = (
    "import sys; "
    "from huggingface_hub import snapshot_download; "
    "snapshot_download(repo_id=sys.argv[1], revision=sys.argv[2], local_dir=sys.argv[3])"
)
IDENTITY_MARKER = ".model_identity.json"


def _installed_revision_matches(
    model_dir: Path,
    repo_id: str,
    revision: str,
) -> bool:
    try:
        payload = read_json(model_dir / IDENTITY_MARKER)
    except (OSError, UnicodeError, ValueError):
        return False
    return payload == {"repo": repo_id, "revision": revision}


def download_model(
    python: Path,
    repo_id: str,
    revision: str,
    model_dir: Path,
    weight_patterns: Sequence[str],
    logger: ProcessLogger,
    env: Mapping[str, str],
    *,
    require_all: bool = False,
) -> bool:
    ready = model_has_required_files if require_all else model_has_weights
    if ready(model_dir, weight_patterns) and _installed_revision_matches(
        model_dir, repo_id, revision
    ):
        return False

    model_dir.parent.mkdir(parents=True, exist_ok=True)
    logger.run(
        [python, "-c", DOWNLOAD_SCRIPT, repo_id, revision, model_dir],
        f"Download model {repo_id}@{revision}",
        env=env,
    )
    if not ready(model_dir, weight_patterns):
        raise SetupError(f"Downloaded model is missing required weights: {model_dir}")
    write_json_atomic(
        model_dir / IDENTITY_MARKER,
        {"repo": repo_id, "revision": revision},
    )
    return True
