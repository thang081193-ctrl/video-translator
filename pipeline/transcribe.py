import json
import os
import threading

from faster_whisper import WhisperModel


# ─── Global model cache (load once, reuse across jobs) ───────────────────────
_model_cache: dict[str, WhisperModel] = {}
_model_lock = threading.Lock()


def _get_model(model_name: str) -> WhisperModel:
    """Get or create a cached Whisper model. Thread-safe."""
    with _model_lock:
        if model_name in _model_cache:
            print(f"  Reusing cached Whisper model: {model_name}")
            return _model_cache[model_name]

        print(f"  Loading Whisper model: {model_name}")

        # Try GPU first
        try:
            model = WhisperModel(model_name, device="cuda", compute_type="float16")
            print("  Model loaded on GPU (CUDA)")
            _model_cache[model_name] = model
            return model
        except Exception as e:
            print(f"  GPU init failed ({e}), using CPU...")

        model = WhisperModel(model_name, device="cpu", compute_type="int8")
        print("  Model loaded on CPU")
        _model_cache[model_name] = model
        return model


def _fallback_to_cpu(model_name: str) -> WhisperModel:
    """Replace cached GPU model with CPU model after runtime failure."""
    with _model_lock:
        print(f"  GPU runtime error — switching to CPU model")
        model = WhisperModel(model_name, device="cpu", compute_type="int8")
        _model_cache[model_name] = model
        print("  CPU model ready")
        return model


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
        print(f"  Loading cached transcript: {cache_path}")
        with open(cache_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data["segments"], data["language"]

    # Get cached model (loaded once, reused across jobs)
    model = _get_model(model_name)

    transcribe_kwargs = {
        "beam_size": 5,
        "vad_filter": True,
    }
    if source_lang:
        transcribe_kwargs["language"] = source_lang

    print("  Transcribing...")
    try:
        raw_segments, info = model.transcribe(audio_path, **transcribe_kwargs)
        # Force iteration (lazy generator) — catches GPU errors early
        raw_segments = list(raw_segments)
    except Exception as e:
        # GPU model was cached but failed at runtime (e.g. cublas missing)
        # Fall back to CPU and retry
        print(f"  GPU transcription failed ({e}), falling back to CPU...")
        model = _fallback_to_cpu(model_name)
        raw_segments, info = model.transcribe(audio_path, **transcribe_kwargs)
        raw_segments = list(raw_segments)

    detected_lang = info.language
    print(f"  Detected language: {detected_lang} (probability: {info.language_probability:.2f})")

    segments = [
        {"start": round(seg.start, 3), "end": round(seg.end, 3), "text": seg.text.strip()}
        for seg in raw_segments
    ]

    print(f"  Transcribed {len(segments)} segments")

    # Save cache
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump({"language": detected_lang, "segments": segments}, f, ensure_ascii=False, indent=2)
    print(f"  Cached transcript: {cache_path}")

    return segments, detected_lang
