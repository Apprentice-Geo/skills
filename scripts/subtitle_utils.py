from pathlib import Path


def infer_subtitle_language(path: Path) -> str | None:
    parts = path.name.split(".")
    if len(parts) >= 3:
        return parts[-2]
    return None
