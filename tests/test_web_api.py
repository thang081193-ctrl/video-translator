"""Tests for web API endpoints using FastAPI TestClient."""

import re
import pytest
from fastapi.testclient import TestClient

from pipeline.languages import LANGUAGES, ALL_LANGUAGE_CODES


VOICE_PATTERN = re.compile(r"^[a-z]{2}-[A-Z]{2}-[A-Z][A-Za-z]+Neural$")


@pytest.fixture
def client():
    """Create a test client for the web app."""
    from web.app import create_app
    app = create_app()
    return TestClient(app)


class TestHealthEndpoint:
    def test_health(self, client):
        r = client.get("/api/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


class TestLanguagesEndpoint:
    """Strict contract tests — catches drift, ordering, mismatches."""

    def test_response_shape(self, client):
        r = client.get("/api/languages")
        assert r.status_code == 200
        data = r.json()
        assert "languages" in data
        assert "voices" in data
        assert isinstance(data["languages"], list)
        assert isinstance(data["voices"], dict)

    def test_count_matches_registry(self, client):
        """API must return exactly the same count as the registry."""
        r = client.get("/api/languages")
        data = r.json()
        assert len(data["languages"]) == len(LANGUAGES)
        assert len(data["voices"]) == len(LANGUAGES)

    def test_all_codes_unique(self, client):
        """No duplicate language codes in the response."""
        r = client.get("/api/languages")
        codes = [lang["code"] for lang in r.json()["languages"]]
        assert len(codes) == len(set(codes)), f"Duplicates: {codes}"

    def test_languages_voices_match(self, client):
        """Set of language codes must equal set of voice keys (no orphans)."""
        r = client.get("/api/languages")
        data = r.json()
        lang_codes = {lang["code"] for lang in data["languages"]}
        voice_codes = set(data["voices"].keys())
        assert lang_codes == voice_codes, \
            f"Mismatch: in_languages_only={lang_codes - voice_codes}, " \
            f"in_voices_only={voice_codes - lang_codes}"

    def test_first_two_pinned(self, client):
        """vi must be first, en must be second (UI ordering contract)."""
        r = client.get("/api/languages")
        languages = r.json()["languages"]
        assert languages[0]["code"] == "vi"
        assert languages[1]["code"] == "en"

    def test_voice_format(self, client):
        """All voice IDs must follow xx-XX-NameNeural pattern."""
        r = client.get("/api/languages")
        voices = r.json()["voices"]
        for code, voice in voices.items():
            assert VOICE_PATTERN.match(voice), \
                f"{code}: voice '{voice}' doesn't match pattern"

    def test_all_languages_have_native_name(self, client):
        """Every language entry must have a non-empty name."""
        r = client.get("/api/languages")
        for lang in r.json()["languages"]:
            assert lang.get("name"), f"Empty name for {lang['code']}"


class TestTranslateValidation:
    """Validation tests for /api/translate (P4.3)."""

    def test_invalid_target_lang_returns_400(self, client, tmp_path):
        """Unknown target_lang must return 400 immediately."""
        fake_video = tmp_path / "test.mp4"
        fake_video.write_bytes(b"fake")
        with open(fake_video, "rb") as f:
            r = client.post(
                "/api/translate",
                files={"video": ("test.mp4", f, "video/mp4")},
                data={"target_lang": "xx"},
            )
        assert r.status_code == 400
        assert "target_lang" in r.json()["error"].lower()

    def test_invalid_source_lang_returns_400(self, client, tmp_path):
        """Unknown source_lang must return 400 immediately."""
        fake_video = tmp_path / "test.mp4"
        fake_video.write_bytes(b"fake")
        with open(fake_video, "rb") as f:
            r = client.post(
                "/api/translate",
                files={"video": ("test.mp4", f, "video/mp4")},
                data={"target_lang": "vi", "source_lang": "xx"},
            )
        assert r.status_code == 400
        assert "source_lang" in r.json()["error"].lower()


class TestStatusEndpoint:
    def test_missing_job(self, client):
        r = client.get("/api/status/nonexistent")
        assert r.status_code == 404


class TestDownloadEndpoint:
    def test_missing_job(self, client):
        r = client.get("/api/download/nonexistent/file.srt")
        assert r.status_code == 404


class TestIndexPage:
    def test_index_returns_html(self, client):
        r = client.get("/")
        assert r.status_code == 200
