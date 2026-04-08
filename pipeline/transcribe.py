import json
import os
import threading
from collections import OrderedDict

from faster_whisper import WhisperModel

from pipeline import gpu_state
from pipeline.config import cfg
from pipeline.logger import get_logger

log = get_logger("Transcribe")


# ─── Process-wide LRU model cache (load once, reuse across jobs) ─────────────
#
# Cache key is (model_name, device, compute_type) so the same model can be
# cached on both GPU and CPU if the runtime falls back. Eviction is LRU at
# `cfg.gpu.whisper_cache_size` (default 2 = fits medium + large-v3 on a 3060).
_model_cache: "OrderedDict[tuple[str, str, str], WhisperModel]" = OrderedDict()
_model_lock = threading.Lock()


def _make_cache_key(model_name: str, device: str, compute_type: str) -> tuple[str, str, str]:
    return (model_name, device, compute_type)


def _evict_if_needed() -> None:
    """Pop the oldest entry if cache exceeds the configured size."""
    while len(_model_cache) > cfg.gpu.whisper_cache_size:
        evicted_key, evicted_model = _model_cache.popitem(last=False)
        log.info(f"LRU evicting Whisper model: {evicted_key}")
        del evicted_model
        gpu_state.empty_cuda_cache()


def _try_load_gpu(model_name: str) -> WhisperModel | None:
    """Try to load Whisper on GPU. Returns None on failure (caller falls back)."""
    if not gpu_state.should_use_gpu():
        return None
    try:
        model = WhisperModel(
            model_name, device="cuda", compute_type=cfg.transcribe.gpu_compute_type
        )
        log.info(f"Whisper loaded on GPU (CUDA): {model_name}")
        return model
    except Exception as e:
        log.warning(f"Whisper GPU init failed for {model_name} ({e}), using CPU...")
        gpu_state.mark_gpu_unavailable(f"Whisper GPU init failed: {e}")
        return None


def _load_cpu(model_name: str) -> WhisperModel:
    """Load Whisper on CPU. Always succeeds (or raises if model name invalid)."""
    model = WhisperModel(
        model_name, device="cpu", compute_type=cfg.transcribe.cpu_compute_type
    )
    log.info(f"Whisper loaded on CPU: {model_name}")
    return model


def _get_model(model_name: str) -> WhisperModel:
    """Get or create a cached Whisper model. Thread-safe LRU."""
    with _model_lock:
        # Determine target device based on sticky GPU state
        device = "cuda" if gpu_state.should_use_gpu() else "cpu"
        compute_type = (
            cfg.transcribe.gpu_compute_type if device == "cuda"
            else cfg.transcribe.cpu_compute_type
        )
        key = _make_cache_key(model_name, device, compute_type)

        # Cache hit: move to end (LRU recency) and return
        if key in _model_cache:
            _model_cache.move_to_end(key)
            log.info(f"Reusing cached Whisper model: {model_name} ({device})")
            return _model_cache[key]

        # Cache miss: load fresh
        log.info(f"Loading Whisper model: {model_name} ({device})")
        model = _try_load_gpu(model_name) if device == "cuda" else None
        if model is None:
            # Either GPU was already disabled, or just failed → use CPU
            cpu_key = _make_cache_key(model_name, "cpu", cfg.transcribe.cpu_compute_type)
            if cpu_key in _model_cache:
                _model_cache.move_to_end(cpu_key)
                return _model_cache[cpu_key]
            model = _load_cpu(model_name)
            _model_cache[cpu_key] = model
        else:
            _model_cache[key] = model

        _evict_if_needed()
        return model


def _fallback_to_cpu(model_name: str) -> WhisperModel:
    """Replace cached GPU model with CPU model after runtime failure.

    Marks GPU unavailable process-wide so subsequent jobs don't waste a
    GPU init attempt.
    """
    with _model_lock:
        log.warning(f"Whisper GPU runtime error — switching {model_name} to CPU")
        gpu_state.mark_gpu_unavailable(f"Whisper runtime error on {model_name}")
        # Evict any GPU entry for this model
        gpu_key = _make_cache_key(model_name, "cuda", cfg.transcribe.gpu_compute_type)
        if gpu_key in _model_cache:
            del _model_cache[gpu_key]
            gpu_state.empty_cuda_cache()
        # Load + cache CPU model
        cpu_key = _make_cache_key(model_name, "cpu", cfg.transcribe.cpu_compute_type)
        if cpu_key not in _model_cache:
            _model_cache[cpu_key] = _load_cpu(model_name)
            _evict_if_needed()
        return _model_cache[cpu_key]


def transcribe(
    audio_path: str,
    model_name: str = "medium",
    source_lang: str | None = None,
    cache_dir: str | None = None,
    use_cache: bool = True,
) -> tuple[list[dict], str]:
    """
    Transcribe audio using faster-whisper.

    Returns (segments, detected_language) where segments is a list of
    {"start": float, "end": float, "text": str}.
    """
    # Cache path — include model name and source lang to avoid stale cache
    if cache_dir is None:
        cache_dir = os.path.dirname(os.path.abspath(audio_path))
    base_name = os.path.splitext(os.path.basename(audio_path))[0]
    lang_tag = source_lang or "auto"
    cache_path = os.path.join(cache_dir, f"{base_name}.{model_name}.{lang_tag}.transcript.json")

    # Load from cache if available
    if use_cache and os.path.isfile(cache_path):
        log.info(f"Loading cached transcript: {cache_path}")
        with open(cache_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data["segments"], data["language"]

    # Get cached model (loaded once, reused across jobs)
    model = _get_model(model_name)

    transcribe_kwargs = {
        "beam_size": cfg.transcribe.beam_size,
        "vad_filter": cfg.transcribe.vad_filter,
    }
    if source_lang:
        transcribe_kwargs["language"] = source_lang

    log.info("Transcribing...")
    try:
        raw_segments, info = model.transcribe(audio_path, **transcribe_kwargs)
        # Force iteration (lazy generator) — catches GPU errors early
        raw_segments = list(raw_segments)
    except Exception as e:
        # GPU model was cached but failed at runtime (e.g. cublas missing)
        # Fall back to CPU and retry
        log.warning(f"GPU transcription failed ({e}), falling back to CPU...")
        model = _fallback_to_cpu(model_name)
        raw_segments, info = model.transcribe(audio_path, **transcribe_kwargs)
        raw_segments = list(raw_segments)

    detected_lang = info.language
    log.info(f"Detected language: {detected_lang} (probability: {info.language_probability:.2f})")

    segments = [
        {"start": round(seg.start, 3), "end": round(seg.end, 3), "text": seg.text.strip()}
        for seg in raw_segments
    ]

    log.info(f"Transcribed {len(segments)} segments")

    # Save cache
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump({"language": detected_lang, "segments": segments}, f, ensure_ascii=False, indent=2)
    log.info(f"Cached transcript: {cache_path}")

    return segments, detected_lang
