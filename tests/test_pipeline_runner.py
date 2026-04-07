"""Tests for web/pipeline_runner.py — step counting + params dataclass."""

import pytest

from web.pipeline_runner import PipelineParams, PipelineResult, count_steps


class TestCountSteps:
    """Verify total step calculation matches the actual pipeline branching."""

    def test_basic_no_options(self):
        """audio + transcribe + translate + srt + finalize = 5 steps."""
        params = PipelineParams(video_path="test.mp4", target_lang="vi")
        assert count_steps(params) == 5

    def test_with_ocr(self):
        """Basic + OCR detection step = 6 steps."""
        params = PipelineParams(
            video_path="test.mp4", target_lang="vi", translate_ocr=True
        )
        assert count_steps(params) == 6

    def test_with_dub_only(self):
        """Basic + TTS + merge = 7 steps."""
        params = PipelineParams(video_path="test.mp4", target_lang="vi", dub=True)
        assert count_steps(params) == 7

    def test_with_dub_and_ocr(self):
        """Basic + OCR + TTS + merge = 8 steps."""
        params = PipelineParams(
            video_path="test.mp4", target_lang="vi", dub=True, translate_ocr=True
        )
        assert count_steps(params) == 8

    def test_burn_no_extra_step(self):
        """Burn is part of finalize step, not extra."""
        params = PipelineParams(video_path="test.mp4", target_lang="vi", burn=True)
        assert count_steps(params) == 5

    def test_all_options_enabled(self):
        """Maximal pipeline: dub + ocr + burn = 8 steps (burn shares finalize)."""
        params = PipelineParams(
            video_path="test.mp4",
            target_lang="vi",
            dub=True,
            burn=True,
            translate_ocr=True,
        )
        assert count_steps(params) == 8


class TestPipelineParams:
    """Verify dataclass defaults match documented behavior."""

    def test_minimum_required_args(self):
        params = PipelineParams(video_path="test.mp4", target_lang="vi")
        assert params.video_path == "test.mp4"
        assert params.target_lang == "vi"

    def test_default_values(self):
        params = PipelineParams(video_path="test.mp4", target_lang="vi")
        assert params.source_lang is None
        assert params.whisper_model == "medium"
        assert params.burn is False
        assert params.dub is False
        assert params.bgm_path is None
        assert params.tts_voice is None
        assert params.batch_size == 20
        assert params.audio_mode == "keep_original_bgm"
        assert params.bgm_volume == 0.25
        assert params.translate_ocr is False
        assert params.ocr_quality == "fast"
        assert params.use_cache is True
        assert params.output_dir is None

    def test_full_params_construction(self):
        params = PipelineParams(
            video_path="movie.mkv",
            target_lang="ja",
            source_lang="en",
            whisper_model="large-v3",
            burn=True,
            dub=True,
            bgm_path="music.mp3",
            tts_voice="ja-JP-NanamiNeural",
            batch_size=50,
            audio_mode="custom_bgm",
            bgm_volume=0.5,
            translate_ocr=True,
            ocr_quality="premium",
            use_cache=False,
            output_dir="/tmp/out",
        )
        assert params.whisper_model == "large-v3"
        assert params.bgm_volume == 0.5
        assert params.ocr_quality == "premium"
        assert params.use_cache is False


class TestPipelineResult:
    """Verify result dataclass defaults."""

    def test_empty_result(self):
        result = PipelineResult()
        assert result.srt_path is None
        assert result.dubbed_video is None
        assert result.burned_video is None
        assert result.files == []
        assert result.segments_count == 0
        assert result.detected_lang == ""

    def test_files_list_independent(self):
        """files list should not be shared across instances."""
        r1 = PipelineResult()
        r2 = PipelineResult()
        r1.files.append({"name": "test.srt"})
        assert r2.files == []
