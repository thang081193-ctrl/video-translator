"""Shared utilities — TempDir context manager, disk space check, retry decorator."""

from __future__ import annotations

import functools
import os
import shutil
import time
from contextlib import contextmanager

from pipeline.logger import get_logger

log = get_logger("Utils")


@contextmanager
def temp_dir(name: str, base_dir: str = "."):
    """Context manager for temporary directories with guaranteed cleanup.

    Usage:
        with temp_dir("tts", base_dir=job_dir) as tmp:
            # work with tmp (str path)
            # auto-cleanup on exit, even on exception
    """
    path = os.path.join(base_dir, f"_{name}_temp")
    os.makedirs(path, exist_ok=True)
    try:
        yield path
    finally:
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)


def check_disk_space(path: str, min_bytes: int = 500 * 1024 * 1024) -> bool:
    """Check if there's enough free disk space.

    Args:
        path: Path to check (uses the filesystem of this path).
        min_bytes: Minimum required free space (default 500MB).

    Returns:
        True if sufficient space, False otherwise.
    """
    try:
        usage = shutil.disk_usage(os.path.dirname(os.path.abspath(path)))
        if usage.free < min_bytes:
            log.warning(
                f"Low disk space: {usage.free // (1024*1024)}MB free, "
                f"need {min_bytes // (1024*1024)}MB"
            )
            return False
        return True
    except OSError:
        return True  # Can't check — assume OK


def retry(max_attempts: int = 3, base_delay: float = 1.0, max_delay: float = 30.0):
    """Decorator for retrying functions with exponential backoff.

    Only retries on TransientError. Other exceptions propagate immediately.

    Usage:
        @retry(max_attempts=3)
        def call_api():
            ...
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            from pipeline.errors import TransientError
            last_error = None
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except TransientError as e:
                    last_error = e
                    delay = min(base_delay * (2 ** attempt), max_delay)
                    if hasattr(e, 'retry_after') and e.retry_after > 0:
                        delay = e.retry_after
                    if attempt < max_attempts - 1:
                        log.warning(f"Retry {attempt + 1}/{max_attempts} after {delay:.1f}s: {e}")
                        time.sleep(delay)
            raise last_error
        return wrapper
    return decorator
