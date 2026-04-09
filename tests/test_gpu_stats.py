"""Tests for pipeline.gpu_stats — nvidia-smi parsing + cache snapshot + /api/gpu integration."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from pipeline import gpu_stats, gpu_state


@pytest.fixture(autouse=True)
def _reset_state():
    gpu_state.reset_for_tests()
    yield
    gpu_state.reset_for_tests()


class TestParseNvidiaSmi:
    """_parse_nvidia_smi converts CSV output to a typed dict."""

    def test_valid_csv(self):
        raw = "NVIDIA GeForce RTX 3060, 12288, 4096, 8192, 75, 68"
        result = gpu_stats._parse_nvidia_smi(raw)
        assert result is not None
        assert result["name"] == "NVIDIA GeForce RTX 3060"
        assert result["vram_total_mb"] == 12288
        assert result["vram_used_mb"] == 4096
        assert result["vram_free_mb"] == 8192
        assert result["utilization_pct"] == 75
        assert result["temperature_c"] == 68
        assert result["source"] == "nvidia-smi"

    def test_only_first_gpu_reported(self):
        """If multiple GPUs, only first line is parsed."""
        raw = "RTX 3060, 12288, 4096, 8192, 75, 68\nRTX 4090, 24576, 0, 24576, 0, 40"
        result = gpu_stats._parse_nvidia_smi(raw)
        assert result["name"] == "RTX 3060"
        assert result["vram_total_mb"] == 12288

    def test_empty_returns_none(self):
        assert gpu_stats._parse_nvidia_smi("") is None

    def test_whitespace_only_returns_none(self):
        assert gpu_stats._parse_nvidia_smi("   \n   ") is None

    def test_wrong_field_count_returns_none(self):
        raw = "RTX 3060, 12288, 4096"  # only 3 fields, expected 6
        assert gpu_stats._parse_nvidia_smi(raw) is None

    def test_non_numeric_returns_none(self):
        raw = "RTX 3060, NOT_A_NUMBER, 4096, 8192, 75, 68"
        assert gpu_stats._parse_nvidia_smi(raw) is None


class TestCollectGpuStats:
    """collect_gpu_stats() — public entry point."""

    def test_nvidia_smi_success(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "RTX 3060, 12288, 4096, 8192, 75, 68"
        with patch("subprocess.run", return_value=mock_result):
            stats = gpu_stats.collect_gpu_stats()
        assert stats["available"] is True
        assert stats["name"] == "RTX 3060"
        assert stats["vram_free_mb"] == 8192
        assert stats["source"] == "nvidia-smi"
        assert stats["force_cpu_sticky"] is False

    def test_nvidia_smi_missing_falls_back_to_torch(self):
        """If nvidia-smi returns None, try torch.cuda."""
        with patch("subprocess.run", side_effect=FileNotFoundError("no nvidia-smi")):
            with patch("pipeline.gpu_stats._try_torch_cuda") as mock_torch:
                mock_torch.return_value = {
                    "name": "Mock GPU",
                    "vram_total_mb": 8000,
                    "vram_used_mb": 1000,
                    "vram_free_mb": 7000,
                    "source": "torch.cuda",
                }
                stats = gpu_stats.collect_gpu_stats()
        assert stats["available"] is True
        assert stats["source"] == "torch.cuda"

    def test_both_unavailable_returns_available_false(self):
        with patch("subprocess.run", side_effect=FileNotFoundError()):
            with patch("pipeline.gpu_stats._try_torch_cuda", return_value=None):
                stats = gpu_stats.collect_gpu_stats()
        assert stats["available"] is False
        assert "error" in stats

    def test_never_raises_on_subprocess_error(self):
        """Even if subprocess itself crashes, no exception propagates."""
        with patch("subprocess.run", side_effect=OSError("unexpected")):
            with patch("pipeline.gpu_stats._try_torch_cuda", return_value=None):
                stats = gpu_stats.collect_gpu_stats()
        assert stats["available"] is False

    def test_sticky_flag_reflected(self):
        gpu_state.mark_gpu_unavailable("test")
        with patch("subprocess.run", side_effect=FileNotFoundError()):
            with patch("pipeline.gpu_stats._try_torch_cuda", return_value=None):
                stats = gpu_stats.collect_gpu_stats()
        assert stats["force_cpu_sticky"] is True
        assert stats["unavailable_reason"] == "test"

    def test_timeout_returns_none(self):
        """nvidia-smi timeout should not crash, should return None."""
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("nvidia-smi", 2)):
            with patch("pipeline.gpu_stats._try_torch_cuda", return_value=None):
                stats = gpu_stats.collect_gpu_stats()
        assert stats["available"] is False


class TestCollectCacheStats:
    """Cache snapshot should reflect current state of P6.A caches."""

    def test_empty_caches(self):
        # Clear all caches first
        from pipeline.transcribe import _model_cache
        from pipeline.ocr.detector import _ocr_readers
        from pipeline.dub.separator import _separator_cache
        _model_cache.clear()
        _ocr_readers.clear()
        _separator_cache.clear()

        result = gpu_stats._collect_cache_stats()
        assert result["whisper_cached_models"] == []
        assert result["ocr_cached_langs"] == []
        assert result["demucs_cached"] == []

    def test_whisper_cache_populated(self):
        from pipeline.transcribe import _model_cache
        _model_cache.clear()
        _model_cache[("medium", "cuda", "float16")] = MagicMock()
        _model_cache[("large-v3", "cuda", "float16")] = MagicMock()

        result = gpu_stats._collect_cache_stats()
        assert len(result["whisper_cached_models"]) == 2
        models = [e["model"] for e in result["whisper_cached_models"]]
        assert "medium" in models
        assert "large-v3" in models
        _model_cache.clear()

    def test_demucs_cache_populated(self):
        from pipeline.dub.separator import _separator_cache
        _separator_cache.clear()
        _separator_cache["htdemucs|cuda"] = MagicMock()
        result = gpu_stats._collect_cache_stats()
        assert "htdemucs|cuda" in result["demucs_cached"]
        _separator_cache.clear()
