"""Audio extraction edge cases — mocked subprocess for fast tests.

Documents current behavior of has_audio_track silent failure mode.
TODO (future phase): has_audio_track should raise FatalError on ffprobe error
instead of silently returning False.
"""

import json
import subprocess
from unittest.mock import patch, MagicMock

import pytest

from pipeline.audio import has_audio_track, extract_audio, get_video_info, check_ffmpeg


class TestCheckFfmpeg:
    """Ensures ffmpeg/ffprobe presence in PATH."""

    def test_ffmpeg_in_path(self):
        """Project requires ffmpeg + ffprobe in PATH."""
        try:
            check_ffmpeg()
        except RuntimeError as e:
            pytest.skip(f"ffmpeg not installed: {e}")


class TestHasAudioTrack:
    """has_audio_track must always return bool — never raise.

    NOTE: Current behavior silently returns False on ANY ffprobe error
    (CalledProcessError, JSONDecodeError, TimeoutExpired). This is technically
    a Phase 3 P3.5 miss — should raise FatalError instead. These tests document
    current behavior so we don't accidentally regress.
    """

    @patch("pipeline.audio.subprocess.run")
    def test_has_audio_returns_true_for_audio_streams(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout=json.dumps({
                "streams": [
                    {"codec_type": "audio", "codec_name": "aac"}
                ]
            }),
            returncode=0,
        )
        assert has_audio_track("any.mp4") is True

    @patch("pipeline.audio.subprocess.run")
    def test_has_audio_returns_false_for_no_streams(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout=json.dumps({"streams": []}),
            returncode=0,
        )
        assert has_audio_track("any.mp4") is False

    @patch("pipeline.audio.subprocess.run")
    def test_has_audio_returns_false_on_ffprobe_error(self, mock_run):
        """KNOWN ISSUE: silent failure. ffprobe error → False."""
        mock_run.side_effect = subprocess.CalledProcessError(1, "ffprobe")
        assert has_audio_track("any.mp4") is False

    @patch("pipeline.audio.subprocess.run")
    def test_has_audio_returns_false_on_timeout(self, mock_run):
        """KNOWN ISSUE: silent failure. Timeout → False."""
        mock_run.side_effect = subprocess.TimeoutExpired("ffprobe", 30)
        assert has_audio_track("any.mp4") is False

    @patch("pipeline.audio.subprocess.run")
    def test_has_audio_returns_false_on_json_error(self, mock_run):
        """KNOWN ISSUE: silent failure. Bad JSON → False."""
        mock_run.return_value = MagicMock(stdout="not json", returncode=0)
        assert has_audio_track("any.mp4") is False

    @patch("pipeline.audio.subprocess.run")
    def test_has_audio_with_missing_streams_key(self, mock_run):
        """Empty data dict (no 'streams' key) → False, not crash."""
        mock_run.return_value = MagicMock(
            stdout=json.dumps({}),
            returncode=0,
        )
        assert has_audio_track("any.mp4") is False


class TestExtractAudio:
    def test_missing_video_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError) as exc_info:
            extract_audio(str(tmp_path / "does_not_exist.mp4"))
        assert "not found" in str(exc_info.value).lower()

    @patch("pipeline.audio.has_audio_track")
    @patch("pipeline.audio.subprocess.run")
    @patch("pipeline.audio.check_ffmpeg")
    def test_no_audio_track_raises_value_error(self, mock_check, mock_run, mock_has, tmp_path):
        """Video without audio track → ValueError (not silent skip)."""
        video = tmp_path / "test.mp4"
        video.touch()
        mock_has.return_value = False
        with pytest.raises(ValueError) as exc_info:
            extract_audio(str(video))
        assert "no audio track" in str(exc_info.value).lower()


class TestGetVideoInfo:
    @patch("pipeline.audio.subprocess.run")
    def test_parses_dimensions_and_codec(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout=json.dumps({
                "streams": [{
                    "width": 1920,
                    "height": 1080,
                    "codec_name": "h264",
                }]
            }),
            returncode=0,
        )
        info = get_video_info("test.mp4")
        assert info["width"] == 1920
        assert info["height"] == 1080
        assert info["codec"] == "h264"

    @patch("pipeline.audio.subprocess.run")
    def test_handles_missing_codec_name(self, mock_run):
        """codec_name absent → empty string fallback."""
        mock_run.return_value = MagicMock(
            stdout=json.dumps({
                "streams": [{"width": 1280, "height": 720}]
            }),
            returncode=0,
        )
        info = get_video_info("test.mp4")
        assert info["codec"] == ""
