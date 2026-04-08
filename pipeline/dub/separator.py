"""Demucs source separation and audio duration utilities.

P6.A: in-process Separator cache (eliminates per-call subprocess overhead)
with subprocess fallback for older demucs versions that don't expose
`demucs.api.Separator`. Sticky GPU/CPU fallback via `pipeline.gpu_state`.
"""

import json
import os
import subprocess
import threading

from pipeline import gpu_state
from pipeline.config import cfg
from pipeline.errors import DegradedError
from pipeline.logger import get_logger

log = get_logger("Dub")


# ─── In-process Separator cache (P6.A) ───────────────────────────────────────
# Reuse the demucs model object across jobs in the same worker process.
# Cache key includes the device so a CPU-fallback Separator coexists with
# any leftover GPU instance.
_separator_cache: dict[str, "object"] = {}
_sep_lock = threading.Lock()
_torchaudio_patched = False


def _install_soundfile_save_patch() -> None:
    """Replace `torchaudio.save` with `soundfile.write` (one-time install).

    torchcodec is broken on Windows; soundfile is faster and works on all
    platforms. Idempotent — only patches the first time it's called.
    """
    global _torchaudio_patched
    if _torchaudio_patched:
        return
    try:
        import torchaudio
        import soundfile as sf

        def _sf_save(filepath, src, sample_rate, **kwargs):
            sf.write(str(filepath), src.cpu().numpy().T, sample_rate)

        torchaudio.save = _sf_save
        _torchaudio_patched = True
        log.info("Installed soundfile fallback for torchaudio.save")
    except ImportError:
        pass  # No torchaudio or soundfile — let demucs use whatever it has


def _get_separator(model: str):
    """Get or create a cached `demucs.api.Separator`.

    Returns None if `demucs.api` is not importable (older version);
    caller should fall back to subprocess `demucs.separate.main([...])`.
    Raises if Separator construction fails for any reason other than
    missing `demucs.api`.
    """
    try:
        from demucs.api import Separator  # type: ignore
    except ImportError:
        return None  # signal subprocess fallback

    device = "cuda" if gpu_state.should_use_gpu() else "cpu"
    key = f"{model}|{device}"
    with _sep_lock:
        if key in _separator_cache:
            log.info(f"Reusing cached Demucs separator: {key}")
            return _separator_cache[key]
        log.info(f"Loading Demucs separator: {key}")
        sep = Separator(model=model, device=device)
        _separator_cache[key] = sep
        return sep


def _evict_gpu_separator(model: str) -> None:
    """Drop the GPU-cached separator for `model` (called after OOM)."""
    with _sep_lock:
        gpu_key = f"{model}|cuda"
        if gpu_key in _separator_cache:
            del _separator_cache[gpu_key]
            gpu_state.empty_cuda_cache()
            log.info(f"Evicted Demucs GPU separator: {gpu_key}")


def _separate_via_subprocess(audio_path: str, demucs_dir: str, model: str) -> None:
    """Fallback path for older demucs versions without `demucs.api.Separator`.

    Mirrors the original P5 behavior: try GPU, fall back to CPU on OOM.
    """
    from demucs.separate import main as demucs_main
    try:
        demucs_main([
            "--two-stems", "vocals",
            "-n", model,
            "-o", demucs_dir,
            audio_path,
        ])
    except RuntimeError as e:
        log.warning(f"GPU OOM during Demucs subprocess ({e}), retrying on CPU...")
        gpu_state.mark_gpu_unavailable(f"Demucs OOM: {e}")
        gpu_state.empty_cuda_cache()
        demucs_main([
            "--two-stems", "vocals",
            "-n", model,
            "-d", "cpu",
            "-o", demucs_dir,
            audio_path,
        ])


def _separate_via_api(separator, audio_path: str, demucs_dir: str, model: str) -> None:
    """In-process separation via `demucs.api.Separator`.

    On OOM, evicts the GPU separator, marks GPU unavailable, and retries
    with a fresh CPU separator. Uses the high-level `separate_audio_file`
    API which writes stems to disk in the same layout as the subprocess
    CLI (so callers don't need to know which path was taken).
    """
    try:
        # Modern demucs API: separate_audio_file writes to disk via the
        # caller-provided output dir. Older versions: returns dict of stems.
        separator.separate_audio_file(audio_path, str(demucs_dir))
    except RuntimeError as e:
        if "out of memory" not in str(e).lower() and "OOM" not in str(e):
            raise
        log.warning(f"Demucs GPU OOM ({e}), evicting cache and retrying on CPU...")
        gpu_state.mark_gpu_unavailable(f"Demucs OOM: {e}")
        _evict_gpu_separator(model)
        cpu_sep = _get_separator(model)  # will load CPU now (sticky flag)
        if cpu_sep is None:
            raise  # demucs.api disappeared mid-call, give up
        cpu_sep.separate_audio_file(audio_path, str(demucs_dir))


def separate_audio(audio_path: str, demucs_dir: str, model: str = "htdemucs") -> dict[str, str]:
    """
    Separate audio into vocals and accompaniment using Demucs.

    `demucs_dir` must be an already-created directory owned by the caller
    (typically from a `temp_dir("demucs", ...)` context manager). Demucs
    writes its output tree under this path.

    Returns dict with paths: {"vocals": ..., "no_vocals": ...}. Note: these
    paths live inside `demucs_dir`; read them BEFORE the caller exits its
    context manager or the files will be cleaned up.

    Tries the in-process `demucs.api.Separator` cache first (fast — model
    stays loaded across jobs). Falls back to subprocess `demucs.separate.main`
    if the API is not available in the installed demucs version.
    """
    log.info(f"Separating audio with Demucs ({model})")
    _install_soundfile_save_patch()

    try:
        separator = _get_separator(model)
        if separator is not None:
            _separate_via_api(separator, audio_path, demucs_dir, model)
        else:
            _separate_via_subprocess(audio_path, demucs_dir, model)
    except Exception as e:
        # Both paths failed — surface as DegradedError so callers can
        # eventually skip source separation and fall back to TTS-only
        # mode instead of aborting the whole job. Behavior preserved
        # from P5.1.
        raise DegradedError(
            f"Demucs source separation failed: {e}", feature="demucs"
        ) from e

    base_name = os.path.splitext(os.path.basename(audio_path))[0]
    stem_dir = os.path.join(demucs_dir, model, base_name)

    return {
        "vocals": os.path.join(stem_dir, "vocals.wav"),
        "no_vocals": os.path.join(stem_dir, "no_vocals.wav"),
    }


def get_audio_duration(path: str) -> float:
    """Get audio duration in seconds using ffprobe."""
    result = subprocess.run(
        [
            "ffprobe", "-v", "quiet",
            "-print_format", "json",
            "-show_entries", "format=duration",
            path,
        ],
        capture_output=True, text=True, check=True, timeout=cfg.ffmpeg.timeout_default,
    )
    data = json.loads(result.stdout)
    return float(data["format"]["duration"])
