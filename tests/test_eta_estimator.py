"""Tests for estimate_eta_seconds() in web/pipeline_runner.py (P6.C).

Heuristic ETA based on video duration × model size × feature flags × device.
Advisory only — never used for job control, just for /api/status display.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from pipeline import gpu_state
from web.pipeline_runner import PipelineParams, estimate_eta_seconds


@pytest.fixture(autouse=True)
def _reset():
    gpu_state.reset_for_tests()
    yield
    gpu_state.reset_for_tests()


def _p(**kwargs) -> PipelineParams:
    return PipelineParams(
        video_path="x.mp4",
        target_lang="vi",
        **kwargs,
    )


class TestEtaEstimator:
    def test_basic_medium_gpu(self):
        """60s video × 0.35 baseline × 1.0 (no modifiers) = 21s → floor to min 30."""
        eta = estimate_eta_seconds(_p(), 60.0, "RTX 3060")
        assert eta == 30  # hit the min floor

    def test_long_video_above_min(self):
        """600s × 0.35 = 210s (above 30s floor)."""
        eta = estimate_eta_seconds(_p(), 600.0, "RTX 3060")
        assert eta == 210

    def test_large_v3_multiplier(self):
        """large-v3 is ~3x slower than medium."""
        eta_medium = estimate_eta_seconds(_p(whisper_model="medium"), 600.0, "RTX 3060")
        eta_large = estimate_eta_seconds(_p(whisper_model="large-v3"), 600.0, "RTX 3060")
        assert eta_large == eta_medium * 3

    def test_dub_multiplier(self):
        """dub=True adds ~2x (TTS is serial)."""
        eta_basic = estimate_eta_seconds(_p(), 600.0, "RTX 3060")
        eta_dub = estimate_eta_seconds(_p(dub=True), 600.0, "RTX 3060")
        assert eta_dub == eta_basic * 2

    def test_ocr_multiplier(self):
        """translate_ocr adds ~30%."""
        eta_basic = estimate_eta_seconds(_p(), 600.0, "RTX 3060")
        eta_ocr = estimate_eta_seconds(_p(translate_ocr=True), 600.0, "RTX 3060")
        assert eta_ocr == int(eta_basic * 1.3)

    def test_cpu_fallback_multiplier(self):
        """gpu_name=None → CPU mode → 6x slower."""
        eta_gpu = estimate_eta_seconds(_p(), 600.0, "RTX 3060")
        eta_cpu = estimate_eta_seconds(_p(), 600.0, None)
        assert eta_cpu == eta_gpu * 6

    def test_sticky_cpu_also_triggers_multiplier(self):
        """Even if gpu_name is passed, sticky CPU flag applies 6x multiplier."""
        gpu_state.mark_gpu_unavailable("test")
        eta = estimate_eta_seconds(_p(), 600.0, "RTX 3060")
        # Base 210 × 6 = 1260
        assert eta == 1260

    def test_all_multipliers_combined(self):
        """large-v3 + dub + ocr + cpu: base × 3 × 2 × 1.3 × 6."""
        eta = estimate_eta_seconds(
            _p(whisper_model="large-v3", dub=True, translate_ocr=True),
            600.0, None,
        )
        # 210 × 3 × 2 × 1.3 × 6 ≈ 9828
        assert 9800 <= eta <= 9850

    def test_minimum_30_seconds(self):
        """Even with 0.1s video, ETA floor is 30."""
        eta = estimate_eta_seconds(_p(), 0.1, "RTX 3060")
        assert eta == 30

    def test_zero_duration_safe(self):
        """Zero duration doesn't crash; hits min floor."""
        eta = estimate_eta_seconds(_p(), 0.0, "RTX 3060")
        assert eta == 30
