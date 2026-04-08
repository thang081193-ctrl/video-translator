"""Provider factory — instantiate providers + build KeyRotator from env."""

from __future__ import annotations

import os

from pipeline.errors import FatalError
from pipeline.logger import get_logger
from pipeline.providers.base import TranslationProvider, KeyRotator
from pipeline.providers.grok import GrokProvider
from pipeline.providers.gemini import GeminiProvider
from pipeline.providers.vertex import VertexProvider

log = get_logger("ProviderFactory")

# Registry of provider classes
PROVIDER_CLASSES: dict[str, type[TranslationProvider]] = {
    "grok": GrokProvider,
    "gemini": GeminiProvider,
    "vertex": VertexProvider,
}


def _split_keys(value: str | None) -> list[str]:
    if not value:
        return []
    return [k.strip() for k in value.split(",") if k.strip()]


def load_keys() -> list[dict[str, str]]:
    """
    Load translation API keys from env.

    Supported env vars (checked in priority order):
    - GROK_API_KEYS / GROK_API_KEY  (default, recommended)
    - GEMINI_API_KEYS / GEMINI_API_KEY
    - VERTEX_API_KEYS / VERTEX_API_KEY
    """
    loaded: list[dict[str, str]] = []

    # Grok keys (highest priority — loaded first)
    grok_keys = _split_keys(os.getenv("GROK_API_KEYS", ""))
    if not grok_keys:
        grok_single = os.getenv("GROK_API_KEY", "").strip()
        if grok_single:
            grok_keys = [grok_single]
    loaded.extend({"provider": "grok", "key": key} for key in grok_keys)

    # Gemini keys
    gemini_keys = _split_keys(os.getenv("GEMINI_API_KEYS", ""))
    if not gemini_keys:
        gemini_single = os.getenv("GEMINI_API_KEY", "").strip()
        if gemini_single:
            gemini_keys = [gemini_single]
    loaded.extend({"provider": "gemini", "key": key} for key in gemini_keys)

    # Vertex keys
    vertex_keys = _split_keys(os.getenv("VERTEX_API_KEYS", ""))
    if not vertex_keys:
        vertex_single = os.getenv("VERTEX_API_KEY", "").strip()
        if vertex_single:
            vertex_keys = [vertex_single]
    loaded.extend({"provider": "vertex", "key": key} for key in vertex_keys)

    if not loaded:
        raise FatalError(
            "No API keys set. Add GROK_API_KEYS, GEMINI_API_KEYS, "
            "and/or VERTEX_API_KEYS to .env"
        )

    return loaded


def build_rotator(keys: list[dict[str, str]]) -> KeyRotator:
    """Build a KeyRotator with provider instances for all keys."""
    providers: dict[str, TranslationProvider] = {}
    for key_info in keys:
        provider_name = key_info["provider"]
        api_key = key_info["key"]
        # Create one provider instance per unique (provider, key) pair
        cache_key = f"{provider_name}:{api_key}"
        if cache_key not in providers:
            cls = PROVIDER_CLASSES.get(provider_name)
            if cls is None:
                raise FatalError(f"Unknown provider: {provider_name}")
            providers[cache_key] = cls(api_key)

    # Map provider names to instances (use the key-specific instance)
    # KeyRotator will look up by current_key["provider"], so we need
    # a way to get the right instance. We'll store per-key providers.
    key_providers: dict[str, TranslationProvider] = {}
    for key_info in keys:
        cache_key = f"{key_info['provider']}:{key_info['key']}"
        key_providers[cache_key] = providers[cache_key]

    return _KeyProviderRotator(keys, key_providers)


class _KeyProviderRotator(KeyRotator):
    """KeyRotator that maps each key to its specific provider instance."""

    def __init__(self, keys: list[dict[str, str]], key_providers: dict[str, TranslationProvider]):
        self.keys = keys
        self._key_providers = key_providers
        self.index = 0

    @property
    def current_provider(self) -> TranslationProvider:
        key_info = self.current_key
        cache_key = f"{key_info['provider']}:{key_info['key']}"
        return self._key_providers[cache_key]
