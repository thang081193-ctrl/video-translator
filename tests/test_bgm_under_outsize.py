"""Tests for the two brand_pass options the Mars-VD 0609 pack needed.

`out_size`      — render to a non-standard canvas (1026x1824) instead of 1080x1920.
`bgm_under_path`— lay a BGM bed UNDER the clip's own audio without touching it,
                  plus `outro_audio` carrying a supplied outro's own sting into
                  the tail (which the `-an` outro normalize would otherwise drop).

All fixtures are synthetic (lavfi) — no network, small encodes.
"""
from __future__ import annotations

import json
import subprocess

import pytest

import pipeline.brand_pass as bp
from pipeline.brand_pass import brand_pass_video

FPS = 12


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _src(path, size="540x960", dur=1.5, freq=440):
    # Fixture levels matter here. A full-scale sine (what lavfi hands you) is
    # clamped ~3 dB by the mix bus `alimiter` and reads as the mixer
    # re-levelling the source; a very quiet one makes the bed -- which IS
    # normalized to a reference -- come out louder than the source. -12 dBFS
    # is roughly where real ad audio sits, so both traps stay shut.
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error",
         "-f", "lavfi", "-i", f"testsrc2=size={size}:rate={FPS}",
         "-f", "lavfi", "-i", f"sine=frequency={freq}:sample_rate=44100",
         "-t", f"{dur}", "-vf", "setsar=1", "-af", "volume=0.25",
         "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30",
         "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)],
        check=True, capture_output=True)
    return str(path)


def _track(path, dur=6.0, freq=180):
    """A standalone audio track to use as the BGM bed."""
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error",
         "-f", "lavfi", "-i", f"sine=frequency={freq}:sample_rate=44100",
         # a normal master level, so the bed's normalize gain is not clamped
         "-t", f"{dur}", "-af", "volume=0.5",
         "-c:a", "libmp3lame", "-b:a", "128k", str(path)],
        check=True, capture_output=True)
    return str(path)


def _outro(path, dur=1.0, with_audio=True):
    cmd = ["ffmpeg", "-y", "-v", "error",
           "-f", "lavfi", "-i", f"color=c=navy:size=360x640:rate={FPS}"]
    if with_audio:
        cmd += ["-f", "lavfi", "-i", "sine=frequency=880:sample_rate=44100"]
    cmd += ["-t", f"{dur}", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30",
            "-pix_fmt", "yuv420p"]
    if with_audio:
        cmd += ["-af", "volume=0.4"]
    cmd += (["-c:a", "aac", "-shortest"] if with_audio else ["-an"])
    cmd += [str(path)]
    subprocess.run(cmd, check=True, capture_output=True)
    return str(path)


def _probe(path):
    j = json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", path],
        capture_output=True, text=True, check=True).stdout)
    v = next(s for s in j["streams"] if s["codec_type"] == "video")
    a = next((s for s in j["streams"] if s["codec_type"] == "audio"), None)
    return v, a, float(j["format"]["duration"])


def _rms_db(path, ss, t):
    """Mean RMS of a slice, in dBFS. -inf-ish (<= -80) means silence."""
    import math
    import struct
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", str(ss), "-t", str(t), "-i", path,
         "-ac", "1", "-ar", "8000", "-f", "s16le", "-"],
        capture_output=True).stdout
    n = len(raw) // 2
    if n == 0:
        return -120.0
    vals = struct.unpack(f"<{n}h", raw[: n * 2])
    mean_sq = sum(v * v for v in vals) / n
    return 20 * math.log10(math.sqrt(mean_sq) / 32768 + 1e-9)


# ===========================================================================
# out_size
# ===========================================================================
class TestOutSize:
    def test_default_is_1080x1920(self, tmp_path):
        out = tmp_path / "o.mp4"
        brand_pass_video(input_path=_src(tmp_path / "s.mp4"), output_path=str(out),
                         transcript="", watermark_text="", random_seed=1)
        v, _, _ = _probe(str(out))
        assert (v["width"], v["height"]) == (1080, 1920)

    def test_custom_canvas_is_honoured(self, tmp_path):
        out = tmp_path / "o.mp4"
        brand_pass_video(input_path=_src(tmp_path / "s.mp4"), output_path=str(out),
                         transcript="", watermark_text="", out_size=(1026, 1824),
                         random_seed=1)
        v, _, _ = _probe(str(out))
        assert (v["width"], v["height"]) == (1026, 1824)
        assert v.get("sample_aspect_ratio", "1:1") in ("1:1", "N/A", None)

    def test_custom_canvas_applies_to_the_outro_too(self, tmp_path):
        """The outro is a separate encode concatenated onto the body — if it kept
        the module default the concat would fail or letterbox."""
        out = tmp_path / "o.mp4"
        brand_pass_video(input_path=_src(tmp_path / "s.mp4", dur=1.0),
                         output_path=str(out), transcript="", watermark_text="",
                         outro_video=_outro(tmp_path / "otr.mp4"),
                         out_size=(1026, 1824), random_seed=1)
        v, _, dur = _probe(str(out))
        assert (v["width"], v["height"]) == (1026, 1824)
        assert dur == pytest.approx(2.0, abs=0.3)   # 1.0 body + 1.0 outro

    def test_non_9x16_canvas_routes_pad_not_cover(self, tmp_path):
        """A 4:5 canvas must re-route a 9:16 source to the blur-pad branch —
        proof the target aspect really comes from out_size, not the constant."""
        out = tmp_path / "o.mp4"
        brand_pass_video(input_path=_src(tmp_path / "s.mp4", size="540x960"),
                         output_path=str(out), transcript="", watermark_text="",
                         out_size=(1080, 1350), random_seed=1)
        v, _, _ = _probe(str(out))
        assert (v["width"], v["height"]) == (1080, 1350)

    @pytest.mark.parametrize("size", [(1025, 1824), (1026, 1823), (0, 1824), (-2, 100)])
    def test_odd_or_nonpositive_rejected(self, tmp_path, size):
        with pytest.raises(ValueError, match="even"):
            brand_pass_video(input_path=_src(tmp_path / "s.mp4"),
                             output_path=str(tmp_path / "o.mp4"),
                             transcript="", watermark_text="", out_size=size)


# ===========================================================================
# bgm_under_path
# ===========================================================================
class TestBgmUnder:
    def test_source_audio_survives_unchanged(self, tmp_path):
        """The whole point of `under`: the clip's own audio is NOT re-levelled.

        Compared against the plain music-only PASSTHROUGH render, not the raw
        source file — passthrough already goes through extract -> limiter -> AAC,
        and measuring against the source would be testing that chain rather than
        this one. With the bed at a negligible gain the two must be the same mix.
        """
        src = _src(tmp_path / "s.mp4", dur=2.0)
        plain, bedded = tmp_path / "plain.mp4", tmp_path / "bedded.mp4"
        brand_pass_video(input_path=src, output_path=str(plain), transcript="",
                         watermark_text="", random_seed=7)
        brand_pass_video(input_path=src, output_path=str(bedded), transcript="",
                         watermark_text="", bgm_under_path=_track(tmp_path / "b.mp3"),
                         bgm_under_gain=0.01, random_seed=7)
        assert _rms_db(str(bedded), 0.2, 1.2) == pytest.approx(
            _rms_db(str(plain), 0.2, 1.2), abs=0.8)

    def test_bed_is_present_and_below_the_source(self, tmp_path):
        """Louder bed => louder mix, but still under the source."""
        src = _src(tmp_path / "s.mp4", dur=2.0)
        quiet, loud = tmp_path / "q.mp4", tmp_path / "l.mp4"
        bgm = _track(tmp_path / "b.mp3")
        for out, gain in ((quiet, 0.05), (loud, 0.9)):
            brand_pass_video(input_path=src, output_path=str(out), transcript="",
                             watermark_text="", bgm_under_path=bgm,
                             bgm_under_gain=gain, random_seed=7)
        assert _rms_db(str(loud), 0.2, 1.2) > _rms_db(str(quiet), 0.2, 1.2) + 0.5

    def test_outro_tail_is_not_silent(self, tmp_path):
        """Without the bed (and the recovered outro audio) the tail under the
        brand card is pure silence — the defect this path exists to avoid."""
        out = tmp_path / "o.mp4"
        brand_pass_video(input_path=_src(tmp_path / "s.mp4", dur=1.5),
                         output_path=str(out), transcript="", watermark_text="",
                         bgm_under_path=_track(tmp_path / "b.mp3"),
                         outro_video=_outro(tmp_path / "otr.mp4", dur=1.0),
                         random_seed=7)
        assert _rms_db(str(out), 1.7, 0.7) > -60.0

    def test_outro_audio_false_leaves_only_the_bed(self, tmp_path):
        """outro_audio=False must drop the sting; the bed alone still plays, so
        the tail is quieter than with the sting mixed in."""
        src = _src(tmp_path / "s.mp4", dur=1.5)
        bgm = _track(tmp_path / "b.mp3")
        otr = _outro(tmp_path / "otr.mp4", dur=1.0)
        on, off = tmp_path / "on.mp4", tmp_path / "off.mp4"
        for out, flag in ((on, True), (off, False)):
            brand_pass_video(input_path=src, output_path=str(out), transcript="",
                             watermark_text="", bgm_under_path=bgm, outro_audio=flag,
                             outro_video=otr, random_seed=7)
        assert _rms_db(str(on), 1.7, 0.7) > _rms_db(str(off), 1.7, 0.7) + 1.0

    def test_silent_outro_video_is_handled(self, tmp_path):
        """An outro with no audio stream must not break the mix."""
        out = tmp_path / "o.mp4"
        brand_pass_video(input_path=_src(tmp_path / "s.mp4", dur=1.5),
                         output_path=str(out), transcript="", watermark_text="",
                         bgm_under_path=_track(tmp_path / "b.mp3"),
                         outro_video=_outro(tmp_path / "otr.mp4", with_audio=False),
                         random_seed=7)
        _, a, _ = _probe(str(out))
        assert a is not None

    def test_no_demucs_on_the_under_path(self, tmp_path, monkeypatch):
        """`under` keeps the source audio whole — separating it would be both
        wasted GPU time and a change to the audio we promised not to touch."""
        called = []
        monkeypatch.setattr(bp, "separate_audio",
                            lambda *a, **k: called.append(1) or {})
        brand_pass_video(input_path=_src(tmp_path / "s.mp4"),
                         output_path=str(tmp_path / "o.mp4"), transcript="",
                         watermark_text="", bgm_under_path=_track(tmp_path / "b.mp3"),
                         random_seed=7)
        assert called == []

    def test_rejects_combination_with_bgm_replace(self, tmp_path):
        with pytest.raises(ValueError, match="mutually exclusive"):
            brand_pass_video(input_path=_src(tmp_path / "s.mp4"),
                             output_path=str(tmp_path / "o.mp4"), transcript="",
                             watermark_text="", bgm_under_path=_track(tmp_path / "b.mp3"),
                             bgm_replace_path=_track(tmp_path / "b2.mp3", freq=300))

    def test_rejects_voice_path(self, tmp_path):
        """With a transcript the measured voice mixer owns the bed; silently
        ignoring the bed there would ship ads missing their music."""
        with pytest.raises(ValueError, match="music-path only"):
            brand_pass_video(input_path=_src(tmp_path / "s.mp4"),
                             output_path=str(tmp_path / "o.mp4"),
                             transcript="hello there", watermark_text="",
                             bgm_under_path=_track(tmp_path / "b.mp3"))

    def test_missing_track_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            brand_pass_video(input_path=_src(tmp_path / "s.mp4"),
                             output_path=str(tmp_path / "o.mp4"), transcript="",
                             watermark_text="", bgm_under_path=str(tmp_path / "nope.mp3"))
