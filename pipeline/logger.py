"""Structured logging — replaces all print() statements in the pipeline."""

from __future__ import annotations

import logging
import os
import sys
import time
from contextlib import contextmanager


# ─── Formatters ──────────────────────────────────────────────────────────────

class CLIFormatter(logging.Formatter):
    """Human-readable format for CLI mode: [Module] message"""

    def format(self, record: logging.LogRecord) -> str:
        prefix = f"[{record.name}]" if record.name != "root" else ""
        # Include extra fields if present
        extras = ""
        for key in ("job_id", "step", "duration_ms", "batch", "provider"):
            val = getattr(record, key, None)
            if val is not None:
                extras += f" {key}={val}"
        msg = f"  {prefix} {record.getMessage()}{extras}"
        if record.exc_info and not record.exc_text:
            record.exc_text = self.formatException(record.exc_info)
        if record.exc_text:
            msg += f"\n{record.exc_text}"
        return msg


class WebFormatter(logging.Formatter):
    """Structured format for web/worker mode: timestamp [LEVEL] [Module] message"""

    def format(self, record: logging.LogRecord) -> str:
        ts = self.formatTime(record, "%Y-%m-%d %H:%M:%S")
        prefix = f"[{record.name}]" if record.name != "root" else ""
        extras = ""
        for key in ("job_id", "step", "duration_ms", "batch", "provider"):
            val = getattr(record, key, None)
            if val is not None:
                extras += f" {key}={val}"
        msg = f"{ts} [{record.levelname}] {prefix} {record.getMessage()}{extras}"
        if record.exc_info and not record.exc_text:
            record.exc_text = self.formatException(record.exc_info)
        if record.exc_text:
            msg += f"\n{record.exc_text}"
        return msg


# ─── Setup ───────────────────────────────────────────────────────────────────

_initialized = False


def setup_logging(mode: str = "cli", log_file: str | None = None):
    """
    Initialize logging for the application.

    Args:
        mode: "cli" for human-readable output, "web" for structured worker logs.
        log_file: Optional file path for web mode logging.
    """
    global _initialized
    if _initialized:
        return
    _initialized = True

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    # Remove default handlers
    root.handlers.clear()

    if mode == "web":
        formatter = WebFormatter()
        # File handler for worker logs
        if log_file:
            fh = logging.FileHandler(log_file, encoding="utf-8")
            fh.setFormatter(formatter)
            fh.setLevel(logging.DEBUG)
            root.addHandler(fh)
        # Console handler (INFO+)
        ch = logging.StreamHandler(sys.stderr)
        ch.setFormatter(formatter)
        ch.setLevel(logging.INFO)
        root.addHandler(ch)
    else:
        formatter = CLIFormatter()
        ch = logging.StreamHandler(sys.stdout)
        ch.setFormatter(formatter)
        ch.setLevel(logging.INFO)
        root.addHandler(ch)


def get_logger(name: str) -> logging.Logger:
    """
    Get a named logger for a pipeline module.

    Usage:
        from pipeline.logger import get_logger
        log = get_logger("Translate")
        log.info("Translating batch", extra={"batch": 3, "provider": "grok"})
    """
    return logging.getLogger(name)


# ─── Timing helper ───────────────────────────────────────────────────────────

@contextmanager
def log_duration(logger: logging.Logger, operation: str, **extra):
    """Context manager to log operation duration.

    Usage:
        with log_duration(log, "TTS generation", segments=10):
            generate_tts(...)
        # Logs: "TTS generation completed" with duration_ms=1234
    """
    start = time.monotonic()
    try:
        yield
    finally:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        logger.info(f"{operation} completed", extra={**extra, "duration_ms": elapsed_ms})
