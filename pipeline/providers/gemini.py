"""Google Gemini translation provider — using genai client library."""

from __future__ import annotations

from google import genai

from pipeline import quota
from pipeline.config import cfg
from pipeline.logger import get_logger
from pipeline.providers.base import TranslationProvider

log = get_logger("Gemini")


class GeminiProvider(TranslationProvider):
    """Google Gemini API provider."""

    name = "gemini"

    def __init__(self, api_key: str):
        self.api_key = api_key
        self._client: genai.Client | None = None

    @property
    def client(self) -> genai.Client:
        if self._client is None:
            self._client = genai.Client(api_key=self.api_key)
        return self._client

    def generate(self, prompt: str) -> str:
        """Call Gemini API and return text response."""
        try:
            response = self.client.models.generate_content(
                model=cfg.translate.gemini_model,
                contents=prompt,
            )
        except Exception as e:
            err = str(e)
            if "RESOURCE_EXHAUSTED" in err or "429" in err:
                quota.record_quota_exceeded(self.api_key, err)
            raise
        quota.record_request(self.api_key)
        return response.text
