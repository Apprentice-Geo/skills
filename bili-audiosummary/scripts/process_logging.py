from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Mapping, Sequence

if __name__ == "process_logging":
    sys.modules.setdefault("scripts.process_logging", sys.modules[__name__])
elif __name__ == "scripts.process_logging":
    sys.modules.setdefault("process_logging", sys.modules[__name__])


LOGGER_NAME = "bili_audiosummary"
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


class TerminalFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return bool(getattr(record, "terminal", False))


def get_logger(name: str | None = None) -> logging.Logger:
    if not name:
        return logging.getLogger(LOGGER_NAME)
    return logging.getLogger(f"{LOGGER_NAME}.{name}")


def terminal_info(logger: logging.Logger, message: str, *args: object) -> None:
    logger.info(message, *args, extra={"terminal": True})


def create_timestamped_log_path(logs_dir: Path, prefix: str) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    return logs_dir / f"{prefix}-{timestamp}.log"


class LoggingSession:
    _current: LoggingSession | None = None

    def __init__(self, log_path: Path) -> None:
        self.log_path = Path(log_path)
        self._logger = get_logger()
        self._file_handler: logging.FileHandler | None = None
        self._stream_handler: logging.StreamHandler | None = None
        self._started = False

    @classmethod
    def current(cls) -> LoggingSession | None:
        return cls._current

    def _make_file_handler(self, path: Path) -> logging.FileHandler:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(path, encoding="utf-8")
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(logging.Formatter(LOG_FORMAT))
        return handler

    def start(self) -> LoggingSession:
        if self._started:
            return self

        previous = LoggingSession._current
        if previous is not None and previous is not self:
            previous.close()

        self._logger.setLevel(logging.DEBUG)
        self._logger.propagate = False
        self._file_handler = self._make_file_handler(self.log_path)
        self._stream_handler = logging.StreamHandler(sys.stdout)
        self._stream_handler.setLevel(logging.DEBUG)
        self._stream_handler.setFormatter(logging.Formatter("%(message)s"))
        self._stream_handler.addFilter(TerminalFilter())
        self._logger.handlers[:] = [self._file_handler, self._stream_handler]
        self._started = True
        LoggingSession._current = self
        return self

    def move_to(self, directory: Path) -> Path:
        if not self._started:
            self.start()

        target_path = Path(directory) / self.log_path.name
        if target_path == self.log_path:
            return self.log_path

        source_path = self.log_path
        file_handler = self._file_handler
        if file_handler is not None:
            file_handler.flush()
            file_handler.close()
            self._logger.removeHandler(file_handler)

        try:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source_path), str(target_path))
        except OSError:
            self._file_handler = self._make_file_handler(source_path)
            self._logger.addHandler(self._file_handler)
            get_logger(__name__).warning(
                "Unable to move log to %s; continuing with %s",
                target_path,
                source_path,
                exc_info=True,
            )
            return source_path

        self.log_path = target_path
        self._file_handler = self._make_file_handler(target_path)
        self._logger.addHandler(self._file_handler)
        return target_path

    def report_failure(self, exc: BaseException) -> None:
        get_logger(__name__).exception("Process failed")
        traceback.print_exception(type(exc), exc, exc.__traceback__, file=sys.stderr)
        print(f"Full log: {self.log_path}", file=sys.stderr)

    def close(self) -> None:
        if not self._started:
            return
        for handler in (self._file_handler, self._stream_handler):
            if handler is None:
                continue
            try:
                handler.flush()
            except ValueError:
                pass
            try:
                handler.close()
            except ValueError:
                pass
            self._logger.removeHandler(handler)
        self._file_handler = None
        self._stream_handler = None
        self._started = False
        if LoggingSession._current is self:
            LoggingSession._current = None

    def __enter__(self) -> LoggingSession:
        return self.start()

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.close()


class YtDlpLogger:
    def __init__(self, logger: logging.Logger) -> None:
        self.logger = logger

    def debug(self, message: str) -> None:
        self.logger.debug(message)

    def info(self, message: str) -> None:
        self.logger.info(message)

    def warning(self, message: str) -> None:
        self.logger.warning(message)

    def error(self, message: str) -> None:
        self.logger.error(message)


class SetupError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProcessResult:
    returncode: int
    output: str


class ProcessLogger:
    def __init__(self, log_path: Path) -> None:
        self.session = LoggingSession(log_path).start()
        self.log_path = self.session.log_path
        self.logger = get_logger("setup")

    def step(self, current: int, total: int, message: str) -> None:
        terminal_info(self.logger, "[%d/%d] %s", current, total, message)

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
        self.logger.info(
            "$ %s\n%s[exit %d]",
            subprocess.list2cmdline(command_text),
            output.rstrip(),
            completed.returncode,
        )

        result = ProcessResult(completed.returncode, output)
        if check and completed.returncode != 0:
            print(f"Error: {description} failed.", file=sys.stderr)
            if output:
                print(output.rstrip(), file=sys.stderr)
            print(f"Full log: {self.log_path}", file=sys.stderr)
            raise SetupError(f"{description} failed. See {self.log_path}")
        return result

    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> ProcessLogger:
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.close()
