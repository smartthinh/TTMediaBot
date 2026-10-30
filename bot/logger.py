from __future__ import annotations
from enum import Flag
import logging
from logging.handlers import RotatingFileHandler
import os
import sys
import threading
from typing import TYPE_CHECKING, Any, List, Optional, Tuple, Union

from bot import app_vars

if TYPE_CHECKING:
    from bot import Bot


DEFAULT_LOG_FORMAT = "%(levelname)s [%(asctime)s]: %(message)s in %(threadName)s file: %(filename)s line %(lineno)d function %(funcName)s"


class InMemoryLogHandler(logging.Handler):
    """Buffers log records in memory for real-time inspection and export via GUI."""

    def __init__(self, capacity: int = 10000):
        super().__init__()
        self.capacity = capacity
        self.records: List[str] = []
        self._lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            with self._lock:
                self.records.append(msg)
                if len(self.records) > self.capacity:
                    self.records.pop(0)
        except Exception:
            self.handleError(record)

    def get_logs(self) -> str:
        with self._lock:
            return "\n".join(self.records)

    def clear(self) -> None:
        with self._lock:
            self.records.clear()


in_memory_log_handler = InMemoryLogHandler()
in_memory_log_handler.setFormatter(logging.Formatter(DEFAULT_LOG_FORMAT))


def setup_gui_logging() -> None:
    """Ensures in-memory log handler and persistent file handler are attached to the root logger."""
    root = logging.getLogger()
    if root.level == logging.NOTSET or root.level > logging.INFO:
        root.setLevel(logging.INFO)
    if in_memory_log_handler not in root.handlers:
        root.addHandler(in_memory_log_handler)

    gui_log_path = os.path.join(app_vars.logs_dir, "TTMediaBot_gui.log")
    has_file_handler = any(
        isinstance(h, logging.FileHandler) and getattr(h, "baseFilename", "") == os.path.abspath(gui_log_path)
        for h in root.handlers
    )
    if not has_file_handler:
        try:
            os.makedirs(app_vars.logs_dir, exist_ok=True)
            gui_file_handler = RotatingFileHandler(
                filename=gui_log_path,
                mode="a",
                maxBytes=5 * 1024 * 1024,
                backupCount=2,
                encoding="UTF-8",
            )
            gui_file_handler.setFormatter(logging.Formatter(DEFAULT_LOG_FORMAT))
            gui_file_handler.setLevel(logging.INFO)
            root.addHandler(gui_file_handler)
        except Exception:
            pass


# Automatically attach in-memory handler on import so all initial events are captured
setup_gui_logging()


class Mode(Flag):
    STDOUT = 1
    FILE = 2
    STDOUT_AND_FILE = STDOUT | FILE


def initialize_logger(bot: Bot) -> None:
    config = bot.config.logger
    logging.addLevelName(5, "PLAYER_DEBUG")
    level = logging.getLevelName(config.level)
    formatter = logging.Formatter(config.format)
    in_memory_log_handler.setFormatter(formatter)
    handlers: List[Any] = [in_memory_log_handler]
    try:
        mode = (
            Mode(config.mode)
            if isinstance(config.mode, int)
            else Mode.__members__[config.mode]
        )
    except KeyError:
        sys.exit("Invalid log mode name")
    if mode & Mode.FILE == Mode.FILE:
        if bot.log_file_name:
            file_name = bot.log_file_name
        else:
            file_name = config.file_name
        if os.path.isabs(file_name):
            file = file_name
        elif os.path.isdir(os.path.join(*os.path.split(file_name)[0:-1])):
            file = file_name
        else:
            file = os.path.join(app_vars.logs_dir, file_name)
        os.makedirs(os.path.dirname(os.path.abspath(file)), exist_ok=True)
        rotating_file_handler = RotatingFileHandler(
            filename=file,
            mode="a",
            maxBytes=config.max_file_size * 1024,
            backupCount=config.backup_count,
            encoding="UTF-8",
        )
        rotating_file_handler.setFormatter(formatter)
        rotating_file_handler.setLevel(level)
        handlers.append(rotating_file_handler)
    if mode & Mode.STDOUT == Mode.STDOUT:
        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setFormatter(formatter)
        stream_handler.setLevel(level)
        handlers.append(stream_handler)

    # Attach handlers to root logger directly so they work even if basicConfig was previously initialized
    root = logging.getLogger()
    root.setLevel(level)
    for h in handlers:
        if h not in root.handlers:
            root.addHandler(h)

    logging.basicConfig(level=level, format=config.format, handlers=handlers)


def get_session_logs(
    server_log_path: Optional[str] = None,
    max_bytes: int = 150_000,
    max_lines: int = 1000,
    return_path: bool = False,
) -> Union[str, Tuple[str, Optional[str]]]:
    """
    Returns recent logs directly from disk log files for fast, responsive inspection.
    Bounds the read size to the most recent entries to prevent UI freezing and memory pressure.
    Falls back to in-memory logs if no disk logs exist yet.
    """
    candidate_files = []
    if server_log_path and os.path.isfile(server_log_path):
        candidate_files.append(server_log_path)
    if os.path.isdir(app_vars.logs_dir):
        try:
            files = [
                os.path.join(app_vars.logs_dir, f)
                for f in os.listdir(app_vars.logs_dir)
                if f.endswith(".log")
            ]
            files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
            for f in files:
                if f not in candidate_files:
                    candidate_files.append(f)
        except Exception:
            pass

    for fpath in candidate_files:
        try:
            if not os.path.isfile(fpath):
                continue
            fsize = os.path.getsize(fpath)
            if fsize == 0:
                continue

            is_truncated = fsize > max_bytes
            with open(fpath, "rb") as f:
                if is_truncated:
                    f.seek(fsize - max_bytes)
                    raw = f.read(max_bytes)
                    text = raw.decode("utf-8", errors="replace")
                    lines = text.splitlines()
                    if len(lines) > 1:
                        lines = lines[1:]  # discard potential incomplete line
                else:
                    raw = f.read()
                    text = raw.decode("utf-8", errors="replace")
                    lines = text.splitlines()

                if max_lines and len(lines) > max_lines:
                    lines = lines[-max_lines:]
                    is_truncated = True

                content = "\n".join(lines).strip()
                if is_truncated:
                    def _fmt_size(n: int) -> str:
                        val = float(n)
                        for u in ["B", "KB", "MB", "GB"]:
                            if val < 1024.0:
                                return f"{val:.1f} {u}"
                            val /= 1024.0
                        return f"{val:.1f} TB"
                    banner = f"--- [Showing latest {len(lines)} log entries ({_fmt_size(fsize)} total). Click 'Open Log File' or 'Save Log...' for complete logs] ---\n\n"
                    content = banner + content

                if content:
                    return (content, fpath) if return_path else content
        except Exception:
            continue

    # Fallback to in-memory session logs if disk logs are empty or not created yet
    mem_logs = in_memory_log_handler.get_logs().strip()
    if max_lines and mem_logs:
        mem_lines = mem_logs.splitlines()
        if len(mem_lines) > max_lines:
            mem_logs = "\n".join(mem_lines[-max_lines:])
    return (mem_logs, None) if return_path else mem_logs
