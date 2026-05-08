"""Tests for pipeline/convert.py — Reels preset, safe-zone layout, ffmpeg integration.

Unit tests cover the pure layout math (no ffmpeg). Integration test runs
ffmpeg against a synthesized lavfi source — gated on ffmpeg being installed
so the suite still passes on bare CI machines.
"""

from __future__ import annotations

import os
import shutil
import subprocess

import pytest

from pipeline.convert import (
    REELS,
    PRESETS,
    Preset,
    _build_video_filter,
    _fit_in_safe_zone,
    _safe_zone_px,
    convert_video,
    list_presets,
    verify_output,
)
from pipeline.errors import FatalError


def _ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


# ─── Preset registry ─────────────────────────────────────────────────────────

class TestPresetRegistry:
    def test_reels_in_presets(self):
        assert "reels" in PRESETS
        assert PRESETS["reels"] is REELS

    def test_reels_dimensions(self):
        assert REELS.width == 1080
        assert REELS.height == 1920
        assert REELS.fps == 30

    def test_reels_safe_zone_pcts(self):
        # Meta Ads spec: top 14% (username), bottom 35% (caption + CTA)
        assert REELS.top_reserve_pct == pytest.approx(0.14)
        assert REELS.bottom_reserve_pct == pytest.approx(0.35)

    def test_list_presets_ui_shape(self):
        presets = list_presets()
        assert len(presets) >= 1
        reels = next(p for p in presets if p["key"] == "reels")
        assert reels["width"] == 1080
        assert reels["height"] == 1920
        assert "label" in reels


# ─── Safe zone math ──────────────────────────────────────────────────────────

class TestSafeZonePx:
    def test_reels_safe_zone_pixels(self):
        top, bottom, safe_h = _safe_zone_px(REELS)
        assert top == int(1920 * 0.14)        # 268
        assert bottom == int(1920 * 0.35)     # 672
        assert safe_h == 1920 - top - bottom  # 980

    def test_safe_zone_within_canvas(self):
        top, bottom, safe_h = _safe_zone_px(REELS)
        assert top + safe_h + bottom == REELS.height


# ─── Layout: _fit_in_safe_zone ───────────────────────────────────────────────

class TestFitInSafeZone:
    def test_landscape_16_9_fits_to_safe_width(self):
        """16:9 source (1920×1080) → fg fills safe_w=1080, height shrinks."""
        fg_w, fg_h, x_off, y_off = _fit_in_safe_zone(1920, 1080, REELS)
        assert fg_w == 1080
        # 1080 / (16/9) = 607.5 → snap-even → 606 or 608
        assert fg_h in (606, 608)
        assert x_off == 0
        # y_off should bias content UP into safe zone (above canvas center)
        canvas_center_y = REELS.height // 2  # 960
        assert y_off < canvas_center_y, "Content must sit above canvas center for Reels Ads"

    def test_landscape_position_in_safe_zone(self):
        """Foreground for 16:9 must lie entirely within the safe zone."""
        fg_w, fg_h, x_off, y_off = _fit_in_safe_zone(1920, 1080, REELS)
        top, bottom, safe_h = _safe_zone_px(REELS)
        assert y_off >= top, "Top of fg crosses into top reserve (username area)"
        assert y_off + fg_h <= REELS.height - bottom, \
            "Bottom of fg crosses into CTA area"

    def test_portrait_9_16_aspect_match_fills_canvas(self):
        """Source already 9:16 → fg fills canvas, no safe-zone shrink."""
        fg_w, fg_h, x_off, y_off = _fit_in_safe_zone(720, 1280, REELS)
        # Aspect matches → content fills the canvas
        assert (fg_w, fg_h) == (REELS.width, REELS.height)
        assert (x_off, y_off) == (0, 0)

    def test_square_1_1_fits_safe_zone(self):
        """1:1 source — taller than safe-zone aspect, fit to safe_h."""
        fg_w, fg_h, x_off, y_off = _fit_in_safe_zone(1080, 1080, REELS)
        _, _, safe_h = _safe_zone_px(REELS)
        # Square is taller than safe-zone aspect (1080:980), fits to height
        assert fg_h == safe_h - (safe_h % 2)
        assert fg_w == fg_h  # square aspect preserved
        assert x_off > 0     # pillarboxed

    def test_dimensions_always_even(self):
        """yuv420p needs even dimensions — composer must snap to even."""
        for src in [(1920, 1080), (1280, 720), (1081, 1921), (1080, 1080), (3840, 2160)]:
            fg_w, fg_h, _, _ = _fit_in_safe_zone(src[0], src[1], REELS)
            assert fg_w % 2 == 0, f"fg_w={fg_w} for src={src} is odd"
            assert fg_h % 2 == 0, f"fg_h={fg_h} for src={src} is odd"

    def test_zero_dimensions_raises(self):
        with pytest.raises(FatalError, match="Invalid source dimensions"):
            _fit_in_safe_zone(0, 1080, REELS)
        with pytest.raises(FatalError, match="Invalid source dimensions"):
            _fit_in_safe_zone(1920, 0, REELS)


# ─── Filter string composition ───────────────────────────────────────────────

class TestBuildVideoFilter:
    def test_aspect_match_simple_scale(self):
        """9:16 source → simple scale, no blur background."""
        f = _build_video_filter(720, 1280, REELS)
        assert f == "[0:v]scale=1080:1920[vout]"

    def test_landscape_uses_blur_pad(self):
        """16:9 source → blur background + foreground overlay with offset."""
        f = _build_video_filter(1920, 1080, REELS)
        assert "boxblur=" in f
        assert "[bg]" in f and "[fg]" in f
        assert "overlay=" in f
        # Y offset must be present (non-zero) since fg is letterboxed
        assert "overlay=0:" in f
        # End label
        assert f.endswith("[vout]")

    def test_landscape_overlay_y_below_center(self):
        """Verify the y-offset in the overlay command places content above canvas center."""
        f = _build_video_filter(1920, 1080, REELS)
        # filter looks like: ...overlay=0:454[vout]
        import re
        m = re.search(r"overlay=(\d+):(\d+)", f)
        assert m, f"no overlay xy in filter: {f}"
        x, y = int(m.group(1)), int(m.group(2))
        assert y < REELS.height // 2, f"y={y} not biased upward (Reels safe zone)"


# ─── PipelineParams validation ───────────────────────────────────────────────

class TestPipelineParamsConvertPreset:
    def test_default_is_none(self):
        from web.pipeline_runner import PipelineParams
        p = PipelineParams(video_path="x.mp4", target_langs=["vi"])
        assert p.convert_preset is None

    def test_reels_accepted(self):
        from web.pipeline_runner import PipelineParams
        p = PipelineParams(
            video_path="x.mp4", target_langs=["vi"], convert_preset="reels",
        )
        assert p.convert_preset == "reels"

    def test_unknown_preset_rejected(self):
        from web.pipeline_runner import PipelineParams
        with pytest.raises(ValueError, match="Unknown convert_preset"):
            PipelineParams(
                video_path="x.mp4", target_langs=["vi"],
                convert_preset="bogus_format",
            )


# ─── Step counting ───────────────────────────────────────────────────────────

class TestCountStepsWithConvert:
    def test_convert_adds_one_shared_step(self):
        from web.pipeline_runner import PipelineParams, count_steps
        without = PipelineParams(video_path="x.mp4", target_langs=["vi"])
        with_convert = PipelineParams(
            video_path="x.mp4", target_langs=["vi"], convert_preset="reels",
        )
        assert count_steps(with_convert) == count_steps(without) + 1

    def test_convert_step_shared_across_langs(self):
        """Convert runs once regardless of N langs (it's pre-fan-out)."""
        from web.pipeline_runner import PipelineParams, count_steps
        one = PipelineParams(
            video_path="x.mp4", target_langs=["vi"], convert_preset="reels",
        )
        five = PipelineParams(
            video_path="x.mp4", target_langs=["vi", "en", "ja", "es", "ko"],
            convert_preset="reels",
        )
        # delta should equal per_lang_step_count × (5-1), not (1 + per_lang × 4)
        from web.pipeline_runner import _per_lang_step_count
        assert count_steps(five) - count_steps(one) == _per_lang_step_count(one) * 4


# ─── Integration: actual ffmpeg encode ───────────────────────────────────────

@pytest.mark.skipif(not _ffmpeg_available(), reason="ffmpeg/ffprobe not installed")
class TestConvertVideoIntegration:
    """Real ffmpeg conversion against synthesized lavfi sources."""

    @staticmethod
    def _make_test_video(path: str, w: int, h: int, duration: float = 1.0) -> None:
        """Synthesize a tiny test clip via ffmpeg lavfi sources."""
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"color=c=blue:s={w}x{h}:d={duration}:r=30",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}",
            "-c:v", "libx264", "-preset", "ultrafast",
            "-c:a", "aac", "-b:a", "96k",
            "-pix_fmt", "yuv420p",
            "-shortest",
            path,
        ]
        subprocess.run(cmd, check=True, capture_output=True, timeout=30)

    @staticmethod
    def _probe(path: str) -> dict:
        result = subprocess.run(
            [
                "ffprobe", "-v", "quiet", "-print_format", "json",
                "-show_streams", "-select_streams", "v:0", path,
            ],
            capture_output=True, text=True, check=True, timeout=10,
        )
        import json
        return json.loads(result.stdout)["streams"][0]

    def test_landscape_16_9_to_reels(self, tmp_path):
        src = str(tmp_path / "src_landscape.mp4")
        out = str(tmp_path / "out.mp4")
        self._make_test_video(src, 1280, 720, duration=0.5)
        convert_video(src, out, REELS)
        info = self._probe(out)
        assert int(info["width"]) == 1080
        assert int(info["height"]) == 1920

    def test_portrait_already_9_16_passthrough(self, tmp_path):
        src = str(tmp_path / "src_portrait.mp4")
        out = str(tmp_path / "out.mp4")
        self._make_test_video(src, 720, 1280, duration=0.5)
        convert_video(src, out, REELS)
        info = self._probe(out)
        assert int(info["width"]) == 1080
        assert int(info["height"]) == 1920

    def test_verify_output_passes_for_compliant(self, tmp_path):
        src = str(tmp_path / "src.mp4")
        out = str(tmp_path / "out.mp4")
        self._make_test_video(src, 1280, 720, duration=0.5)
        convert_video(src, out, REELS)
        issues = verify_output(out, REELS)
        assert issues == [], f"Unexpected verify issues: {issues}"

    def test_missing_input_raises(self, tmp_path):
        with pytest.raises(FatalError, match="not found"):
            convert_video(str(tmp_path / "nope.mp4"), str(tmp_path / "out.mp4"), REELS)
