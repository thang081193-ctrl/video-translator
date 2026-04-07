"""Base class for translation providers + key rotation logic."""

from __future__ import annotations

import re
import time
from abc import ABC, abstractmethod

from pipeline.config import cfg
from pipeline.logger import get_logger

log = get_logger("Provider")


class TranslationProvider(ABC):
    """Abstract base for all translation API providers."""

    name: str = "base"

    @abstractmethod
    def generate(self, prompt: str) -> str:
        """Send prompt to API, return raw text response."""
        ...

    @staticmethod
    def is_rate_limit_error(error_str: str) -> bool:
        """Check if error is a rate limit / quota error."""
        return any(kw in error_str for kw in (
            "429", "RESOURCE_EXHAUSTED", "rate_limit", "too many requests",
            "quota", "rate limit",
        ))

    @staticmethod
    def extract_retry_seconds(error_text: str, default: int = 5) -> int:
        """Best-effort parse retry delay from API error text."""
        m = re.search(r"retryDelay\\?\"?\s*[:=]\s*\\?\"?(\d+)s", error_text)
        if m:
            return max(1, int(m.group(1)))
        m = re.search(r"retry(?:ing)?\s+in\s+(\d+)s", error_text, flags=re.IGNORECASE)
        if m:
            return max(1, int(m.group(1)))
        return default

    @staticmethod
    def compact_error(error: Exception | str, max_len: int | None = None) -> str:
        """Compact error text for UI/log display."""
        if max_len is None:
            max_len = cfg.translate.error_max_length
        text = str(error)
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) <= max_len:
            return text
        return text[: max_len - 3] + "..."


class KeyRotator:
    """Round-robin API key rotator with automatic switch on rate limit."""

    def __init__(self, keys: list[dict[str, str]], providers: dict[str, TranslationProvider]):
        if not keys:
            raise ValueError(
                "No API keys provided. Set GROK_API_KEYS, GEMINI_API_KEYS, "
                "and/or VERTEX_API_KEYS in .env"
            )
        self.keys = keys
        self.providers = providers
        self.index = 0

    @property
    def current_key(self) -> dict[str, str]:
        return self.keys[self.index]

    @property
    def current_provider(self) -> TranslationProvider:
        return self.providers[self.current_key["provider"]]

    @property
    def current_provider_label(self) -> str:
        labels = {"grok": "Grok", "vertex": "Vertex AI", "gemini": "Gemini"}
        return labels.get(self.current_key["provider"], self.current_key["provider"])

    def generate(self, prompt: str) -> str:
        """Call current provider's generate method."""
        return self.current_provider.generate(prompt)

    def rotate(self) -> bool:
        """Switch to next key. Returns True if we haven't looped back to start."""
        prev = self.index
        self.index = (self.index + 1) % len(self.keys)
        key_num = self.index + 1
        total = len(self.keys)
        log.info(f"Switching to API key {key_num}/{total} ({self.current_provider_label})")
        return self.index != prev

    def rotate_all_exhausted(self, start_index: int) -> bool:
        """Check if we've tried all keys (full loop)."""
        return self.index == start_index
