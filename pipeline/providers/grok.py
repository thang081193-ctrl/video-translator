"""Grok (xAI) translation provider — OpenAI-compatible HTTP API."""

from __future__ import annotations

import json
import urllib.request
import urllib.error

from pipeline.config import cfg
from pipeline.logger import get_logger
from pipeline.providers.base import TranslationProvider

log = get_logger("Grok")


class GrokProvider(TranslationProvider):
    """xAI Grok API provider using direct HTTP calls."""

    name = "grok"

    def __init__(self, api_key: str):
        self.api_key = api_key

    def generate(self, prompt: str) -> str:
        """Call Grok API and return text response."""
        payload = json.dumps({
            "model": cfg.translate.grok_model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a professional subtitle translator. "
                        "Always respond with ONLY a JSON array of translated strings. "
                        "No explanation, no markdown formatting."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": cfg.translate.grok_temperature,
        }).encode("utf-8")

        req = urllib.request.Request(
            cfg.translate.grok_endpoint,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=cfg.translate.grok_timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", errors="replace")[:500]
            except Exception:
                pass
            raise RuntimeError(f"Grok API {e.code}: {body}") from e
