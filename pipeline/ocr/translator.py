"""OCR translator -- translate detected on-screen text."""

import unicodedata

from pipeline.config import cfg
from pipeline.logger import get_logger
from pipeline.translate import translate_segments

log = get_logger("OCR")


def translate_ocr_texts(
    text_groups: list[dict],
    source_lang: str,
    target_lang: str,
) -> list[dict]:
    """Translate unique OCR texts using existing Gemini translate infrastructure."""
    unique_texts = list(set(g["text"] for g in text_groups))
    if not unique_texts:
        return text_groups

    # Build pseudo-segments for the translator
    segments = [{"start": 0, "end": 1, "text": t} for t in unique_texts]
    translated = translate_segments(segments, source_lang, target_lang, batch_size=20)

    # Build lookup
    translation_map = {seg["text"]: seg["translated_text"] for seg in translated}

    for group in text_groups:
        translated_text = translation_map.get(group["text"], group["text"])
        # Normalize to NFC -- ensures Vietnamese diacritics are precomposed
        group["translated_text"] = unicodedata.normalize("NFC", translated_text)

    log.info(f"Translated {len(unique_texts)} unique on-screen texts")
    return text_groups
