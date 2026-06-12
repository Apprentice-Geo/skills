from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence


class SetupError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProcessResult:
    returncode: int
    output: str


class ProcessLogger:
    def __init__(self, log_path: Path) -> None:
        self.log_path = log_path
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def step(self, current: int, total: int, message: str) -> None:
        print(f"[{current}/{total}] {message}")

    def run(
        self,
        command: Sequence[str | os.PathLike[str]],
        description: str,
        *,
        env: Mapping[str, str] | None = None,
        cwd: Path | None = None,
        check: bool = True,
    ) -> ProcessResult:
        command_text = [str(part) for part in command]
        completed = subprocess.run(
            command_text,
            cwd=cwd,
            env=dict(env) if env is not None else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        output = completed.stdout or ""
        with self.log_path.open("a", encoding="utf-8") as log_file:
            log_file.write(f"$ {subprocess.list2cmdline(command_text)}\n")
            log_file.write(output)
            if output and not output.endswith("\n"):
                log_file.write("\n")
            log_file.write(f"[exit {completed.returncode}]\n\n")

        result = ProcessResult(completed.returncode, output)
        if check and completed.returncode != 0:
            print(f"Error: {description} failed.", file=sys.stderr)
            if output:
                print(output.rstrip(), file=sys.stderr)
            print(f"Full log: {self.log_path}", file=sys.stderr)
            raise SetupError(f"{description} failed. See {self.log_path}")
        return result
