"""Centralized configuration — single source of truth for all constants."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env_int(name: str, default: int) -> int:
    val = os.getenv(name)
    if val is None:
        return default
    try:
        return int(val)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    val = os.getenv(name)
    if val is None:
        return default
    try:
        return float(val)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class AudioConfig:
    """Audio extraction settings."""
    sample_rate: int = 16000          # Whisper optimal input
    sample_rate_hq: int = 44100       # Demucs source separation input
    channels: int = 1                 # mono for Whisper
    channels_hq: int = 2              # stereo for Demucs
    codec: str = "pcm_s16le"


@dataclass(frozen=True)
class TranscribeConfig:
    """Whisper STT settings."""
    default_model: str = "medium"
    beam_size: int = 5
    # Lower-capacity models (tiny/base) benefit from a smaller beam with
    # minimal quality loss (~30% faster transcription). Applied per-job via
    # `effective_beam_size(model_name)`; not an env override.
    beam_size_small: int = 1
    small_models: tuple = ("tiny", "base")
    vad_filter: bool = True
    gpu_compute_type: str = "float16"
    cpu_compute_type: str = "int8"

    def effective_beam_size(self, model_name: str) -> int:
        """Return beam size appropriate for the given Whisper model."""
        if model_name in self.small_models:
            return self.beam_size_small
        return self.beam_size


@dataclass(frozen=True)
class TranslateConfig:
    """Translation API settings."""
    grok_endpoint: str = "https://api.x.ai/v1/chat/completions"
    grok_model: str = "grok-3-mini-fast"
    grok_temperature: float = 0.3
    grok_timeout: int = 120
    gemini_model: str = "gemini-2.5-flash-lite"
    gemini_temperature: float = 0.3   # low → faithful translation, no paraphrase
    batch_size: int = 20
    context_window: int = 2           # segments before/after batch for context
    attempts_single_key: int = 6      # retry attempts when only 1 key
    attempts_multi_key: int = 3       # retry attempts per key when multiple
    # Defaults aligned with Gemini's typical retryDelay header (26-30s on
    # free tier RPM cap). The actual wait is the max(error.retryDelay, default)
    # — extracted from the 429 body — so this only matters when Google omits
    # the header (rare). Old default of 5s caused premature retry storms.
    retry_delay_single: int = 30      # seconds between retries (single key)
    retry_delay_multi: int = 30       # seconds between retries (multi key)
    error_max_length: int = 280       # compact error message truncation


@dataclass(frozen=True)
class SubtitleConfig:
    """SRT generation settings."""
    max_line_chars: int = 42
    font_size: int = 13
    margin_v: int = 20
    primary_colour: str = "&H00FFFFFF"
    back_colour: str = "&H80000000"


@dataclass(frozen=True)
class TTSConfig:
    """Text-to-speech settings."""
    concurrency: int = 5              # max concurrent edge-tts requests
    retry_attempts: int = 3
    timeout: int = 30                 # seconds per TTS request
    output_sample_rate: int = 24000
    output_channels: int = 1
    # Segment cache: skip regeneration when (voice, text) hash matches.
    # Disk-backed, LRU eviction by mtime. Shared across jobs in the same process.
    cache_enabled: bool = True                         # DISABLE_TTS_CACHE=1 to turn off
    cache_max_bytes: int = 500 * 1024 * 1024           # TTS_CACHE_MB env override
    cache_evict_to_pct: float = 0.8                    # evict down to 80% of limit


@dataclass(frozen=True)
class DubConfig:
    """Dubbing / audio mixing settings."""
    fade_duration: float = 0.015      # 15ms fade in/out
    limiter_threshold: float = 0.95
    audio_bitrate: str = "192k"
    # Volume scaling for keep_original_bgm mode
    original_bgm_multiplier: float = 4.0
    original_bgm_min: float = 0.5
    original_voice_vol: float = 2.5
    # Volume scaling for custom_bgm mode
    custom_bgm_multiplier: float = 2.0
    custom_voice_vol: float = 1.8
    # Voice-audibility guard: minimum measured voice-over-BGM margin in dB;
    # the BGM gain is cut when the projected margin falls below this (mixer).
    min_voice_margin_db: float = 8.0
    # Demucs
    demucs_model: str = "htdemucs"


def _load_drawtext_unsafe_langs() -> tuple[str, ...]:
    """Load drawtext-unsafe language list from the registry.

    Wrapped in a function to avoid import-time circular dependency
    (pipeline.languages doesn't depend on config, but we want lazy resolution).
    """
    from pipeline.languages import DRAWTEXT_UNSAFE_LANGS
    return DRAWTEXT_UNSAFE_LANGS


@dataclass(frozen=True)
class OCRConfig:
    """OCR detection settings."""
    frame_interval: float = 2.0       # seconds between key frames
    # Languages where ffmpeg drawtext cannot render properly.
    # Derived from pipeline.languages registry — do not edit independently.
    drawtext_unsafe_langs: tuple = field(default_factory=_load_drawtext_unsafe_langs)


@dataclass(frozen=True)
class BurnConfig:
    """Video encoding / subtitle burning settings."""
    crf: int = 18
    preset: str = "medium"
    filter_script_threshold: int = 8000   # chars before switching to filter script file
    # NVENC hardware encoding (NVIDIA GPU) — 5-10x faster than libx264.
    # Auto-detected via `ffmpeg -encoders`; falls back to CPU if unavailable.
    use_nvenc: bool = True                # USE_NVENC — master switch
    nvenc_preset: str = "p4"              # p1=fastest, p7=slowest/best quality
    nvenc_cq: int = 20                    # constant quality (lower = better; ~equiv to CRF)


@dataclass(frozen=True)
class FFmpegConfig:
    """ffmpeg subprocess timeouts."""
    timeout_short: int = 30           # ffprobe, quick checks
    timeout_default: int = 300        # audio extraction, TTS processing
    timeout_burn: int = 600           # video encoding (subtitle burn, overlay)
    nvidia_smi_timeout: int = 5


@dataclass(frozen=True)
class GPUConfig:
    """GPU resource management settings (P6.A).

    All values overridable via env vars in `Config.load()`.
    """
    whisper_cache_size: int = 2          # WHISPER_CACHE_SIZE — LRU cache for Whisper models (medium + large-v3)
    vram_min_free_mb: int = 2048         # VRAM_MIN_FREE_MB — fail-loud if Demucs requested with less free VRAM
    force_cpu: bool = False              # FORCE_CPU — hard override: never attempt CUDA on any model


@dataclass(frozen=True)
class WebConfig:
    """Web server settings."""
    host: str = "0.0.0.0"
    port: int = 3456
    upload_dir: str = "uploads"
    max_upload_bytes: int = 100 * 1024 * 1024   # 100MB
    job_timeout: int = 30 * 60                   # 30 minutes
    cleanup_interval: int = 300                  # 5 minutes
    cleanup_max_age: int = 86400                 # 24 hours
    idle_timeout: int = 90 * 60                  # 90 minutes (Vast.ai auto-stop)
    worker_queue_timeout: int = 5                # seconds to wait for next job


@dataclass(frozen=True)
class Config:
    """Root configuration — aggregates all sub-configs."""
    audio: AudioConfig = field(default_factory=AudioConfig)
    transcribe: TranscribeConfig = field(default_factory=TranscribeConfig)
    translate: TranslateConfig = field(default_factory=TranslateConfig)
    subtitle: SubtitleConfig = field(default_factory=SubtitleConfig)
    tts: TTSConfig = field(default_factory=TTSConfig)
    dub: DubConfig = field(default_factory=DubConfig)
    ocr: OCRConfig = field(default_factory=OCRConfig)
    burn: BurnConfig = field(default_factory=BurnConfig)
    ffmpeg: FFmpegConfig = field(default_factory=FFmpegConfig)
    gpu: GPUConfig = field(default_factory=GPUConfig)
    web: WebConfig = field(default_factory=WebConfig)

    @staticmethod
    def load() -> Config:
        """Load config with env var overrides where applicable."""
        return Config(
            translate=TranslateConfig(
                batch_size=_env_int("TRANSLATE_BATCH_SIZE", 20),
                grok_model=os.getenv("GROK_MODEL", TranslateConfig.grok_model),
                gemini_model=os.getenv("GEMINI_MODEL", TranslateConfig.gemini_model),
                gemini_temperature=_env_float("GEMINI_TEMPERATURE", TranslateConfig.gemini_temperature),
            ),
            tts=TTSConfig(
                concurrency=_env_int("TTS_CONCURRENCY", 5),
                cache_enabled=not _env_bool("DISABLE_TTS_CACHE", False),
                cache_max_bytes=_env_int("TTS_CACHE_MB", 500) * 1024 * 1024,
            ),
            burn=BurnConfig(
                use_nvenc=_env_bool("USE_NVENC", True),
            ),
            gpu=GPUConfig(
                whisper_cache_size=_env_int("WHISPER_CACHE_SIZE", 2),
                vram_min_free_mb=_env_int("VRAM_MIN_FREE_MB", 2048),
                force_cpu=_env_bool("FORCE_CPU", False),
            ),
            web=WebConfig(
                port=_env_int("PORT", 3456),
                max_upload_bytes=_env_int("MAX_UPLOAD_MB", 100) * 1024 * 1024,
                job_timeout=_env_int("JOB_TIMEOUT_MIN", 30) * 60,
                idle_timeout=_env_int("IDLE_TIMEOUT_MIN", 90) * 60,
            ),
        )


# Module-level singleton — import and use directly
cfg = Config.load()
