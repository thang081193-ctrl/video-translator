"""Negative path tests — error handling, validation, parsing edge cases.

Covers:
- API key loading failures
- Translation response parsing edge cases
- Voice lookup with invalid input
- Prompt building with missing context
"""

import pytest

from pipeline.providers.factory import load_keys
from pipeline.translate import _parse_json_array, _build_prompt
from pipeline.dub.tts import get_voice_for_lang
from pipeline.errors import TransientError, FatalError, DegradedError


# ─── API Key Loading ─────────────────────────────────────────────────────────

class TestKeyLoadingErrors:
    def test_no_keys_at_all(self, monkeypatch):
        """No API keys set anywhere → FatalError with helpful message."""
        for var in ["GROK_API_KEYS", "GROK_API_KEY", "GEMINI_API_KEYS",
                    "GEMINI_API_KEY", "VERTEX_API_KEYS", "VERTEX_API_KEY"]:
            monkeypatch.delenv(var, raising=False)

        with pytest.raises(FatalError) as exc_info:
            load_keys()
        msg = str(exc_info.value)
        assert "GROK_API_KEYS" in msg or "GEMINI_API_KEYS" in msg

    def test_empty_keys_treated_as_missing(self, monkeypatch):
        """Empty string keys should be treated as no keys."""
        for var in ["GROK_API_KEYS", "GEMINI_API_KEYS", "VERTEX_API_KEYS"]:
            monkeypatch.setenv(var, "")
        for var in ["GROK_API_KEY", "GEMINI_API_KEY", "VERTEX_API_KEY"]:
            monkeypatch.delenv(var, raising=False)

        with pytest.raises(FatalError):
            load_keys()

    def test_whitespace_only_keys_filtered(self, monkeypatch):
        """Keys that are just whitespace should be filtered out."""
        for var in ["GROK_API_KEYS", "GEMINI_API_KEYS", "VERTEX_API_KEYS",
                    "GROK_API_KEY", "GEMINI_API_KEY", "VERTEX_API_KEY"]:
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("GROK_API_KEYS", "   ,  ,   ")

        with pytest.raises(FatalError):
            load_keys()

    def test_comma_separated_keys(self, monkeypatch):
        """Multiple keys via comma-separated env var."""
        for var in ["GROK_API_KEY", "GEMINI_API_KEY", "VERTEX_API_KEYS",
                    "VERTEX_API_KEY", "GEMINI_API_KEYS"]:
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("GROK_API_KEYS", "xai-1,xai-2,xai-3")

        keys = load_keys()
        assert len(keys) == 3
        assert all(k["provider"] == "grok" for k in keys)


# ─── Translation Response Parsing ────────────────────────────────────────────

class TestParseJsonArray:
    """LLM responses come in many shapes — must parse robustly."""

    def test_clean_array(self):
        assert _parse_json_array('["a", "b", "c"]') == ["a", "b", "c"]

    def test_markdown_json_fence(self):
        text = '```json\n["hello", "world"]\n```'
        assert _parse_json_array(text) == ["hello", "world"]

    def test_markdown_plain_fence(self):
        text = '```\n["a", "b"]\n```'
        assert _parse_json_array(text) == ["a", "b"]

    def test_empty_array(self):
        assert _parse_json_array('[]') == []

    def test_array_in_explanatory_text(self):
        """LLM sometimes adds prose around the array."""
        text = 'Sure, here are translations: ["xin chào", "tạm biệt"]'
        assert _parse_json_array(text) == ["xin chào", "tạm biệt"]

    def test_invalid_json_returns_none(self):
        assert _parse_json_array('not valid json at all') is None

    def test_object_not_array_returns_none(self):
        """Object response (LLM mistake) should be rejected."""
        assert _parse_json_array('{"key": "value"}') is None

    def test_null_returns_none(self):
        assert _parse_json_array('null') is None

    def test_number_returns_none(self):
        assert _parse_json_array('42') is None

    def test_array_with_numbers_coerced_to_strings(self):
        """All items get stringified."""
        result = _parse_json_array('[1, 2, 3]')
        assert result == ["1", "2", "3"]


# ─── Prompt Building ─────────────────────────────────────────────────────────

class TestBuildPrompt:
    def test_basic_prompt_structure(self):
        """Prompt includes source/target lang and segment list."""
        prompt = _build_prompt(["Hello"], "en", "vi")
        assert "from en to vi" in prompt
        assert "Hello" in prompt
        assert "JSON array" in prompt

    def test_with_context_before(self):
        prompt = _build_prompt(
            ["middle"], "en", "vi",
            context_before=["before1", "before2"],
        )
        assert "before1" in prompt
        assert "before2" in prompt
        assert "previous" in prompt.lower()

    def test_with_context_after(self):
        prompt = _build_prompt(
            ["middle"], "en", "vi",
            context_after=["after1", "after2"],
        )
        assert "after1" in prompt
        assert "after2" in prompt
        assert "following" in prompt.lower()

    def test_multiple_segments_numbered(self):
        prompt = _build_prompt(["one", "two", "three"], "en", "vi")
        assert '1. "one"' in prompt
        assert '2. "two"' in prompt
        assert '3. "three"' in prompt

    def test_no_context_no_context_section(self):
        """Without context, prompt should not have the context headers."""
        prompt = _build_prompt(["text"], "en", "vi")
        assert "previous segments" not in prompt.lower()
        assert "following segments" not in prompt.lower()


# ─── Voice Lookup ────────────────────────────────────────────────────────────

class TestVoiceLookup:
    def test_valid_language(self):
        assert get_voice_for_lang("vi") == "vi-VN-HoaiMyNeural"
        assert get_voice_for_lang("en") == "en-US-JennyNeural"

    def test_invalid_language_raises(self):
        with pytest.raises(FatalError) as exc_info:
            get_voice_for_lang("xx")
        assert "No default voice" in str(exc_info.value)

    def test_error_message_lists_supported(self):
        """Error message should help user pick a valid lang."""
        with pytest.raises(FatalError) as exc_info:
            get_voice_for_lang("xx")
        msg = str(exc_info.value)
        assert "vi" in msg
        assert "en" in msg

    def test_custom_voice_overrides_lookup(self):
        """Custom voice always wins over default."""
        assert get_voice_for_lang("vi", "custom-voice") == "custom-voice"

    def test_custom_voice_for_unknown_lang(self):
        """If custom voice provided, no error even for unknown lang."""
        assert get_voice_for_lang("xx", "custom-voice") == "custom-voice"

    def test_empty_lang_raises(self):
        with pytest.raises(FatalError):
            get_voice_for_lang("")


# ─── Custom Exceptions ───────────────────────────────────────────────────────

class TestExceptionUsage:
    """Verify custom exceptions can be raised, caught, and chained."""

    def test_transient_with_retry_after(self):
        try:
            raise TransientError("rate limit", retry_after=30)
        except TransientError as e:
            assert e.retry_after == 30
            assert "rate limit" in str(e)

    def test_fatal_caught_as_pipeline_error(self):
        from pipeline.errors import PipelineError
        with pytest.raises(PipelineError):
            raise FatalError("bad input")

    def test_degraded_with_feature(self):
        try:
            raise DegradedError("OCR unavailable", feature="ocr")
        except DegradedError as e:
            assert e.feature == "ocr"

    def test_exception_chaining(self):
        """Errors should chain via __cause__ for traceability."""
        try:
            try:
                raise ValueError("original")
            except ValueError as e:
                raise FatalError("wrapped") from e
        except FatalError as e:
            assert e.__cause__ is not None
            assert isinstance(e.__cause__, ValueError)
