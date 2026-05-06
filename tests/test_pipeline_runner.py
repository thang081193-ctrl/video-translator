"""Tests for web/pipeline_runner.py — step counting + params dataclass."""

import pytest

from web.pipeline_runner import PipelineParams, PipelineResult, count_steps


class TestCountSteps:
    """Verify total step calculation matches the actual pipeline branching."""

    def test_basic_no_options_one_lang(self):
        """shared 2 + per_lang (translate, srt, finalize) = 5 steps for 1 lang."""
        params = PipelineParams(video_path="test.mp4", target_langs=["vi"])
        assert count_steps(params) == 5

    def test_with_ocr_one_lang(self):
        """Basic + OCR per lang = 6 steps for 1 lang."""
        params = PipelineParams(
            video_path="test.mp4", target_langs=["vi"], translate_ocr=True,
        )
        assert count_steps(params) == 6

    def test_with_dub_only_one_lang(self):
        """Basic + TTS + merge = 7 steps for 1 lang (custom_bgm — no shared Demucs)."""
        params = PipelineParams(
            video_path="test.mp4", target_langs=["vi"], dub=True, audio_mode="custom_bgm",
        )
        assert count_steps(params) == 7

    def test_with_dub_keep_bgm_adds_shared_demucs_step(self):
        """keep_original_bgm adds 1 shared Demucs step on top of base."""
        params = PipelineParams(
            video_path="test.mp4", target_langs=["vi"],
            dub=True, audio_mode="keep_original_bgm",
        )
        # shared 2+1 (Demucs) + per_lang 5 (translate, srt, dub-mix, merge, finalize) = 8
        assert count_steps(params) == 8

    def test_burn_no_extra_step(self):
        params = PipelineParams(video_path="test.mp4", target_langs=["vi"], burn=True)
        assert count_steps(params) == 5

    def test_scales_linearly_with_n_langs(self):
        """N langs × per-lang steps + shared. Multi-target is the whole point."""
        single = PipelineParams(
            video_path="x.mp4", target_langs=["vi"], dub=True, audio_mode="custom_bgm",
        )
        five = PipelineParams(
            video_path="x.mp4", target_langs=["vi", "en", "ja", "es", "ko"],
            dub=True, audio_mode="custom_bgm",
        )
        # custom_bgm → no shared Demucs. Shared=2, per_lang=5 (translate, srt, dub, merge, finalize).
        assert count_steps(single) == 2 + 1 * 5
        assert count_steps(five) == 2 + 5 * 5

    def test_demucs_step_shared_across_langs(self):
        """Demucs runs once regardless of N langs — that's the speedup."""
        one = PipelineParams(
            video_path="x.mp4", target_langs=["vi"],
            dub=True, audio_mode="keep_original_bgm",
        )
        five = PipelineParams(
            video_path="x.mp4", target_langs=["vi", "en", "ja", "es", "ko"],
            dub=True, audio_mode="keep_original_bgm",
        )
        # one: 2+1 + 1×5 = 8
        # five: 2+1 + 5×5 = 28 (NOT 5×8 = 40)
        assert count_steps(one) == 8
        assert count_steps(five) == 28


class TestPipelineParams:
    """Verify dataclass defaults match documented behavior."""

    def test_minimum_required_args(self):
        params = PipelineParams(video_path="test.mp4", target_langs=["vi"])
        assert params.video_path == "test.mp4"
        assert params.target_langs == ["vi"]

    def test_empty_target_langs_raises(self):
        """target_langs is the source of truth — empty list is a programming error."""
        with pytest.raises(ValueError, match="target_langs"):
            PipelineParams(video_path="test.mp4", target_langs=[])

    def test_default_values(self):
        params = PipelineParams(video_path="test.mp4", target_langs=["vi"])
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
            target_langs=["ja", "en"],
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
        assert params.target_langs == ["ja", "en"]
        assert params.whisper_model == "large-v3"
        assert params.bgm_volume == 0.5
        assert params.ocr_quality == "premium"
        assert params.use_cache is False


class TestPipelineResult:
    """Verify result dataclass defaults."""

    def test_empty_result(self):
        result = PipelineResult()
        assert result.srt_paths == {}
        assert result.dubbed_videos == {}
        assert result.burned_videos == {}
        assert result.files == []
        assert result.segments_count == 0
        assert result.detected_lang == ""
        assert result.stage_timings == {}

    def test_files_list_independent(self):
        """files list should not be shared across instances."""
        r1 = PipelineResult()
        r2 = PipelineResult()
        r1.files.append({"name": "test.srt"})
        assert r2.files == []

    def test_per_lang_dicts_independent(self):
        r1 = PipelineResult()
        r2 = PipelineResult()
        r1.srt_paths["vi"] = "/tmp/x.srt"
        r1.dubbed_videos["vi"] = "/tmp/x.mp4"
        assert r2.srt_paths == {}
        assert r2.dubbed_videos == {}
