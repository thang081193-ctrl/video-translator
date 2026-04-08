"""Tests for pipeline.transcribe LRU model cache (P6.A).

Mocks `WhisperModel` so tests run on CPU-only dev boxes without
downloading models. Asserts:
- LRU eviction at cfg.gpu.whisper_cache_size
- Cache key includes (model_name, device, compute_type) tuple
- Sticky CPU fallback after GPU init failure
- FORCE_CPU env override skips GPU entirely
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from pipeline import gpu_state
from pipeline import transcribe as t_mod


@pytest.fixture(autouse=True)
def _reset_state():
    """Clear caches and sticky flag before each test."""
    t_mod._model_cache.clear()
    gpu_state.reset_for_tests()
    yield
    t_mod._model_cache.clear()
    gpu_state.reset_for_tests()


def _make_mock_model(name: str = "mock") -> MagicMock:
    """Cheap stand-in for a faster_whisper.WhisperModel instance."""
    m = MagicMock()
    m._mock_name = name
    return m


class TestCacheReuse:
    def test_same_model_returned_on_second_call(self):
        """Cache hit should return the SAME instance, not reload."""
        with patch.object(t_mod, "WhisperModel") as mock_class:
            mock_class.return_value = _make_mock_model("medium")
            m1 = t_mod._get_model("medium")
            m2 = t_mod._get_model("medium")
            assert m1 is m2
            assert mock_class.call_count == 1


class TestLRUEviction:
    def test_eviction_at_size_2(self):
        """With cache_size=2, loading a 3rd model evicts the LRU one."""
        with patch.object(t_mod, "WhisperModel") as mock_class:
            mock_class.side_effect = lambda name, **kw: _make_mock_model(name)
            t_mod._get_model("medium")
            t_mod._get_model("large-v3")
            assert len(t_mod._model_cache) == 2
            t_mod._get_model("tiny")
            assert len(t_mod._model_cache) == 2  # LRU evicted, still 2

    def test_recent_use_protects_from_eviction(self):
        """Touching `medium` should make `large-v3` the LRU victim."""
        with patch.object(t_mod, "WhisperModel") as mock_class:
            mock_class.side_effect = lambda name, **kw: _make_mock_model(name)
            t_mod._get_model("medium")
            t_mod._get_model("large-v3")
            t_mod._get_model("medium")  # bumps medium to MRU
            t_mod._get_model("tiny")    # evicts large-v3, not medium

            keys = [k[0] for k in t_mod._model_cache.keys()]
            assert "medium" in keys
            assert "tiny" in keys
            assert "large-v3" not in keys


class TestCacheKey:
    def test_key_includes_device(self):
        """Same model on GPU vs CPU should be 2 separate cache entries."""
        gpu_key = t_mod._make_cache_key("medium", "cuda", "float16")
        cpu_key = t_mod._make_cache_key("medium", "cpu", "int8")
        assert gpu_key != cpu_key
        assert gpu_key == ("medium", "cuda", "float16")
        assert cpu_key == ("medium", "cpu", "int8")


class TestStickyCpuFallback:
    def test_gpu_init_failure_marks_unavailable(self):
        """If WhisperModel(cuda) raises, gpu_state should be marked CPU-only."""
        def fake_load(name, device=None, compute_type=None):
            if device == "cuda":
                raise RuntimeError("CUDA out of memory")
            return _make_mock_model(f"{name}-cpu")

        with patch.object(t_mod, "WhisperModel", side_effect=fake_load):
            t_mod._get_model("medium")
            assert gpu_state.should_use_gpu() is False
            # Cached entry should be CPU
            cpu_key = t_mod._make_cache_key("medium", "cpu", t_mod.cfg.transcribe.cpu_compute_type)
            assert cpu_key in t_mod._model_cache

    def test_subsequent_call_skips_gpu_after_failure(self):
        """After first GPU failure, second model load goes straight to CPU."""
        attempts = []

        def fake_load(name, device=None, compute_type=None):
            attempts.append(device)
            if device == "cuda":
                raise RuntimeError("CUDA OOM")
            return _make_mock_model(f"{name}-cpu")

        with patch.object(t_mod, "WhisperModel", side_effect=fake_load):
            t_mod._get_model("medium")     # tries cuda, fails, falls back to cpu
            t_mod._get_model("large-v3")   # should go straight to cpu
            cuda_attempts = [a for a in attempts if a == "cuda"]
            assert len(cuda_attempts) == 1, f"Expected 1 cuda attempt, got {cuda_attempts}"


class TestForceCpu:
    def test_force_cpu_skips_gpu_entirely(self, monkeypatch):
        """cfg.gpu.force_cpu=True → never tries cuda.

        Config is frozen, so monkeypatch the module-level `cfg` reference
        inside gpu_state (which is what controls should_use_gpu).
        """
        from dataclasses import replace
        from pipeline.config import cfg as original_cfg
        new_cfg = replace(original_cfg, gpu=replace(original_cfg.gpu, force_cpu=True))
        monkeypatch.setattr(gpu_state, "cfg", new_cfg)

        attempts = []

        def fake_load(name, device=None, compute_type=None):
            attempts.append(device)
            return _make_mock_model(name)

        with patch.object(t_mod, "WhisperModel", side_effect=fake_load):
            t_mod._get_model("medium")
            assert "cuda" not in attempts
            assert "cpu" in attempts
