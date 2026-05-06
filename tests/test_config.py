"""Tests for pipeline/config.py — Config loading, defaults, env overrides."""

import os
import pytest
from pipeline.config import Config, AudioConfig, TranslateConfig, WebConfig, GPUConfig, cfg


class TestConfigDefaults:
    """Test that all config defaults are set correctly."""

    def test_audio_defaults(self):
        c = Config()
        assert c.audio.sample_rate == 16000
        assert c.audio.sample_rate_hq == 44100
        assert c.audio.channels == 1
        assert c.audio.channels_hq == 2
        assert c.audio.codec == "pcm_s16le"

    def test_transcribe_defaults(self):
        c = Config()
        assert c.transcribe.default_model == "medium"
        assert c.transcribe.beam_size == 5
        assert c.transcribe.vad_filter is True
        assert c.transcribe.gpu_compute_type == "float16"
        assert c.transcribe.cpu_compute_type == "int8"

    def test_translate_defaults(self):
        c = Config()
        assert c.translate.batch_size == 20
        assert c.translate.grok_model == "grok-3-mini-fast"
        assert c.translate.grok_temperature == 0.3
        assert c.translate.grok_timeout == 120
        assert c.translate.gemini_model == "gemini-2.5-flash-lite"
        assert c.translate.gemini_temperature == 0.3
        assert c.translate.attempts_single_key == 6
        assert c.translate.attempts_multi_key == 3

    def test_tts_defaults(self):
        c = Config()
        assert c.tts.concurrency == 5
        assert c.tts.retry_attempts == 3
        assert c.tts.timeout == 30
        assert c.tts.output_sample_rate == 24000

    def test_dub_defaults(self):
        c = Config()
        assert c.dub.fade_duration == 0.015
        assert c.dub.limiter_threshold == 0.95
        assert c.dub.audio_bitrate == "192k"
        assert c.dub.demucs_model == "htdemucs"

    def test_web_defaults(self):
        c = Config()
        assert c.web.port == 3456
        assert c.web.max_upload_bytes == 100 * 1024 * 1024
        assert c.web.job_timeout == 30 * 60
        assert c.web.idle_timeout == 90 * 60

    def test_gpu_defaults(self):
        c = Config()
        assert c.gpu.whisper_cache_size == 2
        assert c.gpu.vram_min_free_mb == 2048
        assert c.gpu.force_cpu is False

    def test_ffmpeg_timeouts(self):
        c = Config()
        assert c.ffmpeg.timeout_short == 30
        assert c.ffmpeg.timeout_default == 300
        assert c.ffmpeg.timeout_burn == 600

    def test_frozen_dataclass(self):
        c = Config()
        with pytest.raises(AttributeError):
            c.audio.sample_rate = 44100  # type: ignore


class TestConfigSingleton:
    """Test module-level cfg singleton."""

    def test_cfg_is_config(self):
        assert isinstance(cfg, Config)

    def test_cfg_has_all_sections(self):
        assert cfg.audio is not None
        assert cfg.transcribe is not None
        assert cfg.translate is not None
        assert cfg.subtitle is not None
        assert cfg.tts is not None
        assert cfg.dub is not None
        assert cfg.ocr is not None
        assert cfg.burn is not None
        assert cfg.ffmpeg is not None
        assert cfg.gpu is not None
        assert cfg.web is not None


class TestConfigEnvOverride:
    """Test env var overrides via Config.load()."""

    def test_port_override(self, monkeypatch):
        monkeypatch.setenv("PORT", "8080")
        c = Config.load()
        assert c.web.port == 8080

    def test_batch_size_override(self, monkeypatch):
        monkeypatch.setenv("TRANSLATE_BATCH_SIZE", "50")
        c = Config.load()
        assert c.translate.batch_size == 50

    def test_invalid_env_uses_default(self, monkeypatch):
        monkeypatch.setenv("PORT", "not_a_number")
        c = Config.load()
        assert c.web.port == 3456  # default

    def test_whisper_cache_size_override(self, monkeypatch):
        monkeypatch.setenv("WHISPER_CACHE_SIZE", "3")
        c = Config.load()
        assert c.gpu.whisper_cache_size == 3

    def test_vram_min_free_mb_override(self, monkeypatch):
        monkeypatch.setenv("VRAM_MIN_FREE_MB", "4096")
        c = Config.load()
        assert c.gpu.vram_min_free_mb == 4096

    def test_force_cpu_override_true(self, monkeypatch):
        monkeypatch.setenv("FORCE_CPU", "1")
        c = Config.load()
        assert c.gpu.force_cpu is True

    def test_force_cpu_override_false(self, monkeypatch):
        monkeypatch.setenv("FORCE_CPU", "0")
        c = Config.load()
        assert c.gpu.force_cpu is False
