"""Pipeline integration tests — run_pipeline orchestration with mocked I/O.

Tests the full pipeline flow without actually calling ffmpeg, Whisper, or APIs.
Verifies:
- Step ordering and counting
- Conditional branches (dub, OCR, burn)
- Result accumulation
- Cleanup logic
"""

import os
from unittest.mock import patch, MagicMock

import pytest

from web.pipeline_runner import PipelineParams, run_pipeline


@pytest.fixture
def fake_video(tmp_path):
    """Create a minimal fake video file path."""
    video_path = tmp_path / "test.mp4"
    video_path.touch()
    return str(video_path)


def _common_mocks(tmp_path):
    """Patch all expensive pipeline calls. Returns dict of mocks."""
    wav_path = str(tmp_path / "test.wav")
    # Touch wav so cleanup os.remove works
    open(wav_path, "w").close()
    return {
        "wav_path": wav_path,
        "segments": [
            {"start": 0.0, "end": 1.5, "text": "Hello world"},
            {"start": 2.0, "end": 3.5, "text": "Goodbye"},
        ],
        "translated": [
            {"start": 0.0, "end": 1.5, "text": "Hello world", "translated_text": "Xin chào thế giới"},
            {"start": 2.0, "end": 3.5, "text": "Goodbye", "translated_text": "Tạm biệt"},
        ],
    }


class TestBasicPipeline:
    """Translate-only flow: audio → transcribe → translate → SRT."""

    @patch("web.pipeline_runner.generate_srt")
    @patch("web.pipeline_runner.translate_segments")
    @patch("web.pipeline_runner.transcribe")
    @patch("web.pipeline_runner.extract_audio")
    @patch("web.pipeline_runner.check_ffmpeg")
    def test_basic_translate_only(
        self, mock_check, mock_extract, mock_transcribe,
        mock_translate, mock_srt, fake_video, tmp_path,
    ):
        m = _common_mocks(tmp_path)
        mock_extract.return_value = m["wav_path"]
        mock_transcribe.return_value = (m["segments"], "en")
        mock_translate.return_value = m["translated"]

        params = PipelineParams(
            video_path=fake_video,
            target_lang="vi",
            output_dir=str(tmp_path),
        )
        result = run_pipeline(params)

        assert result.detected_lang == "en"
        assert result.segments_count == 2
        assert result.srt_path is not None
        assert result.dubbed_video is None
        assert result.burned_video is None
        # SRT file should be in result.files
        assert any(f["type"] == "srt" for f in result.files)

        mock_check.assert_called_once()
        mock_extract.assert_called_once()
        mock_transcribe.assert_called_once()
        mock_translate.assert_called_once()
        mock_srt.assert_called_once()

    @patch("web.pipeline_runner.generate_srt")
    @patch("web.pipeline_runner.translate_segments")
    @patch("web.pipeline_runner.transcribe")
    @patch("web.pipeline_runner.extract_audio")
    @patch("web.pipeline_runner.check_ffmpeg")
    def test_progress_callback_invoked(
        self, mock_check, mock_extract, mock_transcribe,
        mock_translate, mock_srt, fake_video, tmp_path,
    ):
        m = _common_mocks(tmp_path)
        mock_extract.return_value = m["wav_path"]
        mock_transcribe.return_value = (m["segments"], "en")
        mock_translate.return_value = m["translated"]

        progress_calls = []
        def on_progress(step, total, label, pct):
            progress_calls.append((step, total, label, pct))

        params = PipelineParams(
            video_path=fake_video, target_lang="vi", output_dir=str(tmp_path),
        )
        run_pipeline(params, on_progress=on_progress)

        assert len(progress_calls) > 0
        # Last call should be at high progress
        assert progress_calls[-1][3] >= 75
        # Step should be monotonically non-decreasing
        steps = [c[0] for c in progress_calls]
        assert steps == sorted(steps)


class TestEmptySpeechHandling:
    """Video with no detectable speech → 0 segments → original video as output."""

    @patch("web.pipeline_runner.generate_srt")
    @patch("web.pipeline_runner.translate_segments")
    @patch("web.pipeline_runner.transcribe")
    @patch("web.pipeline_runner.extract_audio")
    @patch("web.pipeline_runner.check_ffmpeg")
    def test_no_speech_detected(
        self, mock_check, mock_extract, mock_transcribe,
        mock_translate, mock_srt, fake_video, tmp_path,
    ):
        m = _common_mocks(tmp_path)
        mock_extract.return_value = m["wav_path"]
        mock_transcribe.return_value = ([], "en")  # NO segments
        mock_translate.return_value = []

        params = PipelineParams(
            video_path=fake_video, target_lang="vi", output_dir=str(tmp_path),
        )
        result = run_pipeline(params)

        assert result.segments_count == 0
        # No SRT in files (because has_segments == False)
        assert not any(f["type"] == "srt" for f in result.files)
        # Should fall back to original video
        assert len(result.files) >= 1
        assert any("Original" in f.get("label", "") for f in result.files)

    @patch("web.pipeline_runner.dub_video")
    @patch("web.pipeline_runner.build_dubbed_audio")
    @patch("web.pipeline_runner.generate_srt")
    @patch("web.pipeline_runner.translate_segments")
    @patch("web.pipeline_runner.transcribe")
    @patch("web.pipeline_runner.extract_audio")
    @patch("web.pipeline_runner.check_ffmpeg")
    def test_dub_skipped_when_no_speech(
        self, mock_check, mock_extract, mock_transcribe,
        mock_translate, mock_srt, mock_build_dub, mock_dub_vid,
        fake_video, tmp_path,
    ):
        """dub=True but no speech → dub functions not called."""
        m = _common_mocks(tmp_path)
        mock_extract.return_value = m["wav_path"]
        mock_transcribe.return_value = ([], "en")
        mock_translate.return_value = []

        params = PipelineParams(
            video_path=fake_video, target_lang="vi",
            dub=True, bgm_path="fake.mp3",
            output_dir=str(tmp_path),
        )
        result = run_pipeline(params)

        # Dub should NOT be called (graceful skip)
        mock_build_dub.assert_not_called()
        mock_dub_vid.assert_not_called()
        assert result.dubbed_video is None


class TestDubbingFlow:
    """Full dubbing pipeline with custom BGM."""

    @patch("web.pipeline_runner.dub_video")
    @patch("web.pipeline_runner.build_dubbed_audio")
    @patch("web.pipeline_runner.get_audio_duration")
    @patch("web.pipeline_runner.generate_srt")
    @patch("web.pipeline_runner.translate_segments")
    @patch("web.pipeline_runner.transcribe")
    @patch("web.pipeline_runner.extract_audio")
    @patch("web.pipeline_runner.check_ffmpeg")
    def test_dub_with_custom_bgm(
        self, mock_check, mock_extract, mock_transcribe, mock_translate,
        mock_srt, mock_duration, mock_build_dub, mock_dub_vid,
        fake_video, tmp_path,
    ):
        m = _common_mocks(tmp_path)
        mock_extract.return_value = m["wav_path"]
        mock_transcribe.return_value = (m["segments"], "en")
        mock_translate.return_value = m["translated"]
        mock_duration.return_value = 60.0

        bgm_path = str(tmp_path / "music.mp3")
        open(bgm_path, "w").close()

        params = PipelineParams(
            video_path=fake_video, target_lang="vi",
            dub=True, bgm_path=bgm_path, audio_mode="custom_bgm",
            output_dir=str(tmp_path),
        )
        result = run_pipeline(params)

        mock_build_dub.assert_called_once()
        mock_dub_vid.assert_called_once()
        assert result.dubbed_video is not None
        # Verify dubbed video in files
        assert any(f["type"] == "video" and "Dubbed" in f.get("label", "")
                   for f in result.files)


class TestCleanup:
    """Verify temp files are cleaned up after pipeline completes."""

    @patch("web.pipeline_runner.generate_srt")
    @patch("web.pipeline_runner.translate_segments")
    @patch("web.pipeline_runner.transcribe")
    @patch("web.pipeline_runner.extract_audio")
    @patch("web.pipeline_runner.check_ffmpeg")
    def test_wav_cleaned_up(
        self, mock_check, mock_extract, mock_transcribe,
        mock_translate, mock_srt, fake_video, tmp_path,
    ):
        wav_path = str(tmp_path / "test.wav")
        open(wav_path, "w").close()
        assert os.path.isfile(wav_path)

        mock_extract.return_value = wav_path
        mock_transcribe.return_value = ([{"start": 0, "end": 1, "text": "hi"}], "en")
        mock_translate.return_value = [{"start": 0, "end": 1, "text": "hi", "translated_text": "chào"}]

        params = PipelineParams(
            video_path=fake_video, target_lang="vi", output_dir=str(tmp_path),
        )
        run_pipeline(params)

        # WAV should be removed after pipeline
        assert not os.path.isfile(wav_path)
