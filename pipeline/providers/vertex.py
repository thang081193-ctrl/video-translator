"""Google Vertex AI translation provider — using genai client with vertexai flag."""

from __future__ import annotations

from google import genai
from google.genai import types

from pipeline.config import cfg
from pipeline.logger import get_logger
from pipeline.providers.base import TranslationProvider
from pipeline.providers.gemini import _SAFETY_OFF

log = get_logger("Vertex")


class VertexProvider(TranslationProvider):
    """Google Vertex AI provider."""

    name = "vertex"

    def __init__(self, api_key: str):
        self.api_key = api_key
        self._client: genai.Client | None = None

    @property
    def client(self) -> genai.Client:
        if self._client is None:
            self._client = genai.Client(vertexai=True, api_key=self.api_key)
        return self._client

    def generate(self, prompt: str, system_instruction: str | None = None) -> str:
        """Call Vertex AI API and return text response."""
        config = types.GenerateContentConfig(
            temperature=cfg.translate.gemini_temperature,
            response_mime_type="application/json",
            system_instruction=system_instruction,
            safety_settings=_SAFETY_OFF,
        )
        response = self.client.models.generate_content(
            model=cfg.translate.gemini_model,
            contents=prompt,
            config=config,
        )
        return response.text
