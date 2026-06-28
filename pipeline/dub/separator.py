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

# Wall-clock cap for a single Demucs separation. The demucs call has no timeout
# of its own, so a pathological/short clip could otherwise wedge htdemucs and stall
# the whole batch. We run the separation in a daemon watchdog THREAD (in-process —
# so the soundfile-save patch and the cross-call Separator cache both apply, and the
# dispatch stays unit-testable) and join() with this timeout; on expiry we abandon the
# wedged thread and raise DegradedError so brand_pass reroutes the clip to the
# music-only / TTS-only path instead of blocking. Override via env DEMUCS_TIMEOUT (s).
#
# NOTE: an earlier revision ran this in a spawned CHILD PROCESS for a hard kill, but
# `spawn` re-imports a fresh interpreter — which silently defeated the in-process model
# cache (htdemucs reloaded every clip) and made the dispatch un-mockable. The thread
# keeps the timeout intent while restoring both. demucs handles space-containing paths
# fine (it takes an argv list, never a shell), so no path quoting/staging is needed.
DEMUCS_TIMEOUT = int(os.environ.get("DEMUCS_TIMEOUT", "900"))


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


def _stem_paths(audio_path: str, demucs_dir: str, model: str) -> tuple[str, str]:
    """Compute the {vocals, no_vocals} stem paths demucs writes for this input."""
    base_name = os.path.splitext(os.path.basename(audio_path))[0]
    stem_dir = os.path.join(demucs_dir, model, base_name)
    return (
        os.path.join(stem_dir, "vocals.wav"),
        os.path.join(stem_dir, "no_vocals.wav"),
    )


def _verify_stems_written(audio_path: str, demucs_dir: str, model: str) -> None:
    """Raise if demucs returned cleanly but didn't actually write both stems.

    demucs's CLI (`demucs.separate.main`) prints to stderr and returns 0 for some
    failures (e.g. a missing input file, or a save backend that silently no-ops),
    leaving no stems on disk. This is the defensive check that turns that silent
    miss into a raise — which `separate_audio` then surfaces as DegradedError so
    the caller reroutes instead of consuming bogus paths. Lives inside the REAL
    separation impls (not the dispatcher) so test doubles that replace them are
    not forced to fabricate output files.
    """
    vocals, no_vocals = _stem_paths(audio_path, demucs_dir, model)
    if not (os.path.exists(vocals) and os.path.exists(no_vocals)):
        raise RuntimeError(
            f"Demucs completed but stems are missing under "
            f"{os.path.dirname(vocals)}"
        )


def _separate_via_subprocess(audio_path: str, demucs_dir: str, model: str) -> None:
    """Fallback path for older demucs versions without `demucs.api.Separator`.

    Mirrors the original P5 behavior: try GPU, fall back to CPU on OOM. demucs's
    `main()` takes an argv list (no shell), so space-containing paths are handled
    correctly without any quoting/staging.
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
    _verify_stems_written(audio_path, demucs_dir, model)


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
    _verify_stems_written(audio_path, demucs_dir, model)


def _run_separation(audio_path: str, demucs_dir: str, model: str) -> None:
    """Do the actual separation: try the in-process API, else subprocess.

    Module-level (not a closure) so `separate_audio` can run it as the target of
    its watchdog thread. Runs in the current interpreter, so the soundfile-save
    patch and the cross-clip Separator cache both apply.
    """
    _install_soundfile_save_patch()
    separator = _get_separator(model)
    if separator is not None:
        _separate_via_api(separator, audio_path, demucs_dir, model)
    else:
        _separate_via_subprocess(audio_path, demucs_dir, model)


def separate_audio(audio_path: str, demucs_dir: str, model: str = "htdemucs") -> dict[str, str]:
    """
    Separate audio into vocals and accompaniment using Demucs.

    `demucs_dir` must be an already-created directory owned by the caller
    (typically from a `temp_dir("demucs", ...)` context manager). Demucs
    writes its output tree under this path.

    Returns dict with paths: {"vocals": ..., "no_vocals": ...}. Note: these
    paths live inside `demucs_dir`; read them BEFORE the caller exits its
    context manager or the files will be cleaned up.

    The separation runs in an in-process daemon WATCHDOG THREAD joined with a
    DEMUCS_TIMEOUT wall-clock cap — the demucs call has no timeout of its own, so a
    pathological/short clip could otherwise wedge htdemucs and stall the batch. On
    timeout we abandon the wedged thread and raise DegradedError so callers
    (brand_pass) reroute to the music-only / TTS-only path instead of blocking. The
    separation uses the in-process `demucs.api.Separator` cache (fast — model stays
    loaded across clips) or the subprocess fallback for older demucs; both honour the
    soundfile-save patch because they run in this interpreter.
    """
    log.info(f"Separating audio with Demucs ({model}, timeout={DEMUCS_TIMEOUT}s)")

    # In-process thread (not a spawned child): keeps the soundfile-save patch and the
    # cross-clip Separator cache live, and keeps the dispatch mockable. A thread can't
    # be force-killed, so on timeout we leak the (daemon) thread and degrade — rare,
    # and far cheaper than reloading htdemucs per clip just to get a hard kill.
    box: dict[str, BaseException] = {}

    def _runner() -> None:
        try:
            _run_separation(audio_path, demucs_dir, model)
        except BaseException as e:  # noqa: BLE001 — relayed to the caller below
            box["error"] = e

    worker = threading.Thread(target=_runner, name="demucs-separate", daemon=True)
    worker.start()
    worker.join(timeout=DEMUCS_TIMEOUT)

    if worker.is_alive():
        # Timed out — abandon the wedged thread and degrade.
        log.warning(
            f"Demucs separation exceeded {DEMUCS_TIMEOUT}s — abandoning thread and degrading"
        )
        raise DegradedError(
            f"Demucs source separation timed out after {DEMUCS_TIMEOUT}s",
            feature="demucs",
        )

    if "error" in box:
        # Either path raised (incl. the missing-stems guard) — surface as
        # DegradedError so callers skip source separation and fall back to TTS-only
        # instead of aborting the job. Behavior preserved from P5.1.
        raise DegradedError(
            f"Demucs source separation failed: {box['error']}", feature="demucs"
        ) from box["error"]

    vocals, no_vocals = _stem_paths(audio_path, demucs_dir, model)
    return {"vocals": vocals, "no_vocals": no_vocals}


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
