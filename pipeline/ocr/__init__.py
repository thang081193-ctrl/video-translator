"""OCR pipeline package -- detect, group, translate, and overlay on-screen text."""

from pipeline.ocr.detector import extract_key_frames, detect_text_regions
from pipeline.ocr.grouper import group_persistent_texts, group_persistent_texts_tracked
from pipeline.ocr.translator import translate_ocr_texts
from pipeline.ocr.filter import (
    generate_drawtext_filter,
    generate_overlay_images,
    inpaint_all_regions,
)

__all__ = [
    "extract_key_frames",
    "detect_text_regions",
    "group_persistent_texts",
    "group_persistent_texts_tracked",
    "translate_ocr_texts",
    "generate_drawtext_filter",
    "generate_overlay_images",
    "inpaint_all_regions",
]
