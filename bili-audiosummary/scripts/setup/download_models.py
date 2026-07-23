from __future__ import annotations

from pathlib import Path
from typing import Mapping, Sequence

from scripts.model_artifacts import model_has_required_files, model_has_weights
from scripts.process_logging import ProcessLogger, SetupError

DOWNLOAD_SCRIPT = (
    "import sys; "
    "from huggingface_hub import snapshot_download; "
    "snapshot_download(repo_id=sys.argv[1], local_dir=sys.argv[2])"
)


def download_model(
    python: Path,
    repo_id: str,
    model_dir: Path,
    weight_patterns: Sequence[str],
    logger: ProcessLogger,
    env: Mapping[str, str],
    *,
    require_all: bool = False,
) -> bool:
    ready = model_has_required_files if require_all else model_has_weights
    if ready(model_dir, weight_patterns):
        return False

    model_dir.parent.mkdir(parents=True, exist_ok=True)
    logger.run(
        [python, "-c", DOWNLOAD_SCRIPT, repo_id, model_dir],
        f"Download model {repo_id}",
        env=env,
    )
    if not ready(model_dir, weight_patterns):
        raise SetupError(f"Downloaded model is missing required weights: {model_dir}")
    return True
