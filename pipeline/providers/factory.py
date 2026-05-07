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

# Pricing tier of each provider as of 2026-05.
# - "free": has a free tier sufficient for sustained dev/personal use
# - "paid": no free tier (or only trial credits) — every call is billed
# This is what the startup banner uses to tell the user whether they're
# operating in $0 mode or running up a bill. If you add a new provider,
# update this table or the tier banner will misclassify.
PROVIDER_TIERS: dict[str, str] = {
    "gemini": "free",   # Gemini API: ~1000-1500 RPD/key free for Flash-Lite
    "grok": "paid",     # xAI: $0.30/1M input tokens, no free tier
    "vertex": "paid",   # Vertex AI: enterprise GCP product, every call billed
}


def classify_tier(keys: list[dict[str, str]]) -> dict:
    """Summarize loaded keys by pricing tier.

    Returns a dict with:
    - tier: "free" | "paid" | "mixed" | "empty"
    - free_count, paid_count: per-tier key counts
    - by_provider: {provider_name: count}

    Used by the startup banner and /api/quota so the user can see at a
    glance whether the running config is $0 or potentially billable.
    """
    free_count = 0
    paid_count = 0
    by_provider: dict[str, int] = {}
    for k in keys:
        provider = k["provider"]
        by_provider[provider] = by_provider.get(provider, 0) + 1
        tier = PROVIDER_TIERS.get(provider, "paid")  # Unknown defaults to paid (safer)
        if tier == "free":
            free_count += 1
        else:
            paid_count += 1

    if not keys:
        tier = "empty"
    elif paid_count == 0:
        tier = "free"
    elif free_count == 0:
        tier = "paid"
    else:
        tier = "mixed"

    return {
        "tier": tier,
        "free_count": free_count,
        "paid_count": paid_count,
        "by_provider": by_provider,
    }


_banner_signature: tuple | None = None


def _log_tier_banner(summary: dict) -> None:
    """Emit a prominent log line describing the pricing posture.

    Idempotent within a process: if the resolved tier signature hasn't
    changed since the last call (same tier + same provider mix), skip the
    log. Stops the banner from spamming once per /api/quota poll (which
    re-runs load_keys every 30s per connected UI client). Re-fires only
    when the env actually changes — that's exactly when you want to know.

    Goal: if the user accidentally adds a paid provider key (Vertex or Grok),
    they see a loud warning at startup instead of discovering the bill later.
    """
    global _banner_signature
    tier = summary["tier"]
    by_provider = summary.get("by_provider", {})
    signature = (tier, tuple(sorted(by_provider.items())))
    if signature == _banner_signature:
        return
    _banner_signature = signature

    breakdown = ", ".join(
        f"{n}× {p.title()}" for p, n in by_provider.items()
    ) or "no keys"

    if tier == "free":
        log.info(f"[TIER] FREE-TIER MODE — {breakdown}, no billable providers loaded.")
    elif tier == "paid":
        log.warning(
            f"[TIER] PAID MODE — {breakdown}. EVERY translation call WILL be billed. "
            f"Remove paid keys from .env to stay free."
        )
    elif tier == "mixed":
        log.warning(
            f"[TIER] MIXED MODE — {breakdown}. Round-robin will route some calls to "
            f"paid providers. Comment out paid provider keys in .env to stay free."
        )


def _reset_banner_for_tests() -> None:
    """Test-only: clear the dedupe so each test sees a fresh banner state."""
    global _banner_signature
    _banner_signature = None


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

    _log_tier_banner(classify_tier(loaded))
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
