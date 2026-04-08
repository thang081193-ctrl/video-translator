"""Process-wide GPU/CPU state tracking — sticky fallback flag.

Once any model loader (Whisper, Demucs, EasyOCR) hits an OOM or driver
failure on CUDA, this module's `_force_cpu` flag flips to True. All
subsequent loaders read `should_use_gpu()` and skip the CUDA path
entirely until the process restarts. This avoids paying the GPU init
cost (and watching it fail) on every job after the first failure.

Design constraints:
- NO `import torch` at module level — keeps the unit tests fast and
  importable on CPU-only dev boxes.
- Thread-safe via a single lock; reads are cheap (tiny critical section).
- `cfg.gpu.force_cpu` (env `FORCE_CPU=1`) acts as a hard override —
  always returns False from `should_use_gpu()` regardless of state.
"""

from __future__ import annotations

import threading

from pipeline.config import cfg
from pipeline.logger import get_logger

log = get_logger("GPU")

_force_cpu: bool = False
_unavailable_reason: str | None = None
_lock = threading.Lock()


def should_use_gpu() -> bool:
    """Return True if loaders should attempt CUDA, False otherwise.

    Returns False if:
    - `cfg.gpu.force_cpu` is True (env FORCE_CPU=1) — hard override
    - `mark_gpu_unavailable()` was called earlier in this process

    Otherwise returns True, letting the loader try CUDA on its own.
    Note: this does NOT probe `torch.cuda.is_available()` — that's the
    loader's job. We only track the sticky failure state.
    """
    if cfg.gpu.force_cpu:
        return False
    with _lock:
        return not _force_cpu


def mark_gpu_unavailable(reason: str) -> None:
    """Permanently disable GPU for the rest of this process.

    Idempotent: subsequent calls are no-ops (with a single warning log
    on the first call so the operator sees the trigger reason).
    """
    global _force_cpu, _unavailable_reason
    with _lock:
        if _force_cpu:
            return  # already disabled, no-op
        _force_cpu = True
        _unavailable_reason = reason
    log.warning(f"GPU disabled for the rest of this process: {reason}")


def get_unavailable_reason() -> str | None:
    """Return the reason GPU was disabled, or None if still available."""
    with _lock:
        return _unavailable_reason


def reset_for_tests() -> None:
    """Reset sticky state. ONLY for unit tests."""
    global _force_cpu, _unavailable_reason
    with _lock:
        _force_cpu = False
        _unavailable_reason = None


def empty_cuda_cache() -> None:
    """Call torch.cuda.empty_cache() if torch is importable. Never raises.

    Safe to call from any thread, on CPU-only machines, or after the
    pipeline crashes — the goal is to release fragmented VRAM between
    jobs without coupling the rest of the codebase to torch.
    """
    try:
        import torch  # type: ignore
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        # Swallow all errors — empty_cache is best-effort housekeeping.
        pass
