from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Mapping, Sequence

from scripts.io_utils import write_json_atomic
from scripts.model_artifacts import model_has_required_files, model_has_weights
from scripts.model_identity import IDENTITY_MARKER, installed_revision_matches
from scripts.process_logging import ProcessLogger, SetupError

DOWNLOAD_SCRIPT = (
    "import sys; "
    "from huggingface_hub import snapshot_download; "
    "snapshot_download(repo_id=sys.argv[1], revision=sys.argv[2], local_dir=sys.argv[3])"
)


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
    if ready(model_dir, weight_patterns) and installed_revision_matches(
        model_dir, repo_id, revision
    ):
        return False

    model_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary_dir = Path(
        tempfile.mkdtemp(prefix=f".{model_dir.name}.", dir=model_dir.parent)
    )
    backup_dir = model_dir.with_name(f".{model_dir.name}.old")
    try:
        logger.run(
            [python, "-c", DOWNLOAD_SCRIPT, repo_id, revision, temporary_dir],
            f"Download model {repo_id}@{revision}",
            env=env,
        )
        if not ready(temporary_dir, weight_patterns):
            raise SetupError(
                f"Downloaded model is missing required weights: {model_dir}"
            )
        write_json_atomic(
            temporary_dir / IDENTITY_MARKER,
            {"repo": repo_id, "revision": revision},
        )
        if backup_dir.exists():
            shutil.rmtree(backup_dir)
        if model_dir.exists():
            model_dir.replace(backup_dir)
        try:
            temporary_dir.replace(model_dir)
        except Exception:
            if backup_dir.exists() and not model_dir.exists():
                backup_dir.replace(model_dir)
            raise
        if backup_dir.exists():
            shutil.rmtree(backup_dir)
    except Exception:
        if backup_dir.exists() and not model_dir.exists():
            backup_dir.replace(model_dir)
        raise
    finally:
        if temporary_dir.exists():
            shutil.rmtree(temporary_dir)
    return True
