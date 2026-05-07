"""Tests for pipeline/providers/ — provider base, factory, key rotation."""

import pytest
from pipeline.errors import FatalError
from pipeline.providers.base import TranslationProvider, KeyRotator
from pipeline.providers.factory import load_keys, build_rotator


class TestTranslationProviderBase:
    """Test static methods on base class."""

    def test_is_rate_limit_429(self):
        assert TranslationProvider.is_rate_limit_error("HTTP 429 Too Many Requests") is True

    def test_is_rate_limit_quota(self):
        assert TranslationProvider.is_rate_limit_error("RESOURCE_EXHAUSTED: quota exceeded") is True

    def test_not_rate_limit(self):
        assert TranslationProvider.is_rate_limit_error("Invalid API key") is False

    def test_compact_error_short(self):
        assert TranslationProvider.compact_error("short error") == "short error"

    def test_compact_error_truncates(self):
        long_error = "x" * 500
        result = TranslationProvider.compact_error(long_error, max_len=100)
        assert len(result) == 100
        assert result.endswith("...")

    def test_extract_retry_seconds(self):
        assert TranslationProvider.extract_retry_seconds("retryDelay: 10s", default=5) == 10

    def test_extract_retry_default(self):
        assert TranslationProvider.extract_retry_seconds("some error", default=7) == 7


class TestKeyRotation:
    """Test key rotation logic using mock provider."""

    class MockProvider(TranslationProvider):
        name = "mock"
        def __init__(self):
            self.calls = 0
            self.last_system: str | None = None
        def generate(self, prompt: str, system_instruction: str | None = None) -> str:
            self.calls += 1
            self.last_system = system_instruction
            return '["translated"]'

    def _make_rotator(self, n_keys: int):
        keys = [{"provider": "mock", "key": f"key-{i}"} for i in range(n_keys)]
        provider = self.MockProvider()
        providers = {f"mock:key-{i}": provider for i in range(n_keys)}

        class TestRotator(KeyRotator):
            def __init__(self, keys, providers):
                self.keys = keys
                self._providers = providers
                self.index = 0
            @property
            def current_provider(self):
                k = self.current_key
                return self._providers[f"{k['provider']}:{k['key']}"]

        return TestRotator(keys, providers), provider

    def test_single_key_no_rotate(self):
        rotator, _ = self._make_rotator(1)
        assert rotator.current_key["key"] == "key-0"
        rotator.rotate()
        assert rotator.current_key["key"] == "key-0"  # loops back

    def test_multi_key_rotates(self):
        rotator, _ = self._make_rotator(3)
        assert rotator.current_key["key"] == "key-0"
        rotator.rotate()
        assert rotator.current_key["key"] == "key-1"
        rotator.rotate()
        assert rotator.current_key["key"] == "key-2"

    def test_exhaust_detection(self):
        rotator, _ = self._make_rotator(2)
        start = rotator.index
        rotator.rotate()
        assert not rotator.rotate_all_exhausted(start)
        rotator.rotate()
        assert rotator.rotate_all_exhausted(start)

    def test_generate_delegates(self):
        rotator, provider = self._make_rotator(1)
        result = rotator.generate("test prompt")
        assert result == '["translated"]'
        assert provider.calls == 1


class TestLoadKeys:
    """Test key loading from environment."""

    def test_no_keys_raises(self, monkeypatch):
        for var in ["GROK_API_KEYS", "GROK_API_KEY", "GEMINI_API_KEYS",
                     "GEMINI_API_KEY", "VERTEX_API_KEYS", "VERTEX_API_KEY"]:
            monkeypatch.delenv(var, raising=False)
        with pytest.raises(FatalError, match="No API keys"):
            load_keys()

    def test_single_grok_key(self, monkeypatch):
        for var in ["GROK_API_KEYS", "GEMINI_API_KEYS", "GEMINI_API_KEY",
                     "VERTEX_API_KEYS", "VERTEX_API_KEY"]:
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("GROK_API_KEY", "xai-test123")
        keys = load_keys()
        assert len(keys) == 1
        assert keys[0]["provider"] == "grok"
        assert keys[0]["key"] == "xai-test123"

    def test_multi_keys(self, monkeypatch):
        for var in ["GROK_API_KEY", "GEMINI_API_KEY", "VERTEX_API_KEYS", "VERTEX_API_KEY"]:
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("GROK_API_KEYS", "xai-a,xai-b")
        monkeypatch.setenv("GEMINI_API_KEYS", "gem-1")
        keys = load_keys()
        assert len(keys) == 3
        assert keys[0]["provider"] == "grok"
        assert keys[2]["provider"] == "gemini"


class TestTierClassifier:
    """Pricing-tier classifier — drives the startup banner + UI badge.

    Catches the silent footgun where someone adds VERTEX_API_KEYS to .env
    expecting "more capacity" without realizing every Vertex call gets billed.
    """

    def test_only_gemini_is_free(self):
        from pipeline.providers.factory import classify_tier
        keys = [{"provider": "gemini", "key": "g1"}, {"provider": "gemini", "key": "g2"}]
        info = classify_tier(keys)
        assert info["tier"] == "free"
        assert info["free_count"] == 2
        assert info["paid_count"] == 0
        assert info["by_provider"] == {"gemini": 2}

    def test_only_vertex_is_paid(self):
        from pipeline.providers.factory import classify_tier
        info = classify_tier([{"provider": "vertex", "key": "v1"}])
        assert info["tier"] == "paid"
        assert info["paid_count"] == 1

    def test_only_grok_is_paid(self):
        from pipeline.providers.factory import classify_tier
        info = classify_tier([{"provider": "grok", "key": "xai-1"}])
        assert info["tier"] == "paid"

    def test_gemini_plus_vertex_is_mixed(self):
        """Most dangerous footgun: user thinks "I have free Gemini" but
        also added a Vertex key — round-robin will route some calls to
        the paid provider. classify_tier must flag this loudly."""
        from pipeline.providers.factory import classify_tier
        info = classify_tier([
            {"provider": "gemini", "key": "g1"},
            {"provider": "vertex", "key": "v1"},
        ])
        assert info["tier"] == "mixed"
        assert info["free_count"] == 1
        assert info["paid_count"] == 1

    def test_empty_keys(self):
        from pipeline.providers.factory import classify_tier
        info = classify_tier([])
        assert info["tier"] == "empty"
        assert info["free_count"] == 0
        assert info["paid_count"] == 0
        assert info["by_provider"] == {}

    def test_unknown_provider_treated_as_paid(self):
        """Defensive: an unrecognized provider name should classify as paid
        (not free) — better to false-flag than miss a billable provider."""
        from pipeline.providers.factory import classify_tier
        info = classify_tier([{"provider": "openai", "key": "sk-1"}])
        assert info["tier"] == "paid"
        assert info["paid_count"] == 1
