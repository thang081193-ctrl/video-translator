"""Tests for end-card detection v2 (reverse frame-matching) and BGM smart-start.

End-card failures these pin down:
- animated competitor cards (pulsing CTA) that freezedetect missed → cut thiếu
- multi-card outros must cut at the FIRST card
- a card filling the whole scan window must be refused (anti-gutting)
- plain content with no card must never be trimmed → cut thừa

BGM smart-start failures:
- tracks with a near-silent intro must start the ad on the loud section
- the rendered bed must be exactly the needed length, wrapping past EOF

All fixtures are synthetic (lavfi) — no network, fast encodes at 240×426/12fps.
"""

import subprocess

import pytest

from pipeline.audio import measure_loudness
from pipeline.brand_pass import (
    _detect_endcard_start,
    _detect_endcard_start_v2,
    _pick_bgm_start,
    _render_bgm_bed,
)
from pipeline.dub.separator import get_audio_duration

SIZE = "240x426"
FPS = 12


def _seg(path, src, dur, vf=None):
    sep = ":" if "=" in src else "="
    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
           "-i", f"{src}{sep}size={SIZE}:rate={FPS}", "-t", f"{dur}"]
    if vf:
        cmd += ["-vf", vf]
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
            "-pix_fmt", "yuv420p", str(path)]
    subprocess.run(cmd, check=True, capture_output=True)
    return str(path)


def _concat(tmp, out, parts):
    lst = tmp / "list.txt"
    lst.write_text("".join(f"file '{p}'\n" for p in parts), encoding="utf-8")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
                    "-i", str(lst), "-c", "copy", str(out)],
                   check=True, capture_output=True)
    return str(out)


def _motion(tmp, name, dur):
    return _seg(tmp / name, "testsrc", dur)


def _card(tmp, name, dur, color="red", box_y=100, extra=""):
    vf = f"drawbox=x=80:y={box_y}:w=80:h=40:color=white:t=fill" + extra
    return _seg(tmp / name, f"color=c={color}", dur, vf=vf)


class TestEndcardV2:
    def test_static_card_detected_at_boundary(self, tmp_path):
        out = _concat(tmp_path, tmp_path / "v.mp4",
                      [_motion(tmp_path, "a.mp4", 12), _card(tmp_path, "b.mp4", 3)])
        cut = _detect_endcard_start(out, 15.0)
        assert cut is not None
        assert 11.0 <= cut <= 12.4

    def test_multi_card_cuts_at_first_card(self, tmp_path):
        out = _concat(tmp_path, tmp_path / "v.mp4",
                      [_motion(tmp_path, "a.mp4", 12),
                       _card(tmp_path, "b.mp4", 2, color="red", box_y=100),
                       _card(tmp_path, "c.mp4", 2, color="blue", box_y=250)])
        cut = _detect_endcard_start(out, 16.0)
        assert cut is not None
        assert 11.0 <= cut <= 12.4

    def test_animated_cta_card_still_detected(self, tmp_path):
        """A pulsing CTA defeats freezedetect — v2 must still catch the card."""
        blink = _card(tmp_path, "b.mp4", 3,
                      extra=":enable='lt(mod(t,1),0.5)'")
        out = _concat(tmp_path, tmp_path / "v.mp4",
                      [_motion(tmp_path, "a.mp4", 12), blink])
        cut = _detect_endcard_start(out, 15.0)
        assert cut is not None
        assert 11.0 <= cut <= 12.4

    def test_no_card_no_trim(self, tmp_path):
        out = _motion(tmp_path, "v.mp4", 15)
        assert _detect_endcard_start(out, 15.0) is None

    def test_card_filling_window_refused(self, tmp_path):
        """Content boundary outside the scan window → refuse rather than gut."""
        out = _concat(tmp_path, tmp_path / "v.mp4",
                      [_motion(tmp_path, "a.mp4", 4), _card(tmp_path, "b.mp4", 10)])
        status, cut = _detect_endcard_start_v2(out, 14.0)
        assert status == "none" and cut is None


def _bgm_track(tmp, intro_s=8.0, body_s=22.0, intro_db=-30, body_db=-3):
    """Sine track with a quiet intro then a loud body (the Pixabay shape)."""
    p = str(tmp / "track.wav")
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={intro_s}",
         "-f", "lavfi", "-i", f"sine=frequency=600:duration={body_s}",
         "-filter_complex",
         f"[0:a]volume={intro_db}dB[a];[1:a]volume={body_db}dB[b];"
         f"[a][b]concat=n=2:v=0:a=1[out]",
         "-map", "[out]", "-c:a", "pcm_s16le", p],
        check=True, capture_output=True)
    return p


class TestBgmSmartStart:
    def test_quiet_intro_skipped(self, tmp_path):
        track = _bgm_track(tmp_path)
        off, info = _pick_bgm_start(track, 15.0)
        assert 7.0 <= off <= 10.0
        assert info["score_db"] > info["intro_db"] + 10

    def test_uniform_track_keeps_natural_start(self, tmp_path):
        track = _bgm_track(tmp_path, intro_s=0.5, body_s=25.0)
        off, _ = _pick_bgm_start(track, 15.0)
        assert off <= 1.0

    def test_bed_exact_length_with_wraparound(self, tmp_path):
        track = _bgm_track(tmp_path)          # 30 s total
        bed = _render_bgm_bed(track, 25.0, 12.0, str(tmp_path / "bed.wav"))
        assert get_audio_duration(bed) == pytest.approx(12.0, abs=0.3)

    def test_bed_opening_is_loud(self, tmp_path):
        """The whole point: the ad's first seconds must carry the hook."""
        track = _bgm_track(tmp_path)
        off, _ = _pick_bgm_start(track, 15.0)
        bed = _render_bgm_bed(track, off, 15.0, str(tmp_path / "bed.wav"))
        head = str(tmp_path / "head.wav")
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", bed, "-t", "3",
                        "-c:a", "pcm_s16le", head], check=True, capture_output=True)
        m = measure_loudness(head)
        assert m is not None
        # loud body ≈ -24 LUFS; the un-skipped intro would be ≈ -51.
        assert m["input_i"] >= -30.0

    def test_unreadable_track_falls_back_to_zero(self):
        off, info = _pick_bgm_start("__nope__.mp3", 15.0)
        assert off == 0.0 and info == {}
