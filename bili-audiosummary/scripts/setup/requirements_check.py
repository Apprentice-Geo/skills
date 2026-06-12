from __future__ import annotations

import importlib.metadata
import json
import sys
from pathlib import Path

from pip._vendor.packaging.requirements import InvalidRequirement, Requirement


def check_requirements(requirements_path: Path) -> list[str]:
    issues: list[str] = []
    for raw_line in requirements_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if " #" in line:
            line = line.split(" #", 1)[0].rstrip()

        try:
            requirement = Requirement(line)
        except InvalidRequirement as exc:
            issues.append(f"{line}: invalid requirement ({exc})")
            continue

        if requirement.marker and not requirement.marker.evaluate():
            continue

        try:
            installed_version = importlib.metadata.version(requirement.name)
        except importlib.metadata.PackageNotFoundError:
            issues.append(f"{line}: not installed")
            continue

        if requirement.specifier and not requirement.specifier.contains(
            installed_version,
            prereleases=True,
        ):
            issues.append(f"{line}: installed {installed_version}")

    return issues


def main() -> int:
    requirements_path = Path(sys.argv[1])
    print(json.dumps(check_requirements(requirements_path)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
