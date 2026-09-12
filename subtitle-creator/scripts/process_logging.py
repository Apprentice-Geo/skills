from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import warnings
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Self

LOGGER_NAME = "subtitle_creator"
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
SKILL_DIR = Path(__file__).resolve().parents[1]


class ConsoleFilter(logging.Filter):
    def __init__(self, console: str) -> None:
        super().__init__()
        self.console = console

    def filter(self, record: logging.LogRecord) -> bool:
        return getattr(record, "console", None) == self.console


class ForwardingHandler(logging.Handler):
    def __init__(self, target: logging.Logger) -> None:
        super().__init__()
        self.target = target

    def emit(self, record: logging.LogRecord) -> None:
        self.target.handle(record)


@dataclass
class CapturedLoggerState:
    handlers: list[logging.Handler]
    level: int
    propagate: bool
    disabled: bool
    forwarding_handler: ForwardingHandler


def get_logger(name: str | None = None) -> logging.Logger:
    if not name:
        return logging.getLogger(LOGGER_NAME)
    return logging.getLogger(f"{LOGGER_NAME}.{name}")


def detail(
    logger: logging.Logger,
    message: str,
    *args: object,
    level: int = logging.INFO,
) -> None:
    logger.log(level, message, *args, extra={"console": None})


def status(logger: logging.Logger, message: str, *args: object) -> None:
    logger.info(message, *args, extra={"console": "stdout"})


def result(logger: logging.Logger, message: str, *args: object) -> None:
    logger.info(message, *args, extra={"console": "stdout"})


def warning(logger: logging.Logger, message: str, *args: object) -> None:
    logger.warning(message, *args, extra={"console": "stderr"})


def error(logger: logging.Logger, message: str, *args: object) -> None:
    logger.error(message, *args, extra={"console": "stderr"})


def exception(logger: logging.Logger, message: str, *args: object) -> None:
    logger.exception(message, *args, extra={"console": None})


def create_timestamped_log_path(logs_dir: Path, prefix: str) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
    return logs_dir / f"{prefix}-{timestamp}.log"


def create_workflow_log_path(prefix: str) -> Path:
    return create_timestamped_log_path(SKILL_DIR / ".cache" / "logs", prefix)


class LoggingSession:
    _current: LoggingSession | None = None

    def __init__(self, log_path: Path) -> None:
        self.log_path = Path(log_path)
        self._logger = get_logger()
        self._file_handler: logging.FileHandler | None = None
        self._stdout_handler: logging.StreamHandler | None = None
        self._stderr_handler: logging.StreamHandler | None = None
        self._captured_loggers: dict[str, CapturedLoggerState] = {}
        self._previous_showwarning = None
        self._previous_logger_state: CapturedLoggerState | None = None
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

    def start(self) -> Self:
        if self._started:
            return self
        previous = LoggingSession._current
        if previous is not None and previous is not self:
            raise RuntimeError(f"another logging session is already active: {previous.log_path}")

        self._previous_logger_state = CapturedLoggerState(
            handlers=list(self._logger.handlers),
            level=self._logger.level,
            propagate=self._logger.propagate,
            disabled=self._logger.disabled,
            forwarding_handler=ForwardingHandler(self._logger),
        )
        self._previous_logger_state.forwarding_handler.close()
        self._logger.setLevel(logging.DEBUG)
        self._logger.propagate = False
        self._file_handler = self._make_file_handler(self.log_path)
        self._stdout_handler = logging.StreamHandler(sys.stdout)
        self._stdout_handler.setLevel(logging.DEBUG)
        self._stdout_handler.setFormatter(logging.Formatter("%(message)s"))
        self._stdout_handler.addFilter(ConsoleFilter("stdout"))
        self._stderr_handler = logging.StreamHandler(sys.stderr)
        self._stderr_handler.setLevel(logging.DEBUG)
        self._stderr_handler.setFormatter(logging.Formatter("%(message)s"))
        self._stderr_handler.addFilter(ConsoleFilter("stderr"))
        self._logger.handlers[:] = [
            self._file_handler,
            self._stdout_handler,
            self._stderr_handler,
        ]
        self._started = True
        LoggingSession._current = self
        self.capture_logger("py.warnings")
        self._previous_showwarning = warnings.showwarning
        warnings.showwarning = self._showwarning
        return self

    def capture_logger(self, name: str) -> None:
        if name in self._captured_loggers:
            return
        external_logger = logging.getLogger(name)
        forwarding_handler = ForwardingHandler(self._logger)
        self._captured_loggers[name] = CapturedLoggerState(
            handlers=list(external_logger.handlers),
            level=external_logger.level,
            propagate=external_logger.propagate,
            disabled=external_logger.disabled,
            forwarding_handler=forwarding_handler,
        )
        external_logger.handlers[:] = [forwarding_handler]
        external_logger.propagate = False

    def _showwarning(
        self,
        message,
        category,
        filename,
        lineno,
        file=None,
        line=None,
    ) -> None:
        warning_text = warnings.formatwarning(message, category, filename, lineno, line).rstrip()
        logging.getLogger("py.warnings").warning(warning_text)

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
        logger = get_logger(__name__)
        logger.error("Process failed: %s", exc, exc_info=exc)
        error(logger, "Error: %s\nFull log: %s", exc, self.log_path)

    def close(self) -> None:
        if not self._started:
            return
        if self._previous_showwarning is not None:
            warnings.showwarning = self._previous_showwarning
            self._previous_showwarning = None
        for name, state in reversed(list(self._captured_loggers.items())):
            external_logger = logging.getLogger(name)
            external_logger.handlers[:] = state.handlers
            external_logger.setLevel(state.level)
            external_logger.propagate = state.propagate
            external_logger.disabled = state.disabled
            state.forwarding_handler.close()
        self._captured_loggers.clear()
        for handler in (self._file_handler, self._stdout_handler, self._stderr_handler):
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
        if self._previous_logger_state is not None:
            state = self._previous_logger_state
            self._logger.handlers[:] = state.handlers
            self._logger.setLevel(state.level)
            self._logger.propagate = state.propagate
            self._logger.disabled = state.disabled
            self._previous_logger_state = None
        self._file_handler = None
        self._stdout_handler = None
        self._stderr_handler = None
        self._started = False
        if LoggingSession._current is self:
            LoggingSession._current = None

    def __enter__(self) -> Self:
        return self.start()

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.close()


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
        status(self.logger, "[%d/%d] %s", current, total, message)

    def result(self, message: str, *args: object) -> None:
        result(self.logger, message, *args)

    def report_failure(self, exc: BaseException) -> None:
        self.session.report_failure(exc)

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
        try:
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
        except OSError as exc:
            self.logger.error(
                "$ %s\n[failed to start: %s]", subprocess.list2cmdline(command_text), exc
            )
            raise SetupError(f"{description} could not start.") from exc
        output = completed.stdout or ""
        self.logger.info(
            "$ %s\n%s[exit %d]",
            subprocess.list2cmdline(command_text),
            output.rstrip(),
            completed.returncode,
        )
        process_result = ProcessResult(completed.returncode, output)
        if check and completed.returncode != 0:
            raise SetupError(f"{description} failed.")
        return process_result

    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.close()
