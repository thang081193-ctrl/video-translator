"""Tests for pipeline/subtitle.py — SRT generation, timestamps, line breaking."""

import os
import tempfile
import pytest
from pipeline.subtitle import _format_timestamp, _break_lines, generate_srt


class TestFormatTimestamp:
    """Test SRT timestamp formatting."""

    def test_zero(self):
        assert _format_timestamp(0) == "00:00:00,000"

    def test_simple_seconds(self):
        assert _format_timestamp(1.5) == "00:00:01,500"

    def test_minutes(self):
        assert _format_timestamp(65.123) == "00:01:05,123"

    def test_hours(self):
        assert _format_timestamp(3661.999) == "01:01:01,999"

    def test_millisecond_precision(self):
        assert _format_timestamp(0.001) == "00:00:00,001"
        assert _format_timestamp(0.999) == "00:00:00,999"

    def test_large_value(self):
        assert _format_timestamp(7200) == "02:00:00,000"


class TestBreakLines:
    """Test subtitle line breaking."""

    def test_short_text(self):
        assert _break_lines("Hello world") == "Hello world"

    def test_long_text_breaks(self):
        text = "This is a long subtitle text that should be broken into two lines"
        result = _break_lines(text, max_chars=40)
        assert "\n" in result
        lines = result.split("\n")
        assert len(lines) == 2

    def test_exact_limit(self):
        text = "A" * 42
        assert _break_lines(text) == text  # exactly at limit

    def test_over_limit(self):
        text = "word1 word2 word3 word4 word5 word6 word7 word8 word9"
        result = _break_lines(text, max_chars=30)
        assert "\n" in result  # breaks at word boundary


class TestGenerateSrt:
    """Test SRT file generation."""

    def test_basic_generation(self, tmp_path):
        segments = [
            {"start": 0.0, "end": 2.5, "translated_text": "Hello"},
            {"start": 3.0, "end": 5.0, "translated_text": "World"},
        ]
        output = str(tmp_path / "test.srt")
        generate_srt(segments, output, text_key="translated_text")

        assert os.path.isfile(output)
        content = open(output, "r", encoding="utf-8").read()
        assert "1\n" in content
        assert "Hello" in content
        assert "World" in content
        assert "00:00:00,000 --> 00:00:02,500" in content

    def test_empty_segments_skipped(self, tmp_path):
        segments = [
            {"start": 0.0, "end": 2.0, "translated_text": "Hello"},
            {"start": 3.0, "end": 4.0, "translated_text": ""},
            {"start": 5.0, "end": 6.0, "translated_text": "World"},
        ]
        output = str(tmp_path / "test.srt")
        generate_srt(segments, output)

        content = open(output, "r", encoding="utf-8").read()
        assert "1\n" in content
        assert "2\n" in content
        # Index should be 1,2 (not 1,2,3) since empty is skipped
        assert "3\n" not in content

    def test_fallback_text_key(self, tmp_path):
        segments = [
            {"start": 0.0, "end": 1.0, "text": "Original"},
        ]
        output = str(tmp_path / "test.srt")
        generate_srt(segments, output, text_key="translated_text")

        content = open(output, "r", encoding="utf-8").read()
        assert "Original" in content  # falls back to "text" key
