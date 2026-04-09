"""GPU statistics collector for /api/gpu endpoint (P6.C).

Parses `nvidia-smi --query-gpu=...` CSV output with a 2-second timeout.
Never raises into the web layer — on any error returns
`{"available": False, "error": str(e)}` so the endpoint always returns
valid JSON. Falls back to torch.cuda memory stats if nvidia-smi is
unavailable but torch is.

Also reports the state of the P6.A in-process caches:
- Sticky GPU/CPU flag (from gpu_state)
- Whisper LRU cache contents (from transcribe)
- Demucs separator cache contents (from dub.separator)
- EasyOCR reader cache contents (from ocr.detector)

Callers:
- `web/routes.py::gpu_stats()` — GET /api/gpu
- `web/pipeline_runner.py::_vram_guard_check()` — VRAM guard
- `web/worker.py::create_job()` — ETA estimator

Design:
- `collect_gpu_stats()` is the single public entry point
- All helpers are `_`-prefixed and kept under 50 LOC
- `_parse_nvidia_smi()` does string → dict conversion defensively
- `_collect_cache_stats()` peeks at in-process caches read-only
"""

from __future__ import annotations

import subprocess
from typing import Any

from pipeline import gpu_state
from pipeline.config import cfg
from pipeline.logger import get_logger

log = get_logger("GPUStats")

# Query order MUST match _parse_nvidia_smi() expected fields
_NVIDIA_SMI_QUERY = "name,memory.total,memory.used,memory.free,utilization.gpu,temperature.gpu"


def collect_gpu_stats() -> dict[str, Any]:
    """Return a dict snapshot of current GPU state + cache state.

    Never raises. On error returns a dict with `available=False` and
    an `error` key explaining why. Web layer can always serialize the
    result to JSON.
    """
    stats: dict[str, Any] = {
        "available": False,
        "force_cpu_sticky": not gpu_state.should_use_gpu(),
        "unavailable_reason": gpu_state.get_unavailable_reason(),
    }

    # Try nvidia-smi first (authoritative source)
    nvidia_stats = _try_nvidia_smi()
    if nvidia_stats is not None:
        stats.update(nvidia_stats)
        stats["available"] = True
    else:
        # Fallback: torch.cuda memory stats (less detail, but better than nothing)
        torch_stats = _try_torch_cuda()
        if torch_stats is not None:
            stats.update(torch_stats)
            stats["available"] = True
        else:
            stats["error"] = "nvidia-smi and torch.cuda both unavailable"

    # Always include cache snapshot (cheap, no external calls)
    stats.update(_collect_cache_stats())

    return stats


def _try_nvidia_smi() -> dict[str, Any] | None:
    """Run nvidia-smi with 2s timeout. Return parsed dict or None on failure."""
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                f"--query-gpu={_NVIDIA_SMI_QUERY}",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True, text=True, timeout=cfg.ffmpeg.nvidia_smi_timeout,
            check=False,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None
        return _parse_nvidia_smi(result.stdout)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        log.debug(f"nvidia-smi unavailable: {e}")
        return None


def _parse_nvidia_smi(raw_csv: str) -> dict[str, Any] | None:
    """Parse CSV output from nvidia-smi into a dict.

    Expected format (noheader,nounits): `name, total_mb, used_mb, free_mb, util_pct, temp_c`
    Only returns data for the first GPU (index 0).
    """
    first_line = raw_csv.strip().splitlines()[0] if raw_csv.strip() else ""
    if not first_line:
        return None
    fields = [f.strip() for f in first_line.split(",")]
    if len(fields) != 6:
        log.debug(f"Unexpected nvidia-smi field count: {len(fields)} (expected 6)")
        return None
    try:
        return {
            "name": fields[0],
            "vram_total_mb": int(fields[1]),
            "vram_used_mb": int(fields[2]),
            "vram_free_mb": int(fields[3]),
            "utilization_pct": int(fields[4]),
            "temperature_c": int(fields[5]),
            "source": "nvidia-smi",
        }
    except (ValueError, IndexError) as e:
        log.debug(f"nvidia-smi parse failure: {e}")
        return None


def _try_torch_cuda() -> dict[str, Any] | None:
    """Fallback: use torch.cuda APIs. Less detail than nvidia-smi."""
    try:
        import torch  # type: ignore
        if not torch.cuda.is_available():
            return None
        idx = 0
        props = torch.cuda.get_device_properties(idx)
        total_mb = int(props.total_memory / (1024 * 1024))
        allocated_mb = int(torch.cuda.memory_allocated(idx) / (1024 * 1024))
        reserved_mb = int(torch.cuda.memory_reserved(idx) / (1024 * 1024))
        return {
            "name": props.name,
            "vram_total_mb": total_mb,
            "vram_used_mb": reserved_mb,  # use reserved (closer to actual)
            "vram_free_mb": total_mb - reserved_mb,
            "vram_allocated_mb": allocated_mb,
            "utilization_pct": None,  # torch doesn't expose this
            "temperature_c": None,
            "source": "torch.cuda",
        }
    except Exception as e:
        log.debug(f"torch.cuda fallback failed: {e}")
        return None


def _collect_cache_stats() -> dict[str, Any]:
    """Read-only peek at P6.A in-process caches. Fast, never raises."""
    cache_info: dict[str, Any] = {
        "whisper_cached_models": [],
        "ocr_cached_langs": [],
        "demucs_cached": [],
    }
    try:
        from pipeline.transcribe import _model_cache
        cache_info["whisper_cached_models"] = [
            {"model": k[0], "device": k[1], "compute_type": k[2]}
            for k in _model_cache.keys()
        ]
    except Exception:
        pass
    try:
        from pipeline.ocr.detector import _ocr_readers
        cache_info["ocr_cached_langs"] = list(_ocr_readers.keys())
    except Exception:
        pass
    try:
        from pipeline.dub.separator import _separator_cache
        cache_info["demucs_cached"] = list(_separator_cache.keys())
    except Exception:
        pass
    return cache_info
