"""Tests for pipeline/convert.py — Reels preset, max-content layout, ffmpeg integration.

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
    FEED,
    REELS,
    PRESETS,
    Preset,
    _build_video_filter,
    _fit_to_canvas,
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

    def test_feed_in_presets(self):
        assert "feed" in PRESETS
        assert PRESETS["feed"] is FEED

    def test_reels_dimensions(self):
        assert REELS.width == 1080
        assert REELS.height == 1920
        assert REELS.fps == 30
        assert REELS.fit_mode == "blur"

    def test_feed_dimensions(self):
        assert FEED.width == 1080
        assert FEED.height == 1350  # 4:5
        assert FEED.fps == 30
        assert FEED.fit_mode == "cover"

    def test_list_presets_ui_shape(self):
        presets = list_presets()
        keys = {p["key"] for p in presets}
        assert {"reels", "feed"} <= keys
        for p in presets:
            assert "label" in p
            assert p["width"] == 1080


# ─── Layout: _fit_to_canvas (max-content) ────────────────────────────────────

class TestFitToCanvas:
    def test_landscape_16_9_fits_to_canvas_width(self):
        """16:9 source → fg width = 1080 (full canvas), centered vertically."""
        fg_w, fg_h, x_off, y_off = _fit_to_canvas(1920, 1080, REELS)
        assert fg_w == 1080
        # 1080 / (16/9) = 607.5 → snap-even → 606 or 608
        assert fg_h in (606, 608)
        assert x_off == 0
        # y_off centers content vertically (no upward bias).
        assert y_off == (REELS.height - fg_h) // 2

    def test_portrait_9_16_aspect_match_fills_canvas(self):
        """Source already 9:16 → fg fills canvas, no blur padding."""
        fg_w, fg_h, x_off, y_off = _fit_to_canvas(720, 1280, REELS)
        assert (fg_w, fg_h) == (REELS.width, REELS.height)
        assert (x_off, y_off) == (0, 0)

    def test_square_1_1_fits_to_canvas_width(self):
        """1:1 source — wider than 9:16, fit to canvas width 1080."""
        fg_w, fg_h, x_off, y_off = _fit_to_canvas(1080, 1080, REELS)
        assert fg_w == 1080
        assert fg_h == 1080  # square preserved at canvas width
        assert x_off == 0
        # Centered vertically, blur fills 420px top + 420px bottom.
        assert y_off == (REELS.height - fg_h) // 2

    def test_portrait_4_5_fits_to_canvas_width(self):
        """4:5 source (720×900) — taller than 1:1 but narrower than 9:16,
        fit to full canvas width 1080. Old safe-zone code would have shrunk
        this into 980px height — new max-content fills with 1350px height."""
        fg_w, fg_h, x_off, y_off = _fit_to_canvas(720, 900, REELS)
        assert fg_w == 1080
        assert fg_h == 1350  # 1080 / (720/900)
        assert x_off == 0
        assert y_off == (REELS.height - fg_h) // 2  # 285

    def test_dimensions_always_even(self):
        """yuv420p needs even dimensions — composer must snap to even."""
        for src in [(1920, 1080), (1280, 720), (1081, 1921), (1080, 1080), (3840, 2160)]:
            fg_w, fg_h, _, _ = _fit_to_canvas(src[0], src[1], REELS)
            assert fg_w % 2 == 0, f"fg_w={fg_w} for src={src} is odd"
            assert fg_h % 2 == 0, f"fg_h={fg_h} for src={src} is odd"

    def test_zero_dimensions_raises(self):
        with pytest.raises(FatalError, match="Invalid source dimensions"):
            _fit_to_canvas(0, 1080, REELS)
        with pytest.raises(FatalError, match="Invalid source dimensions"):
            _fit_to_canvas(1920, 0, REELS)


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

    def test_landscape_overlay_y_centers_content(self):
        """Max-content layout centers fg vertically — no safe-zone bias."""
        f = _build_video_filter(1920, 1080, REELS)
        import re
        m = re.search(r"overlay=(\d+):(\d+)", f)
        assert m, f"no overlay xy in filter: {f}"
        x, y = int(m.group(1)), int(m.group(2))
        # fg height ~607, so y should be ~(1920 - 607) / 2 ≈ 656
        canvas_center_y = REELS.height // 2
        assert abs(y - (canvas_center_y - 303)) < 5, \
            f"y={y} not centered (expected ~656)"

    def test_feed_aspect_match_simple_scale(self):
        """4:5 source → simple scale, no crop, no blur (FEED preset)."""
        f = _build_video_filter(720, 900, FEED)
        assert f == "[0:v]scale=1080:1350[vout]"

    def test_feed_9_16_source_uses_cover_crop(self):
        """9:16 source into 4:5 FEED → scale-to-cover + center-crop, no blur."""
        f = _build_video_filter(720, 1280, FEED)
        # Cover-mode filter is single-pass — no [bg]/[fg] overlay, no boxblur.
        assert "boxblur" not in f, "Feed cover mode must not blur"
        assert "[fg]" not in f and "[bg]" not in f, "Feed must not letterbox"
        assert "force_original_aspect_ratio=increase" in f
        assert "crop=1080:1350" in f
        assert f.endswith("[vout]")

    def test_feed_landscape_source_also_covers(self):
        """16:9 source into 4:5 FEED → still cover mode (crops sides)."""
        f = _build_video_filter(1920, 1080, FEED)
        assert "boxblur" not in f
        assert "crop=1080:1350" in f


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
