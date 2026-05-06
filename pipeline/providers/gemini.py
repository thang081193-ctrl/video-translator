"""Google Gemini translation provider — using genai client library."""

from __future__ import annotations

from google import genai
from google.genai import types

from pipeline import quota
from pipeline.config import cfg
from pipeline.logger import get_logger
from pipeline.providers.base import TranslationProvider

log = get_logger("Gemini")


# All categories disabled — translator is a closed-domain task and false-positive
# safety blocks (e.g. on movie dialogue containing violence/profanity) cause
# count-mismatch retries. Gemini still applies a non-overrideable abuse layer.
_SAFETY_OFF = [
    types.SafetySetting(category=c, threshold=types.HarmBlockThreshold.BLOCK_NONE)
    for c in (
        types.HarmCategory.HARM_CATEGORY_HARASSMENT,
        types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
        types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
        types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
    )
]


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

    def generate(self, prompt: str, system_instruction: str | None = None) -> str:
        """Call Gemini API and return text response."""
        config = types.GenerateContentConfig(
            temperature=cfg.translate.gemini_temperature,
            response_mime_type="application/json",
            system_instruction=system_instruction,
            safety_settings=_SAFETY_OFF,
        )
        try:
            response = self.client.models.generate_content(
                model=cfg.translate.gemini_model,
                contents=prompt,
                config=config,
            )
        except Exception as e:
            err = str(e)
            if "RESOURCE_EXHAUSTED" in err or "429" in err:
                quota.record_quota_exceeded(self.api_key, err)
            raise
        quota.record_request(self.api_key)
        return response.text
