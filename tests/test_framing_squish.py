"""Tests for display-aspect framing (squish-proof) in brand_pass + the
forensic side-blur audit.

These pin down the two independent ways a clip used to get "bóp ảnh" (squished
into a narrow center with blurred side bars), plus the false-positive that
caused it:

- _detect_side_blur ran UNCONDITIONALLY and false-positived on already-9:16 UGC
  with soft side edges -> cropped into real content -> narrowed strip -> blur-pad.
  FIX: it is now opt-in (detect_baked_padding=False by default) and never runs.
- anamorphic pixels (SAR != 1:1) have a CODED aspect that can read as 9:16
  (e.g. 720x1280 SAR40:33 -> coded 0.5625) and the old code declared setsar=1
  without resampling -> horizontal squish. FIX: route on the TRUE display aspect
  (_ffprobe_display_dims) and always un-anamorph to square pixels first.
- portrait-via-rotation metadata (coded landscape 1920x1080 + rot90) used to
  mis-route because coded dims and ffprobe DAR both ignore rotation. FIX:
  _ffprobe_display_dims replicates ffmpeg's autorotate (swaps dims AND SAR).

Group A: _ffprobe_display_dims pure-function matrix (monkeypatched ffprobe JSON).
Group B: routing + a full brand_pass_video render on real synthetic sources.
Group C: scripts/audit_sidebar_blur.py forensic detector.

All fixtures are synthetic (lavfi) — no network, fast encodes.
"""

import importlib.util
import json
import os
import subprocess
import types

import pytest

import pipeline.brand_pass as bp
from pipeline.brand_pass import (
    _ffprobe_display_dims,
    _ffprobe_dims,
    _is_target_aspect,
    brand_pass_video,
)

FPS = 12


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _canned_run(stream: dict):
    """Return a fake subprocess.run that yields {"streams":[stream]} as JSON."""
    payload = json.dumps({"streams": [stream]})

    def _run(*_a, **_k):
        return types.SimpleNamespace(stdout=payload, stderr="", returncode=0)

    return _run


def _src(path, size, dur=1.0, sar=None, src="testsrc2"):
    """Encode a tiny synthetic clip with a sine audio track (so the music-only
    path of brand_pass_video has audio to work with)."""
    vf = f"setsar={sar}" if sar else "setsar=1"
    cmd = ["ffmpeg", "-y", "-v", "error",
           "-f", "lavfi", "-i", f"{src}=size={size}:rate={FPS}",
           "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100",
           "-t", f"{dur}", "-vf", vf,
           "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30",
           "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)]
    subprocess.run(cmd, check=True, capture_output=True)
    return str(path)


def _load_audit():
    """Import scripts/audit_sidebar_blur.py by path (it is a script, not a pkg)."""
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    fp = os.path.join(here, "scripts", "audit_sidebar_blur.py")
    spec = importlib.util.spec_from_file_location("audit_sidebar_blur", fp)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_squish(path, side_blur="boxblur=20:5"):
    """1080x1920 with a SHARP narrow center column and BLURRED L/R bars."""
    fc = (f"[0:v]split=2[a][b];[a]{side_blur}[bg];"
          f"[b]crop=600:1920:240:0[fg];[bg][fg]overlay=240:0,format=yuv420p[v]")
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", f"testsrc2=size=1080x1920:rate={FPS}", "-t", "1",
         "-filter_complex", fc, "-map", "[v]",
         "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
         "-pix_fmt", "yuv420p", str(path)],
        check=True, capture_output=True)
    return str(path)


def _make_toppad(path):
    """1080x1920 with SHARP full-width center and BLURRED top/bottom bars (the
    INTENDED 1:1->9:16 pad look). Must NOT be flagged."""
    fc = ("[0:v]split=2[a][b];[a]boxblur=20:5[bg];"
          "[b]crop=1080:1080:0:420[fg];[bg][fg]overlay=0:420,format=yuv420p[v]")
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", f"testsrc2=size=1080x1920:rate={FPS}", "-t", "1",
         "-filter_complex", fc, "-map", "[v]",
         "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
         "-pix_fmt", "yuv420p", str(path)],
        check=True, capture_output=True)
    return str(path)


# ===========================================================================
# Group A — _ffprobe_display_dims (pure, monkeypatched ffprobe JSON)
# ===========================================================================
class TestDisplayDims:
    def test_normal_9x16(self, monkeypatch):
        monkeypatch.setattr(bp.subprocess, "run", _canned_run(
            {"width": 1080, "height": 1920, "sample_aspect_ratio": "1:1"}))
        assert _ffprobe_display_dims("x") == (1080, 1920)

    def test_anamorphic_is_the_squish_trap(self, monkeypatch):
        # coded 720x1280 reads as EXACTLY 9:16 (0.5625) but display is 0.681.
        monkeypatch.setattr(bp.subprocess, "run", _canned_run(
            {"width": 720, "height": 1280, "sample_aspect_ratio": "40:33"}))
        dw, dh = _ffprobe_display_dims("x")
        assert (dw, dh) == (872, 1280)          # 720*40/33 = 872.7 -> 872 (even)
        assert _is_target_aspect(dw, dh) is False    # -> PAD, not cover-squish
        # coded dims WOULD have falsely picked COVER:
        assert _is_target_aspect(720, 1280) is True

    @pytest.mark.parametrize("rot", [90, 270, -90])
    def test_rotation_metadata_swaps_to_portrait(self, monkeypatch, rot):
        monkeypatch.setattr(bp.subprocess, "run", _canned_run(
            {"width": 1920, "height": 1080, "sample_aspect_ratio": "1:1",
             "side_data_list": [{"rotation": rot}]}))
        dw, dh = _ffprobe_display_dims("x")
        assert (dw, dh) == (1080, 1920)
        assert _is_target_aspect(dw, dh) is True     # rotated portrait -> COVER

    def test_anamorphic_plus_rotation_swaps_dims_and_sar(self, monkeypatch):
        monkeypatch.setattr(bp.subprocess, "run", _canned_run(
            {"width": 720, "height": 1280, "sample_aspect_ratio": "40:33",
             "side_data_list": [{"rotation": 90}]}))
        # post-rotate: dims 1280x720, SAR 33:40 -> 1280*33/40 = 1056
        assert _ffprobe_display_dims("x") == (1056, 720)

    @pytest.mark.parametrize("sar", ["0:1", "0:0", "N/A", None])
    def test_degenerate_sar_treated_square(self, monkeypatch, sar):
        st = {"width": 1080, "height": 1920}
        if sar is not None:
            st["sample_aspect_ratio"] = sar
        monkeypatch.setattr(bp.subprocess, "run", _canned_run(st))
        assert _ffprobe_display_dims("x") == (1080, 1920)

    def test_odd_dims_even_clamped(self, monkeypatch):
        monkeypatch.setattr(bp.subprocess, "run", _canned_run(
            {"width": 1081, "height": 1921, "sample_aspect_ratio": "1:1"}))
        assert _ffprobe_display_dims("x") == (1080, 1920)

    def test_tiny_degenerate_clamped_to_min_2(self, monkeypatch):
        monkeypatch.setattr(bp.subprocess, "run", _canned_run(
            {"width": 1, "height": 1, "sample_aspect_ratio": "1:1"}))
        assert _ffprobe_display_dims("x") == (2, 2)

    def test_no_side_data_no_transpose(self, monkeypatch):
        monkeypatch.setattr(bp.subprocess, "run", _canned_run(
            {"width": 1080, "height": 1920, "sample_aspect_ratio": "1:1"}))
        assert _ffprobe_display_dims("x") == (1080, 1920)


# ===========================================================================
# Group B — routing + full render on real synthetic sources (real ffmpeg)
# ===========================================================================
class TestRoutingReal:
    def test_real_anamorphic_routes_pad(self, tmp_path):
        s = _src(tmp_path / "anam.mp4", "720x1280", sar="40/33")
        assert _ffprobe_dims(s) == (720, 1280)        # coded reads 9:16
        dw, dh = _ffprobe_display_dims(s)             # but display is wider
        assert dw > dh * (9 / 16) * 1.05
        assert _is_target_aspect(dw, dh) is False     # -> PAD (no squish)

    def test_real_rotated_portrait_routes_cover(self, tmp_path):
        land = _src(tmp_path / "land.mp4", "1920x1080")
        rot = str(tmp_path / "rot.mp4")
        # -display_rotation is an INPUT option; -c copy keeps the matrix tag.
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-noautorotate",
             "-display_rotation", "90", "-i", land, "-c", "copy", rot],
            check=True, capture_output=True)
        dw, dh = _ffprobe_display_dims(rot)
        assert (dw, dh) == (1080, 1920)               # display upright portrait
        assert _is_target_aspect(dw, dh) is True      # -> COVER, not mis-routed


class TestRenderEndToEnd:
    """Full brand_pass_video on a real anamorphic source (music-only path,
    transcript='' so no Whisper/Demucs). Proves the whole wiring runs and the
    un-anamorph un-squishes; the output must be a clean 1080x1920 with SAR 1:1."""

    def _render(self, tmp_path, src_path, **kw):
        out = str(tmp_path / "out.mp4")
        brand_pass_video(src_path, out, transcript="", watermark_text="QA",
                         outro_duration=0.6, random_seed=7, **kw)
        return out

    def test_anamorphic_renders_square_1080x1920(self, tmp_path):
        s = _src(tmp_path / "anam.mp4", "720x1280", dur=1.5, sar="40/33")
        out = self._render(tmp_path, s)
        # output is exactly the canvas, square pixels, even dims
        info = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height,sample_aspect_ratio",
             "-of", "csv=p=0", out], capture_output=True, text=True).stdout.strip()
        assert info.startswith("1080,1920")
        assert info.endswith("1:1")
        # and the squish-audit sees no LEFT/RIGHT blur bars on the result
        audit = _load_audit()
        flagged, _reason = audit.audit_file(out)
        assert flagged is False

    def test_default_never_calls_side_blur_detector(self, tmp_path, monkeypatch):
        s = _src(tmp_path / "v916.mp4", "720x1280", dur=1.2)
        calls = []
        monkeypatch.setattr(bp, "_detect_side_blur",
                            lambda *a, **k: calls.append(a) or None)
        monkeypatch.setattr(bp, "_detect_content_crop",
                            lambda *a, **k: calls.append(a) or None)
        self._render(tmp_path, s)                      # default detect_baked_padding=False
        assert calls == []                             # detectors NEVER invoked

    def test_opt_in_calls_side_blur_detector(self, tmp_path, monkeypatch):
        s = _src(tmp_path / "v916.mp4", "720x1280", dur=1.2)
        seen = {"n": 0}
        monkeypatch.setattr(bp, "_detect_side_blur",
                            lambda *a, **k: seen.update(n=seen["n"] + 1) or None)
        self._render(tmp_path, s, detect_baked_padding=True)
        assert seen["n"] == 1                           # invoked exactly once


# ===========================================================================
# Group C — scripts/audit_sidebar_blur.py forensic detector
# ===========================================================================
class TestAudit:
    def test_squish_flagged(self, tmp_path):
        audit = _load_audit()
        f = _make_squish(tmp_path / "squish.mp4")
        flagged, reason = audit.audit_file(f)
        assert flagged is True, reason

    def test_fullbleed_clean(self, tmp_path):
        audit = _load_audit()
        f = _src(tmp_path / "full.mp4", "1080x1920", dur=1.0)
        flagged, reason = audit.audit_file(f)
        assert flagged is False, reason

    def test_toppad_clean(self, tmp_path):
        audit = _load_audit()
        f = _make_toppad(tmp_path / "toppad.mp4")
        flagged, reason = audit.audit_file(f)
        assert flagged is False, reason          # the NOT-PAD guard must catch this

    def test_directory_exit_codes(self, tmp_path):
        audit = _load_audit()
        good = tmp_path / "good"; good.mkdir()
        _src(good / "a.mp4", "1080x1920", dur=1.0)
        _make_toppad(good / "b.mp4")
        assert audit.main(["audit", str(good)]) == 0          # all clean -> exit 0
        _make_squish(good / "c.mp4")
        assert audit.main(["audit", str(good)]) == 1          # one squish -> exit 1
