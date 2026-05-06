"""Tests for estimate_eta_seconds() in web/pipeline_runner.py.

Heuristic ETA based on video duration × shared cost + per-lang cost × N langs.
Advisory only — never used for job control, just for /api/status display.

Tests focus on directional behavior (X makes ETA bigger / smaller) rather
than exact magic numbers, since the heuristic constants are an implementation
detail tuned against empirical RTX 3060 runs.
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
        target_langs=kwargs.pop("target_langs", ["vi"]),
        **kwargs,
    )


class TestEtaEstimator:
    def test_basic_short_video_hits_min_floor(self):
        """Floor is 30s — short videos get rounded up."""
        eta = estimate_eta_seconds(_p(), 1.0, "RTX 3060")
        assert eta == 30

    def test_long_video_scales_with_duration(self):
        eta_60s = estimate_eta_seconds(_p(), 60.0, "RTX 3060")
        eta_600s = estimate_eta_seconds(_p(), 600.0, "RTX 3060")
        assert eta_600s > eta_60s
        # ~10x duration → ~10x cost (not exact, but ballpark)
        assert eta_600s / eta_60s > 5

    def test_large_v3_makes_eta_bigger(self):
        """large-v3 transcribe is slower → bigger ETA."""
        eta_medium = estimate_eta_seconds(_p(whisper_model="medium"), 600.0, "RTX 3060")
        eta_large = estimate_eta_seconds(_p(whisper_model="large-v3"), 600.0, "RTX 3060")
        assert eta_large > eta_medium

    def test_dub_makes_eta_bigger(self):
        eta_basic = estimate_eta_seconds(_p(), 600.0, "RTX 3060")
        eta_dub = estimate_eta_seconds(_p(dub=True, audio_mode="custom_bgm"), 600.0, "RTX 3060")
        assert eta_dub > eta_basic

    def test_keep_original_bgm_adds_demucs_overhead(self):
        """keep_original_bgm triggers Demucs (shared) → bigger ETA than custom_bgm."""
        eta_custom = estimate_eta_seconds(
            _p(dub=True, audio_mode="custom_bgm"), 600.0, "RTX 3060",
        )
        eta_keep = estimate_eta_seconds(
            _p(dub=True, audio_mode="keep_original_bgm"), 600.0, "RTX 3060",
        )
        assert eta_keep > eta_custom

    def test_ocr_makes_eta_bigger(self):
        eta_basic = estimate_eta_seconds(_p(), 600.0, "RTX 3060")
        eta_ocr = estimate_eta_seconds(_p(translate_ocr=True), 600.0, "RTX 3060")
        assert eta_ocr > eta_basic

    def test_cpu_fallback_makes_eta_much_bigger(self):
        """gpu_name=None → CPU mode → ~6x slower."""
        eta_gpu = estimate_eta_seconds(_p(), 600.0, "RTX 3060")
        eta_cpu = estimate_eta_seconds(_p(), 600.0, None)
        assert eta_cpu == eta_gpu * 6

    def test_sticky_cpu_also_triggers_multiplier(self):
        """Even if gpu_name is passed, sticky CPU flag applies multiplier."""
        gpu_state.mark_gpu_unavailable("test")
        eta_gpu_off = estimate_eta_seconds(_p(), 600.0, "RTX 3060")
        gpu_state.reset_for_tests()
        eta_gpu_on = estimate_eta_seconds(_p(), 600.0, "RTX 3060")
        assert eta_gpu_off > eta_gpu_on

    def test_more_langs_makes_eta_bigger(self):
        """N langs → ETA scales linearly per-lang. The whole point of the
        multi-target architecture: ETA grows with langs, but slower than N×
        because shared work (transcribe, Demucs) doesn't repeat."""
        eta_1 = estimate_eta_seconds(_p(target_langs=["vi"]), 600.0, "RTX 3060")
        eta_5 = estimate_eta_seconds(
            _p(target_langs=["vi", "en", "ja", "es", "ko"]), 600.0, "RTX 3060",
        )
        assert eta_5 > eta_1
        # Should NOT be 5× (that would mean no shared work) — well below
        assert eta_5 < eta_1 * 5

    def test_minimum_30_seconds(self):
        eta = estimate_eta_seconds(_p(), 0.1, "RTX 3060")
        assert eta == 30

    def test_zero_duration_safe(self):
        eta = estimate_eta_seconds(_p(), 0.0, "RTX 3060")
        assert eta == 30
