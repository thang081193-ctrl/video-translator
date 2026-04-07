"""Google Vertex AI translation provider — using genai client with vertexai flag."""

from __future__ import annotations

from google import genai

from pipeline.config import cfg
from pipeline.logger import get_logger
from pipeline.providers.base import TranslationProvider

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

    def generate(self, prompt: str) -> str:
        """Call Vertex AI API and return text response."""
        response = self.client.models.generate_content(
            model=cfg.translate.gemini_model,
            contents=prompt,
        )
        return response.text
