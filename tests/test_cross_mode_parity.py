"""Cross-mode parity tests — CLI and Web must use the same logic.

These tests catch the kind of drift that broke Phase 3 P3.1:
video_translator.py:158 had a hardcoded `_DRAWTEXT_UNSAFE` set that
diverged from `cfg.ocr.drawtext_unsafe_langs`.

Critical: every test in this file enforces single-source-of-truth invariants.
"""

import os
import pytest

from pipeline.config import cfg
from pipeline.languages import (
    DEFAULT_VOICES,
    DRAWTEXT_UNSAFE_LANGS,
    ALL_LANGUAGE_CODES,
    LANGUAGES,
)


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(rel_path: str) -> str:
    """Read a file from the project root."""
    with open(os.path.join(PROJECT_ROOT, rel_path), "r", encoding="utf-8") as f:
        return f.read()


class TestRegistryDerivedConstants:
    """All derived constants must match the registry — no drift."""

    def test_cfg_drawtext_matches_registry(self):
        """cfg.ocr.drawtext_unsafe_langs derives from registry."""
        assert tuple(cfg.ocr.drawtext_unsafe_langs) == tuple(DRAWTEXT_UNSAFE_LANGS)

    def test_default_voices_count_matches_languages(self):
        """DEFAULT_VOICES dict must have one entry per language."""
        assert set(DEFAULT_VOICES.keys()) == ALL_LANGUAGE_CODES
        assert len(DEFAULT_VOICES) == len(LANGUAGES)

    def test_drawtext_unsafe_is_complement_of_safe(self):
        """Every language is either drawtext_safe or in DRAWTEXT_UNSAFE_LANGS."""
        safe_codes = {spec.code for spec in LANGUAGES if spec.drawtext_safe}
        unsafe_codes = set(DRAWTEXT_UNSAFE_LANGS)
        all_codes = {spec.code for spec in LANGUAGES}
        assert safe_codes | unsafe_codes == all_codes
        assert safe_codes & unsafe_codes == set()


class TestNoLocalDuplicates:
    """Verify no entry point has local copies of registry data."""

    def test_video_translator_no_local_drawtext_set(self):
        """video_translator.py must NOT redefine _DRAWTEXT_UNSAFE locally.

        This test prevents recurrence of the P3.1 CLI drift.
        """
        source = _read("video_translator.py")
        assert "_DRAWTEXT_UNSAFE" not in source, (
            "CLI DRIFT: video_translator.py has local _DRAWTEXT_UNSAFE set. "
            "It must use cfg.ocr.drawtext_unsafe_langs from the registry."
        )

    def test_video_translator_uses_cfg_for_drawtext(self):
        """CLI must reference the shared cfg accessor."""
        source = _read("video_translator.py")
        assert "cfg.ocr.drawtext_unsafe_langs" in source, (
            "CLI must use cfg.ocr.drawtext_unsafe_langs (single source of truth)"
        )

    def test_web_pipeline_runner_uses_cfg_for_drawtext(self):
        """Web pipeline_runner must use the same cfg accessor."""
        source = _read("web/pipeline_runner.py")
        assert "cfg.ocr.drawtext_unsafe_langs" in source

    def test_web_routes_uses_registry_list(self):
        """web/routes.py must call list_for_ui(), not hardcode languages."""
        source = _read("web/routes.py")
        assert "list_for_ui()" in source

    def test_web_routes_no_hardcoded_language_dicts(self):
        """web/routes.py must NOT contain a hardcoded list of language dicts."""
        source = _read("web/routes.py")
        # Check for the old pattern: list of dicts with "code" and "name"
        assert '{"code": "vi"' not in source, (
            "web/routes.py has hardcoded language dict — must use list_for_ui()"
        )

    def test_dub_tts_imports_from_registry(self):
        """pipeline/dub/tts.py must import DEFAULT_VOICES from languages."""
        source = _read("pipeline/dub/tts.py")
        assert "from pipeline.languages import DEFAULT_VOICES" in source, (
            "pipeline/dub/tts.py must import DEFAULT_VOICES from registry"
        )

    def test_dub_tts_no_local_voices_dict(self):
        """pipeline/dub/tts.py must NOT redefine DEFAULT_VOICES locally."""
        source = _read("pipeline/dub/tts.py")
        # The line must not appear as a definition (=)
        assert "DEFAULT_VOICES = {" not in source


class TestImportBoundaries:
    """Ensure no forbidden cross-layer imports."""

    def test_cli_does_not_import_web(self):
        """CLI entry point must NOT import from web/ package."""
        source = _read("video_translator.py")
        assert "from web" not in source, "CLI must not depend on web/"
        assert "import web" not in source

    def test_pipeline_modules_do_not_import_web(self):
        """No pipeline/ module should import from web/."""
        for fname in ["pipeline/translate.py", "pipeline/transcribe.py",
                       "pipeline/audio.py", "pipeline/burn.py",
                       "pipeline/dub/tts.py", "pipeline/dub/mixer.py"]:
            source = _read(fname)
            assert "from web" not in source, f"{fname} imports from web/"

    def test_pipeline_languages_has_no_dependencies(self):
        """pipeline/languages.py must be a leaf module — no pipeline.* imports.

        This prevents circular imports when languages is imported by config/dub.
        """
        source = _read("pipeline/languages.py")
        # The only allowed imports are stdlib and dataclasses
        forbidden = ["from pipeline.config", "from pipeline.dub",
                     "from pipeline.audio", "from pipeline.burn",
                     "from pipeline.translate"]
        for f in forbidden:
            assert f not in source, f"languages.py has forbidden import: {f}"


class TestVoiceLookupConsistency:
    """get_voice_for_lang must agree with registry for all languages."""

    def test_all_registry_codes_have_voice(self):
        """Every language code in registry returns a voice."""
        from pipeline.dub.tts import get_voice_for_lang
        for spec in LANGUAGES:
            voice = get_voice_for_lang(spec.code)
            assert voice == spec.voice, (
                f"{spec.code}: get_voice_for_lang returned {voice}, "
                f"expected {spec.voice}"
            )
