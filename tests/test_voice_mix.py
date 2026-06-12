"""Tests for the measured voice-first mix (brand_pass) and loudness helper.

The failure these protect against: a hot royalty-free BGM master (-9 LUFS)
mixed under a quiet Demucs/TTS voice stem (-25..-30 LUFS) with a blind gain
multiplier — the narration drowns. The measured mix must rescue the voice in
that worst case, fall back gracefully on voiceless stems, and hard-fail the
QA gate instead of shipping a broken mix.

All fixtures are synthetic lavfi tones — no network, no Demucs, no GPU.
Voice proxy = 1 kHz sine, BGM proxy = 150 Hz sine: far enough apart that
cascaded high/low-pass filters (24 dB/oct at 400 Hz) isolate each stem in the
mixed output, so the realized voice-over-BGM margin is directly measurable.
"""

import subprocess
from unittest.mock import patch

import pytest

import pipeline.brand_pass as bp
from pipeline.audio import measure_loudness
from pipeline.brand_pass import (
    MIX_FINAL_RANGE,
    _clamp,
    _mix_voice_over_bgm,
)
from pipeline.dub.mixer import _mix_voice_and_bgm
from pipeline.errors import DegradedError

VOICE_FREQ = 1000
BGM_FREQ = 150
BAND_SPLIT = 400


def _tone(path, freq, dur, vol_db):
    """Generate a stereo PCM tone at `vol_db` relative to lavfi sine base."""
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi",
         "-i", f"sine=frequency={freq}:sample_rate=44100:duration={dur}",
         "-af", f"volume={vol_db}dB", "-ac", "2", "-c:a", "pcm_s16le", str(path)],
        check=True, capture_output=True,
    )
    return str(path)


def _silence(path, dur):
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi",
         "-i", f"anullsrc=r=44100:cl=stereo:duration={dur}",
         "-c:a", "pcm_s16le", str(path)],
        check=True, capture_output=True,
    )
    return str(path)


def _band_lufs(mixed, band, work_dir):
    """Integrated LUFS of the low (BGM) or high (voice) band of a mix."""
    filt = (f"lowpass=f={BAND_SPLIT},lowpass=f={BAND_SPLIT}" if band == "low"
            else f"highpass=f={BAND_SPLIT},highpass=f={BAND_SPLIT}")
    out = str(work_dir / f"band_{band}.wav")
    subprocess.run(
        ["ffmpeg", "-y", "-i", mixed, "-af", filt, "-c:a", "pcm_s16le", out],
        check=True, capture_output=True,
    )
    meas = measure_loudness(out)
    assert meas is not None
    return meas["input_i"]


class TestMeasureLoudness:
    def test_relative_levels(self, tmp_path):
        quiet = measure_loudness(_tone(tmp_path / "q.wav", 900, 5, -20))
        loud = measure_loudness(_tone(tmp_path / "l.wav", 900, 5, -5))
        assert quiet is not None and loud is not None
        assert loud["input_i"] - quiet["input_i"] == pytest.approx(15.0, abs=1.5)

    def test_missing_file_returns_none(self):
        assert measure_loudness("__does_not_exist__.wav") is None

    def test_video_without_audio_returns_none(self, tmp_path):
        mute = str(tmp_path / "mute.mp4")
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=black:s=64x64:d=1",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", mute],
            check=True, capture_output=True,
        )
        assert measure_loudness(mute) is None

    def test_digital_silence_maps_to_floor(self, tmp_path):
        meas = measure_loudness(_silence(tmp_path / "s.wav", 3))
        assert meas is not None
        assert meas["input_i"] == -99.0


class TestClamp:
    def test_clamp(self):
        assert _clamp(5, 0, 10) == 5
        assert _clamp(-5, 0, 10) == 0
        assert _clamp(15, 0, 10) == 10


class TestMeasuredVoiceMix:
    def test_rescues_voice_drowned_by_hot_bgm(self, tmp_path):
        """Worst case: BGM master ~19 dB hotter than the voice stem."""
        voice = _tone(tmp_path / "voice.wav", VOICE_FREQ, 12, -10)  # ≈ -31 LUFS
        bgm = _tone(tmp_path / "bgm.wav", BGM_FREQ, 30, +9)         # ≈ -12 LUFS
        mixed = str(tmp_path / "mixed.m4a")

        qa = _mix_voice_over_bgm(bgm, voice, 12.0, margin_db=12.0,
                                 mixed_audio=mixed)

        assert qa["verdict"] == "MEASURED"
        assert MIX_FINAL_RANGE[0] <= qa["final_i"] <= MIX_FINAL_RANGE[1]
        voice_band = _band_lufs(mixed, "high", tmp_path)
        bgm_band = _band_lufs(mixed, "low", tmp_path)
        # 12 dB margin requested; ≥8 dB realized allows filter/K-weight slack.
        assert voice_band - bgm_band >= 8.0

    def test_novoice_stem_not_boosted(self, tmp_path):
        """Forced-voice run on a music-only source: silence stem must NOT be
        amplified into hiss; the mix falls back to music-led levels."""
        voice = _silence(tmp_path / "voice.wav", 8)
        bgm = _tone(tmp_path / "bgm.wav", BGM_FREQ, 30, +9)
        mixed = str(tmp_path / "mixed.m4a")

        qa = _mix_voice_over_bgm(bgm, voice, 8.0, margin_db=12.0,
                                 mixed_audio=mixed)

        assert qa["verdict"] == "NOVOICE_FALLBACK"
        assert qa["final_i"] == pytest.approx(bp.MUSIC_TARGET_LUFS, abs=3.0)

    def test_legacy_mode_preserved(self, tmp_path):
        voice = _tone(tmp_path / "voice.wav", VOICE_FREQ, 5, -10)
        bgm = _tone(tmp_path / "bgm.wav", BGM_FREQ, 10, -5)
        mixed = str(tmp_path / "mixed.m4a")

        qa = _mix_voice_over_bgm(bgm, voice, 5.0, margin_db=12.0,
                                 mixed_audio=mixed, legacy_bgm_vol=0.4)

        assert qa["verdict"] == "LEGACY_UNGATED"
        assert qa["mode"] == "legacy_ratio"
        assert measure_loudness(mixed) is not None

    def test_measurement_failure_raises_degraded(self, tmp_path):
        with patch.object(bp, "measure_loudness", return_value=None):
            with pytest.raises(DegradedError, match="loudness measurement failed"):
                _mix_voice_over_bgm("b.wav", "v.wav", 5.0, margin_db=12.0,
                                    mixed_audio=str(tmp_path / "m.m4a"))

    def test_gate_failure_raises_degraded_after_retry(self, tmp_path):
        """If the post-mix re-measure never lands in range, the file must NOT
        ship silently — DegradedError after exactly one corrective re-mix."""
        voice = _tone(tmp_path / "voice.wav", VOICE_FREQ, 3, -10)
        bgm = _tone(tmp_path / "bgm.wav", BGM_FREQ, 6, -5)
        mixed = str(tmp_path / "mixed.m4a")

        seq = [
            {"input_i": -25.0, "input_tp": -20.0, "input_lra": 1.0, "input_thresh": -35.0},  # voice
            {"input_i": -10.0, "input_tp": -5.0, "input_lra": 1.0, "input_thresh": -20.0},   # bgm
            {"input_i": -35.0, "input_tp": -30.0, "input_lra": 1.0, "input_thresh": -45.0},  # mix #1
            {"input_i": -35.0, "input_tp": -30.0, "input_lra": 1.0, "input_thresh": -45.0},  # mix #2
        ]
        with patch.object(bp, "measure_loudness", side_effect=seq):
            with pytest.raises(DegradedError, match="QA gate failed"):
                _mix_voice_over_bgm(bgm, voice, 3.0, margin_db=12.0,
                                    mixed_audio=mixed)


class TestDubMixerGuard:
    def test_guard_cuts_hot_bgm(self, tmp_path):
        """Dub path: quiet TTS + hot BGM under legacy slider gains must still
        come out voice-dominant thanks to the measured margin guard."""
        voice = _tone(tmp_path / "tts.wav", VOICE_FREQ, 10, -25)  # quiet TTS
        bgm = _tone(tmp_path / "bgm.wav", BGM_FREQ, 30, +9)       # hot master
        out = str(tmp_path / "dubbed.m4a")

        _mix_voice_and_bgm(voice, bgm, loop_bgm=True, audio_mode="custom_bgm",
                           bgm_volume=0.25, output_path=out)

        voice_band = _band_lufs(out, "high", tmp_path)
        bgm_band = _band_lufs(out, "low", tmp_path)
        assert voice_band - bgm_band >= 5.0
