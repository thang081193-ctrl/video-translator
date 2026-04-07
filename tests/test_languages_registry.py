"""Contract tests for the language registry (pipeline/languages.py).

These tests enforce invariants that prevent the kinds of drift Phase 4
was created to fix:
- Single source of truth: derived constants stay in sync
- Display order: vi first, en second, rest by speaker rank
- No duplicates: codes and voices are unique
- Voice format: matches edge-tts pattern xx-XX-NameNeural
- Drawtext policy: only specific languages are flagged safe
"""

import re
import pytest

from pipeline.languages import (
    LANGUAGES,
    LanguageSpec,
    DEFAULT_VOICES,
    DRAWTEXT_UNSAFE_LANGS,
    ALL_LANGUAGE_CODES,
    LANGUAGE_NAMES,
    get_language,
    is_supported,
    list_for_ui,
)


# Voice ID format: e.g. "vi-VN-HoaiMyNeural", "zh-CN-XiaoxiaoNeural"
VOICE_PATTERN = re.compile(r"^[a-z]{2}-[A-Z]{2}-[A-Z][A-Za-z]+Neural$")

# OCR-safe languages: ffmpeg drawtext renders these without overlay
EXPECTED_DRAWTEXT_SAFE = {"en", "zh", "ja", "ko", "id"}


class TestRegistrySize:
    """Registry must have a known number of entries (updated as we add langs)."""

    def test_at_least_15(self):
        assert len(LANGUAGES) >= 15

    def test_no_orphan_constants(self):
        """All derived constants must have the same size as LANGUAGES."""
        assert len(DEFAULT_VOICES) == len(LANGUAGES)
        assert len(ALL_LANGUAGE_CODES) == len(LANGUAGES)
        assert len(LANGUAGE_NAMES) == len(LANGUAGES)


class TestUniqueness:
    """Codes and voices must be unique — no copy-paste duplicates."""

    def test_codes_unique(self):
        codes = [spec.code for spec in LANGUAGES]
        assert len(codes) == len(set(codes)), f"Duplicate codes: {codes}"

    def test_voices_unique(self):
        voices = [spec.voice for spec in LANGUAGES]
        assert len(voices) == len(set(voices)), f"Duplicate voices: {voices}"

    def test_speaker_ranks_unique(self):
        ranks = [spec.speaker_rank for spec in LANGUAGES]
        assert len(ranks) == len(set(ranks)), f"Duplicate speaker ranks: {ranks}"


class TestSpecValidity:
    """Each LanguageSpec must have valid fields."""

    @pytest.mark.parametrize("spec", LANGUAGES, ids=[s.code for s in LANGUAGES])
    def test_code_format(self, spec: LanguageSpec):
        assert len(spec.code) == 2, f"{spec.code} is not 2 letters"
        assert spec.code.islower(), f"{spec.code} is not lowercase"

    @pytest.mark.parametrize("spec", LANGUAGES, ids=[s.code for s in LANGUAGES])
    def test_native_name_set(self, spec: LanguageSpec):
        assert spec.native_name, f"{spec.code} has empty native_name"

    @pytest.mark.parametrize("spec", LANGUAGES, ids=[s.code for s in LANGUAGES])
    def test_voice_format(self, spec: LanguageSpec):
        assert VOICE_PATTERN.match(spec.voice), \
            f"{spec.code}: voice '{spec.voice}' doesn't match xx-XX-NameNeural"

    @pytest.mark.parametrize("spec", LANGUAGES, ids=[s.code for s in LANGUAGES])
    def test_voice_locale_starts_with_lang(self, spec: LanguageSpec):
        """Voice ID locale should start with the language code (e.g. vi → vi-VN)."""
        voice_lang = spec.voice.split("-")[0]
        assert voice_lang == spec.code, \
            f"{spec.code}: voice {spec.voice} starts with {voice_lang}"

    @pytest.mark.parametrize("spec", LANGUAGES, ids=[s.code for s in LANGUAGES])
    def test_speaker_rank_positive(self, spec: LanguageSpec):
        assert spec.speaker_rank >= 1, f"{spec.code} has invalid rank {spec.speaker_rank}"

    @pytest.mark.parametrize("spec", LANGUAGES, ids=[s.code for s in LANGUAGES])
    def test_script_family_valid(self, spec: LanguageSpec):
        valid = {"latin", "cjk", "arabic", "devanagari", "cyrillic",
                 "bengali", "telugu", "tamil", "greek", "thai"}
        assert spec.script_family in valid, \
            f"{spec.code}: script_family '{spec.script_family}' not in {valid}"


class TestDrawtextPolicy:
    """OCR drawtext safety policy: only specific languages render natively."""

    def test_safe_set_matches_expected(self):
        actual_safe = {spec.code for spec in LANGUAGES if spec.drawtext_safe}
        # Expected safe set is a subset of actual (unknown future langs OK)
        for code in EXPECTED_DRAWTEXT_SAFE:
            if code in ALL_LANGUAGE_CODES:
                assert code in actual_safe, f"{code} should be drawtext-safe"

    def test_unsafe_complement(self):
        """DRAWTEXT_UNSAFE_LANGS is the complement of safe set."""
        safe = {spec.code for spec in LANGUAGES if spec.drawtext_safe}
        unsafe = set(DRAWTEXT_UNSAFE_LANGS)
        all_codes = {spec.code for spec in LANGUAGES}
        assert safe | unsafe == all_codes
        assert safe & unsafe == set()  # no overlap


class TestDerivedConstants:
    """Derived constants must stay in sync with LANGUAGES (single source of truth)."""

    def test_default_voices_matches_languages(self):
        for spec in LANGUAGES:
            assert DEFAULT_VOICES[spec.code] == spec.voice

    def test_all_codes_matches_languages(self):
        assert ALL_LANGUAGE_CODES == frozenset(spec.code for spec in LANGUAGES)

    def test_drawtext_unsafe_matches_languages(self):
        expected = tuple(spec.code for spec in LANGUAGES if not spec.drawtext_safe)
        assert DRAWTEXT_UNSAFE_LANGS == expected

    def test_language_names_matches_languages(self):
        for spec in LANGUAGES:
            assert LANGUAGE_NAMES[spec.code] == spec.native_name


class TestLookupHelpers:
    """get_language() and is_supported() lookups."""

    def test_get_language_existing(self):
        spec = get_language("vi")
        assert spec is not None
        assert spec.code == "vi"
        assert spec.native_name == "Tiếng Việt"

    def test_get_language_missing(self):
        assert get_language("xx") is None

    def test_is_supported_true(self):
        for code in ("vi", "en", "zh"):
            assert is_supported(code) is True

    def test_is_supported_false(self):
        assert is_supported("xx") is False
        assert is_supported("") is False


class TestUIOrdering:
    """list_for_ui() returns the right order: vi → en → speaker rank."""

    def test_returns_all_languages(self):
        ui_list = list_for_ui()
        assert len(ui_list) == len(LANGUAGES)

    def test_first_two_pinned(self):
        ui_list = list_for_ui()
        assert ui_list[0]["code"] == "vi", "vi must be first"
        assert ui_list[1]["code"] == "en", "en must be second"

    def test_rest_sorted_by_speaker_rank(self):
        ui_list = list_for_ui()
        rest_codes = [item["code"] for item in ui_list[2:]]
        rest_specs = [get_language(code) for code in rest_codes]
        ranks = [spec.speaker_rank for spec in rest_specs]
        assert ranks == sorted(ranks), \
            f"Rest of UI list not sorted by speaker_rank: {ranks}"

    def test_returns_dict_with_code_and_name(self):
        for item in list_for_ui():
            assert "code" in item
            assert "name" in item
            assert item["code"] in ALL_LANGUAGE_CODES
