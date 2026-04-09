"""Tests for _vram_guard_check() in web/pipeline_runner.py (P6.C).

Contract: fail-loud with FatalError when user requests keep_original_bgm
mode (Demucs) with less than cfg.gpu.vram_min_free_mb free VRAM. Pass
through silently when VRAM is sufficient, Demucs isn't requested, or
GPU stats aren't available.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from pipeline.errors import FatalError
from web.pipeline_runner import PipelineParams, _vram_guard_check


def _params(dub=True, audio_mode="keep_original_bgm") -> PipelineParams:
    return PipelineParams(
        video_path="fake.mp4",
        target_lang="vi",
        dub=dub,
        audio_mode=audio_mode,
        bgm_path=None,
        output_dir="/tmp",
    )


class TestVramGuard:
    """The guard only fires when: dub=True AND audio_mode=keep_original_bgm AND VRAM low."""

    def test_no_op_when_dub_disabled(self):
        """No dub → no Demucs → no guard regardless of VRAM."""
        # Should not even call collect_gpu_stats
        with patch("pipeline.gpu_stats.collect_gpu_stats") as mock_stats:
            _vram_guard_check(_params(dub=False))
            mock_stats.assert_not_called()

    def test_no_op_when_custom_bgm(self):
        """custom_bgm mode doesn't need Demucs."""
        with patch("pipeline.gpu_stats.collect_gpu_stats") as mock_stats:
            _vram_guard_check(_params(audio_mode="custom_bgm"))
            mock_stats.assert_not_called()

    def test_no_op_when_gpu_unavailable(self):
        """CPU-only mode: let the pipeline try and fail with its own error."""
        with patch("pipeline.gpu_stats.collect_gpu_stats") as mock_stats:
            mock_stats.return_value = {"available": False}
            # Should not raise — pass through
            _vram_guard_check(_params())

    def test_no_op_when_vram_sufficient(self):
        """Plenty of free VRAM → no-op."""
        with patch("pipeline.gpu_stats.collect_gpu_stats") as mock_stats:
            mock_stats.return_value = {"available": True, "vram_free_mb": 8000}
            _vram_guard_check(_params())

    def test_raises_fatal_when_vram_low(self):
        """Low VRAM + dub + keep_original_bgm → FatalError."""
        with patch("pipeline.gpu_stats.collect_gpu_stats") as mock_stats:
            mock_stats.return_value = {"available": True, "vram_free_mb": 500}
            with pytest.raises(FatalError) as exc_info:
                _vram_guard_check(_params())
            msg = str(exc_info.value).lower()
            assert "vram" in msg
            assert "500" in str(exc_info.value)  # actual free MB in message

    def test_raises_fatal_at_exactly_boundary(self):
        """Free MB exactly equal to threshold still passes (strict less-than)."""
        from pipeline.config import cfg
        with patch("pipeline.gpu_stats.collect_gpu_stats") as mock_stats:
            mock_stats.return_value = {
                "available": True,
                "vram_free_mb": cfg.gpu.vram_min_free_mb,
            }
            _vram_guard_check(_params())  # should not raise

    def test_stats_collection_failure_is_noop(self):
        """If collect_gpu_stats raises, guard should swallow and pass through."""
        with patch("pipeline.gpu_stats.collect_gpu_stats", side_effect=RuntimeError("boom")):
            _vram_guard_check(_params())  # should not raise

    def test_free_mb_none_is_noop(self):
        """torch.cuda fallback may return None for vram_free_mb."""
        with patch("pipeline.gpu_stats.collect_gpu_stats") as mock_stats:
            mock_stats.return_value = {"available": True, "vram_free_mb": None}
            _vram_guard_check(_params())  # should not raise
