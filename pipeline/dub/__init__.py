"""Dubbing pipeline — TTS generation, audio mixing, and video dubbing."""

from .mixer import build_dubbed_audio, dub_video
from .separator import get_audio_duration, separate_audio
from .tts import DEFAULT_VOICES, get_voice_for_lang

__all__ = [
    "build_dubbed_audio",
    "dub_video",
    "DEFAULT_VOICES",
    "get_voice_for_lang",
    "get_audio_duration",
    "separate_audio",
]
