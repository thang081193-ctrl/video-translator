"""Regression tests for pipeline.dub.mixer — the looping BGM hang.

The custom_bgm path (the DEFAULT for build_dubbed_audio, and reachable from
both `video_translator.py --dub --bgm` and the web app's "Custom BGM" radio)
loops the BGM with `-stream_loop -1`, an INFINITE input. Combined with the old
`amix=duration=longest` that made ffmpeg run forever: a `--dub` run hung and
only died on the 300s subprocess timeout (subprocess.TimeoutExpired).

These tests pin the contract:
  - a looped (infinite) BGM input MUST be bounded to the finite voice track so
    the mix terminates (the fix: amix duration=first);
  - the keep_original_bgm path (finite BGM, loop_bgm=False) keeps
    duration=longest so trailing original music plays through non-speech tails.

The fast tests mock ffmpeg and assert the generated command is bounded — that
is what the prior tests missed (they mocked build_dubbed_audio wholesale and
never inspected the mix argv). The final test runs real ffmpeg and proves the
looped mix actually terminates.
"""

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from pipeline.audio import check_ffmpeg
from pipeline.dub.mixer import _mix_voice_and_bgm, build_dubbed_audio
from pipeline.dub.separator import get_audio_duration
from pipeline.errors import FatalError


def _capture_mix_cmd(loop_bgm: bool, audio_mode: str) -> list[str]:
    """Run _mix_voice_and_bgm with ffmpeg mocked; return the MIX ffmpeg argv.

    Patching subprocess.run on the mixer module patches the shared subprocess
    module, so the voice-audibility guard's loudness probes (measure_loudness
    in pipeline.audio) hit the same mock — their empty-stderr result makes
    them return None and the guard becomes a no-op. Filter to the one command
    that actually mixes (`-filter_complex` with amix).
    """
    with patch("pipeline.dub.mixer.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        _mix_voice_and_bgm(
            dubbed_raw="voice.wav",
            bgm_source="music.mp3",
            loop_bgm=loop_bgm,
            audio_mode=audio_mode,
            bgm_volume=0.25,
            output_path="out.m4a",
        )
        mix_calls = [
            c for c in mock_run.call_args_list
            if "-filter_complex" in c[0][0]
            and "amix" in c[0][0][c[0][0].index("-filter_complex") + 1]
        ]
        assert len(mix_calls) == 1
        return list(mix_calls[0][0][0])


def _filter_complex(cmd: list[str]) -> str:
    """Pull the -filter_complex argument out of an ffmpeg argv."""
    return cmd[cmd.index("-filter_complex") + 1]


def _is_bounded(cmd: list[str]) -> bool:
    """True if the command cannot outrun the finite voice track.

    Any of these bounds the output: amix tied to the first/shortest input, an
    explicit `-t`, or `-shortest`. `duration=longest` over an infinite
    `-stream_loop -1` input is the unbounded case that hangs.
    """
    fc = _filter_complex(cmd)
    if "duration=first" in fc or "duration=shortest" in fc:
        return True
    return "-shortest" in cmd or "-t" in cmd


class TestMixCommandBounded:
    """The generated mix command must terminate (no infinite amix)."""

    def test_looped_custom_bgm_is_bounded(self):
        """custom_bgm loops BGM (`-stream_loop -1` = infinite) → must be bounded."""
        cmd = _capture_mix_cmd(loop_bgm=True, audio_mode="custom_bgm")
        # Precondition: the loop that makes the BGM input infinite is present.
        assert "-stream_loop" in cmd and "-1" in cmd
        # The bug: unbounded `longest` over an infinite input runs to the timeout.
        assert _is_bounded(cmd), (
            f"looped BGM mix is unbounded and will hang: {_filter_complex(cmd)!r}"
        )

    def test_looped_custom_bgm_uses_duration_first(self):
        """Pins the chosen fix: bound the mix to the finite voice track [0]."""
        fc = _filter_complex(_capture_mix_cmd(loop_bgm=True, audio_mode="custom_bgm"))
        assert "duration=first" in fc
        assert "duration=longest" not in fc

    def test_keep_original_bgm_not_looped_and_longest(self):
        """keep_original_bgm: finite BGM, no `-stream_loop`, longest preserved so
        trailing original music plays through non-speech tails."""
        cmd = _capture_mix_cmd(loop_bgm=False, audio_mode="keep_original_bgm")
        assert "-stream_loop" not in cmd
        fc = _filter_complex(cmd)
        assert "duration=longest" in fc
        # longest is safe here: both inputs are finite, so the mix still ends.
        assert "duration=first" not in fc


class TestBuildDubbedAudioWiring:
    """Pin the trigger: the DEFAULT custom_bgm mode is the one that loops."""

    @patch("pipeline.dub.mixer._mix_voice_and_bgm")
    @patch("pipeline.dub.mixer._ffmpeg_concat", return_value="dubbed_raw.wav")
    @patch("pipeline.dub.mixer._collect_concat_pieces", return_value=["a.wav"])
    @patch(
        "pipeline.dub.mixer._generate_tts_pieces",
        return_value=[(0.0, 1.0, "a.wav")],
    )
    @patch("pipeline.dub.mixer.check_ffmpeg")
    def test_default_custom_bgm_loops_bgm(
        self, _ck, _tts, _concat, _ffc, mock_mix, tmp_path
    ):
        """audio_mode defaults to custom_bgm → _mix is called with loop_bgm=True."""
        bgm = tmp_path / "music.mp3"
        bgm.write_bytes(b"fake-bgm")  # only existence is checked here
        out = tmp_path / "out.m4a"

        build_dubbed_audio(
            segments=[{"start": 0.0, "end": 1.0, "translated_text": "hi"}],
            lang="fr",
            output_path=str(out),
            output_dir=str(tmp_path),
            bgm_path=str(bgm),
            # audio_mode omitted on purpose — exercises the "custom_bgm" default.
        )

        assert mock_mix.call_count == 1
        assert mock_mix.call_args.kwargs["loop_bgm"] is True
        assert mock_mix.call_args.kwargs["audio_mode"] == "custom_bgm"


class TestMixActuallyTerminates:
    """The true regression: run real ffmpeg and prove the looped mix ends."""

    @staticmethod
    def _make_wav(path, seconds, freq):
        subprocess.run(
            [
                "ffmpeg", "-y", "-f", "lavfi",
                "-i", f"sine=frequency={freq}:duration={seconds}",
                "-ac", "2", "-ar", "44100", "-c:a", "pcm_s16le", str(path),
            ],
            capture_output=True, check=True, timeout=30,
        )

    def test_looped_custom_bgm_terminates(self, tmp_path):
        try:
            check_ffmpeg()
        except FatalError as e:
            pytest.skip(f"ffmpeg/ffprobe not installed: {e}")

        voice = tmp_path / "voice.wav"
        bgm = tmp_path / "bgm.wav"
        out = tmp_path / "out.m4a"
        self._make_wav(voice, seconds=3, freq=220)  # finite 3s voice (input [0])
        self._make_wav(bgm, seconds=1, freq=440)     # 1s BGM → must loop to fill

        real_run = subprocess.run

        def capped_run(*args, **kwargs):
            # Force a short timeout so a regression (infinite mix) fails in ~15s
            # instead of hanging on the real 300s default.
            kwargs["timeout"] = 15
            return real_run(*args, **kwargs)

        # Must NOT raise subprocess.TimeoutExpired — that is the hang regressing.
        with patch("pipeline.dub.mixer.subprocess.run", side_effect=capped_run):
            _mix_voice_and_bgm(
                dubbed_raw=str(voice),
                bgm_source=str(bgm),
                loop_bgm=True,
                audio_mode="custom_bgm",
                bgm_volume=0.25,
                output_path=str(out),
            )

        assert out.exists() and out.stat().st_size > 0
        # Output is bounded to the 3s voice, not the endlessly-looped BGM.
        dur = get_audio_duration(str(out))
        assert dur < 6.0, f"mix ran long ({dur:.1f}s) — not bounded to the voice"
