from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import warnings
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from functools import wraps
from pathlib import Path
from typing import Callable, Iterator, Mapping, Sequence

LOGGER_NAME = "audio_transcribe"
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
FILTERED_MESSAGE_PREFIXES = {
    "speechbrain.dataio.encoder": "CategoricalEncoder.expect_len was never called",
    "transformers.generation.utils": "Setting `pad_token_id` to `eos_token_id`",
}


class TerminalFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return bool(getattr(record, "terminal", False))


class MessagePrefixFilter(logging.Filter):
    def __init__(self, prefix: str) -> None:
        super().__init__()
        self.prefix = prefix

    def filter(self, record: logging.LogRecord) -> bool:
        return not record.getMessage().startswith(self.prefix)


@contextmanager
def filtered_log_messages() -> Iterator[None]:
    installed: list[tuple[logging.Logger, MessagePrefixFilter]] = []
    try:
        for logger_name, prefix in FILTERED_MESSAGE_PREFIXES.items():
            external_logger = logging.getLogger(logger_name)
            message_filter = MessagePrefixFilter(prefix)
            external_logger.addFilter(message_filter)
            installed.append((external_logger, message_filter))
        yield
    finally:
        for external_logger, message_filter in reversed(installed):
            external_logger.removeFilter(message_filter)


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


def terminal_info(logger: logging.Logger, message: str, *args: object) -> None:
    logger.info(message, *args, extra={"terminal": True})


def create_timestamped_log_path(logs_dir: Path, prefix: str) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    return logs_dir / f"{prefix}-{timestamp}.log"


class LoggingSession:
    _current: LoggingSession | None = None

    def __init__(self, log_path: Path, *, mode: str = "a") -> None:
        self.log_path = Path(log_path).resolve()
        self._mode = mode
        self._logger = get_logger()
        self._file_handler: logging.FileHandler | None = None
        self._stream_handler: logging.StreamHandler | None = None
        self._captured_loggers: dict[str, CapturedLoggerState] = {}
        self._previous_showwarning = None
        self._previous_logger_state = None
        self._started = False

    @classmethod
    def current(cls) -> LoggingSession | None:
        return cls._current

    def _make_file_handler(self, path: Path) -> logging.FileHandler:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(path, mode=self._mode, encoding="utf-8")
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(logging.Formatter(LOG_FORMAT))
        return handler

    def start(self) -> LoggingSession:
        if self._started:
            return self

        previous = LoggingSession._current
        try:
            file_handler = self._make_file_handler(self.log_path)
        except OSError as exc:
            raise FilesystemFailure(
                "start log (log not created)", self.log_path, exc
            ) from exc

        if previous is not None and previous is not self:
            previous.close()

        self._previous_logger_state = CapturedLoggerState(
            handlers=list(self._logger.handlers),
            level=self._logger.level,
            propagate=self._logger.propagate,
            disabled=self._logger.disabled,
            forwarding_handler=ForwardingHandler(self._logger),
        )
        self._previous_logger_state.forwarding_handler.close()
        self._file_handler = file_handler
        self._started = True
        try:
            self._logger.setLevel(logging.DEBUG)
            self._logger.propagate = False
            self._stream_handler = logging.StreamHandler(sys.stdout)
            self._stream_handler.setLevel(logging.DEBUG)
            self._stream_handler.setFormatter(logging.Formatter("%(message)s"))
            # Filter 过滤器，只输出 extra={"terminal": True} 的日志记录到终端
            self._stream_handler.addFilter(TerminalFilter())
            self._logger.handlers[:] = [self._file_handler, self._stream_handler]
            LoggingSession._current = self
            self.capture_logger("py.warnings")
            self._previous_showwarning = warnings.showwarning
            warnings.showwarning = self._showwarning
        except BaseException:
            self.close()
            raise
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
        warning_text = warnings.formatwarning(
            message,
            category,
            filename,
            lineno,
            line,
        ).rstrip()
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
            # 把缓冲区里的日志强制写入磁盘，避免还有日志停留在内存里没落盘
            file_handler.flush()
            # 关闭文件句柄。因为 FileHandler 内部一直持有目标日志文件，如果不关闭，文件可能仍然被占用
            file_handler.close()
            # 把这个 FileHandler 从 logger 上移除。否则 logger 后续还会尝试往这个已经关闭的 handler 写日志。
            self._logger.removeHandler(file_handler)
            self._file_handler = None

        try:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source_path), str(target_path))
        except OSError:
            try:
                self._file_handler = self._make_file_handler(source_path)
            except OSError as exc:
                raise FilesystemFailure(
                    "reopen original log", source_path, exc
                ) from exc
            self._logger.addHandler(self._file_handler)
            get_logger(__name__).warning(
                "Unable to move log to %s; continuing with %s",
                target_path,
                source_path,
                exc_info=True,
            )
            return source_path

        self.log_path = target_path
        try:
            self._file_handler = self._make_file_handler(target_path)
        except OSError as exc:
            raise FilesystemFailure("reopen moved log", target_path, exc) from exc
        self._logger.addHandler(self._file_handler)
        return target_path

    def report_failure(self, exc: BaseException) -> None:
        if self._file_handler is None or self._file_handler.stream is None:
            print(f"Error: {exc}\nLog unavailable: {self.log_path}", file=sys.stderr)
            return
        get_logger(__name__).error("Process failed: %s", exc, exc_info=exc)
        print(f"Error: {exc}\nFull log: {self.log_path}", file=sys.stderr)

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
        if self._previous_logger_state is not None:
            state = self._previous_logger_state
            self._logger.handlers[:] = state.handlers
            self._logger.setLevel(state.level)
            self._logger.propagate = state.propagate
            self._logger.disabled = state.disabled
            self._previous_logger_state = None
        self._file_handler = None
        self._stream_handler = None
        self._started = False
        if LoggingSession._current is self:
            LoggingSession._current = None

    def __enter__(self) -> LoggingSession:
        return self.start()

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        if _exc is not None and self._file_handler is not None:
            get_logger(__name__).error("Process failed: %s", _exc, exc_info=_exc)
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
                "$ %s\n[failed to start: %s]",
                subprocess.list2cmdline(command_text),
                exc,
            )
            raise SetupError(
                f"{description} could not start. See {self.log_path}"
            ) from exc
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


class FilesystemFailure(OSError):
    def __init__(self, operation: str, path: Path, cause: OSError) -> None:
        super().__init__(
            cause.errno,
            f"{operation}: {type(cause).__name__}: {cause}",
            str(path.resolve()),
        )

    def __str__(self) -> str:
        return f"{self.strerror} (path: {self.filename})"


def filesystem_cli(function: Callable[..., int]) -> Callable[..., int]:
    @wraps(function)
    def guarded(*args, **kwargs) -> int:
        try:
            return function(*args, **kwargs)
        except ReportPublishError:
            return 1
        except OSError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1

    return guarded


def publish_check_report(report: dict, path: Path, log_path: Path) -> None:
    report["logs"] = {"report": str(path), "log": str(log_path)}
    temporary = path.with_suffix(".tmp")
    try:
        temporary.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(temporary, path)
    except OSError as exc:
        session = LoggingSession.current()
        failure = FilesystemFailure("publish check report", path, exc)
        if session is not None:
            session.report_failure(failure)
        raise ReportPublishError(failure) from exc
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


class ReportPublishError(RuntimeError):
    pass
