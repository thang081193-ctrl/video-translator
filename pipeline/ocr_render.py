"""Pillow-based text overlay renderer for OCR text replacement.

Renders translated text onto transparent PNG images that are composited
onto the video via ffmpeg overlay filter. This provides full style control
(font size, color, centering, word-wrap) that ffmpeg drawtext cannot achieve.
"""

import os
import platform
import unicodedata
from typing import Optional

import numpy as np

from pipeline.logger import get_logger

log = get_logger("OCR-Render")

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    raise ImportError(
        "Pillow is required for overlay rendering. Install with: pip install Pillow"
    )


# ---------------------------------------------------------------------------
# Font paths — cross-platform (Pillow native paths)
# ---------------------------------------------------------------------------
if platform.system() == "Windows":
    # Windows: rely on system fonts. Segoe UI Symbol covers most scripts;
    # Arial Unicode (if installed) covers everything. Fallback chain handles gaps.
    FONT_MAP = {
        # CJK
        "zh": "C:/Windows/Fonts/msyh.ttc",
        "ja": "C:/Windows/Fonts/msgothic.ttc",
        "ko": "C:/Windows/Fonts/malgun.ttf",
        # Indic / SE Asia
        "th": "C:/Windows/Fonts/leelawad.ttf",
        "hi": "C:/Windows/Fonts/mangal.ttf",
        "mr": "C:/Windows/Fonts/mangal.ttf",        # Marathi uses Devanagari
        "bn": "C:/Windows/Fonts/vrinda.ttf",        # Bengali
        "te": "C:/Windows/Fonts/gautami.ttf",       # Telugu
        "ta": "C:/Windows/Fonts/latha.ttf",         # Tamil
        # Arabic script
        "ar": "C:/Windows/Fonts/arial.ttf",
        "ur": "C:/Windows/Fonts/arial.ttf",         # Urdu (Nastaliq variant if available)
        "fa": "C:/Windows/Fonts/arial.ttf",         # Farsi/Persian
        # Greek + Cyrillic + Latin (Arial Unicode covers all)
        "el": "C:/Windows/Fonts/arial.ttf",
        "vi": "C:/Windows/Fonts/arial.ttf",
    }
    DEFAULT_FONT = "C:/Windows/Fonts/arial.ttf"
    FALLBACK_FONTS = [
        "C:/Windows/Fonts/seguisym.ttf",            # Segoe UI Symbol — wide coverage
        "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/tahoma.ttf",
        "C:/Windows/Fonts/times.ttf",
    ]
else:
    # Linux (Docker container with Noto fonts installed). All Noto fonts MUST
    # be installed via Dockerfile (fonts-noto, fonts-noto-bengali, etc.).
    FONT_MAP = {
        # CJK
        "zh": "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "ja": "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "ko": "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        # SE Asia
        "th": "/usr/share/fonts/truetype/noto/NotoSansThai-Regular.ttf",
        # Indic
        "hi": "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf",
        "mr": "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf",
        "bn": "/usr/share/fonts/truetype/noto/NotoSansBengali-Regular.ttf",
        "te": "/usr/share/fonts/truetype/noto/NotoSansTelugu-Regular.ttf",
        "ta": "/usr/share/fonts/truetype/noto/NotoSansTamil-Regular.ttf",
        # Arabic script
        "ar": "/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf",
        "ur": "/usr/share/fonts/truetype/noto/NotoNastaliqUrdu-Regular.ttf",
        "fa": "/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf",
        # Greek (NotoSans handles Greek + Cyrillic too, but explicit is clearer)
        "el": "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
        # Latin / Cyrillic (NotoSans-Regular)
        "vi": "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
    }
    DEFAULT_FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    FALLBACK_FONTS = [
        "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]


def _font_has_glyph(font: ImageFont.FreeTypeFont, char: str) -> bool:
    """Check if a font can render a specific character (no tofu/□)."""
    try:
        # Pillow's getbbox returns None for unsupported glyphs in some versions
        bbox = font.getbbox(char)
        if bbox is None:
            return False
        # Check if glyph has non-zero width (zero width = missing glyph)
        return (bbox[2] - bbox[0]) > 0
    except Exception:
        return False


def _check_glyph_coverage(font: ImageFont.FreeTypeFont, text: str) -> float:
    """Return ratio of characters in text that the font can render."""
    if not text:
        return 1.0
    # Sample unique non-ASCII characters from the text
    test_chars = set(c for c in text if ord(c) > 127)
    if not test_chars:
        return 1.0  # ASCII-only text, all fonts handle this
    covered = sum(1 for c in test_chars if _font_has_glyph(font, c))
    return covered / len(test_chars)


def _select_font(target_lang: str, size: int, sample_text: str = "") -> ImageFont.FreeTypeFont:
    """
    Select and load the appropriate font for the target language.

    Uses a fallback chain with glyph coverage check to avoid tofu (□) characters.
    If sample_text is provided, verifies the font can render it.
    """
    # Build candidate list: preferred font first, then fallbacks
    candidates = []
    preferred = FONT_MAP.get(target_lang, DEFAULT_FONT)
    candidates.append(preferred)
    if preferred != DEFAULT_FONT:
        candidates.append(DEFAULT_FONT)
    candidates.extend(FALLBACK_FONTS)
    # Deduplicate while preserving order
    seen = set()
    candidates = [c for c in candidates if not (c in seen or seen.add(c))]

    best_font = None
    best_coverage = -1.0

    for font_path in candidates:
        try:
            font = ImageFont.truetype(font_path, size)
        except (OSError, IOError):
            continue

        if not sample_text:
            return font  # No text to check, use first available

        coverage = _check_glyph_coverage(font, sample_text)
        if coverage >= 1.0:
            return font  # Perfect coverage, use immediately
        if coverage > best_coverage:
            best_coverage = coverage
            best_font = font

    if best_font is not None:
        # Warn if best available font still has poor coverage
        # (output will likely show tofu characters for missing glyphs)
        if best_coverage < 0.8:
            log.warning(
                f"Font glyph coverage low for lang={target_lang}: "
                f"{best_coverage:.0%} — install Noto fonts for missing scripts"
            )
        return best_font

    log.warning(f"No font with any glyph coverage found for lang={target_lang}, using Pillow default")
    # Last resort
    return ImageFont.load_default()


def _is_cjk_text(text: str) -> bool:
    """Check if text contains CJK characters (no spaces for word boundary)."""
    for ch in text:
        cp = ord(ch)
        if (0x4E00 <= cp <= 0x9FFF or   # CJK Unified
            0x3040 <= cp <= 0x309F or    # Hiragana
            0x30A0 <= cp <= 0x30FF or    # Katakana
            0xAC00 <= cp <= 0xD7AF):     # Hangul
            return True
    return False


def wrap_text_to_bbox(
    text: str,
    font: ImageFont.FreeTypeFont,
    bbox_width: int,
) -> list[str]:
    """
    Word-wrap text to fit within bbox_width.

    For Latin text: wraps at word boundaries.
    For CJK text: wraps at character boundaries.

    Returns list of lines.
    """
    if not text:
        return [""]

    if _is_cjk_text(text):
        # Character-level wrapping for CJK
        lines = []
        current_line = ""
        for char in text:
            test = current_line + char
            bbox = font.getbbox(test)
            w = bbox[2] - bbox[0]
            if w > bbox_width and current_line:
                lines.append(current_line)
                current_line = char
            else:
                current_line = test
        if current_line:
            lines.append(current_line)
        return lines if lines else [""]

    # Word-level wrapping for Latin scripts
    words = text.split()
    if not words:
        return [""]

    lines = []
    current_line = words[0]

    for word in words[1:]:
        test = current_line + " " + word
        bbox = font.getbbox(test)
        w = bbox[2] - bbox[0]
        if w > bbox_width:
            lines.append(current_line)
            current_line = word
        else:
            current_line = test

    if current_line:
        lines.append(current_line)

    return lines if lines else [""]


def _fit_font_size(
    text: str,
    font_getter,
    bbox_width: int,
    bbox_height: int,
    max_size: int,
    min_size: int = 10,
) -> tuple[ImageFont.FreeTypeFont, list[str], int]:
    """
    Binary search for the largest font size that fits text within bbox.

    Returns (font, wrapped_lines, font_size).
    """
    best_size = min_size
    best_font = font_getter(min_size)
    best_lines = [text]

    lo, hi = min_size, max_size
    while lo <= hi:
        mid = (lo + hi) // 2
        font = font_getter(mid)
        lines = wrap_text_to_bbox(text, font, bbox_width)

        # Calculate total text height
        line_height = mid + 4  # font size + line spacing
        total_height = line_height * len(lines)

        if total_height <= bbox_height:
            best_size = mid
            best_font = font
            best_lines = lines
            lo = mid + 1
        else:
            hi = mid - 1

    return best_font, best_lines, best_size


def _hex_to_rgba(hex_color: str, alpha: int = 255) -> tuple[int, int, int, int]:
    """Convert hex color string to RGBA tuple."""
    hex_color = hex_color.lstrip("#")
    if len(hex_color) == 6:
        r, g, b = int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16)
        return (r, g, b, alpha)
    return (255, 255, 255, alpha)


def render_single_overlay(
    text: str,
    bbox: list[int],
    style: dict,
    video_width: int,
    video_height: int,
    target_lang: str,
    inpaint_patch: Optional[np.ndarray] = None,
) -> Image.Image:
    """
    Render a single text overlay as a transparent RGBA image (full video size).

    Args:
        text: Translated text to render.
        bbox: [x1, y1, x2, y2] bounding box of original text.
        style: Style dict with fg_color, bg_color, font_height_px, bg_opacity.
        video_width: Video width in pixels.
        video_height: Video height in pixels.
        target_lang: Target language code (for font selection).
        inpaint_patch: Optional inpainted background patch (numpy RGBA array).

    Returns:
        PIL Image (RGBA) of full video size with the overlay rendered.
    """
    img = Image.new("RGBA", (video_width, video_height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Normalize Unicode to NFC — critical for Vietnamese diacritics
    text = unicodedata.normalize("NFC", text)

    x1, y1, x2, y2 = bbox
    bbox_w = x2 - x1
    bbox_h = y2 - y1
    padding = 8

    # Style extraction
    fg_color = style.get("fg_color", "#FFFFFF")
    bg_color = style.get("bg_color", "#000000")
    bg_opacity = 1.0  # Fully opaque to completely cover original text

    # --- Background: inpaint patch or solid color ---
    bg_x1 = max(0, x1 - padding)
    bg_y1 = max(0, y1 - padding)
    bg_x2 = min(video_width, x2 + padding)
    bg_y2 = min(video_height, y2 + padding)

    if inpaint_patch is not None:
        # Paste inpainted patch as background
        try:
            patch_img = Image.fromarray(inpaint_patch)
            if patch_img.mode != "RGBA":
                patch_img = patch_img.convert("RGBA")
            # Resize to match bbox if needed
            target_size = (bg_x2 - bg_x1, bg_y2 - bg_y1)
            if patch_img.size != target_size:
                patch_img = patch_img.resize(target_size, Image.LANCZOS)
            img.paste(patch_img, (bg_x1, bg_y1))
        except Exception:
            # Fallback to solid color
            bg_rgba = _hex_to_rgba(bg_color, int(bg_opacity * 255))
            draw.rectangle([bg_x1, bg_y1, bg_x2, bg_y2], fill=bg_rgba)
    else:
        bg_rgba = _hex_to_rgba(bg_color, int(bg_opacity * 255))
        draw.rectangle([bg_x1, bg_y1, bg_x2, bg_y2], fill=bg_rgba)

    # --- Text rendering (with glyph coverage check) ---
    font_getter = lambda size: _select_font(target_lang, size, sample_text=text)
    max_font_size = max(14, int(bbox_h * 0.85))
    font, lines, font_size = _fit_font_size(
        text, font_getter, bbox_w, bbox_h, max_font_size
    )

    fg_rgba = _hex_to_rgba(fg_color, 255)
    line_height = font_size + 4

    # Calculate total text block height for vertical centering
    total_text_height = line_height * len(lines)
    text_y_start = y1 + (bbox_h - total_text_height) // 2

    for i, line in enumerate(lines):
        line_bbox = font.getbbox(line)
        line_w = line_bbox[2] - line_bbox[0]
        # Center horizontally
        line_x = x1 + (bbox_w - line_w) // 2
        line_y = text_y_start + i * line_height

        # Draw text shadow for readability (1px offset)
        shadow_color = _hex_to_rgba(bg_color, 180)
        draw.text((line_x + 1, line_y + 1), line, font=font, fill=shadow_color)
        # Draw main text
        draw.text((line_x, line_y), line, font=font, fill=fg_rgba)

    return img


def _has_bbox_timeline(text_groups: list[dict]) -> bool:
    """Check if any text group has a bbox_timeline (premium tracked mode)."""
    return any("bbox_timeline" in g and len(g.get("bbox_timeline", [])) > 1
               for g in text_groups)


def _render_tracked_overlays(
    text_groups: list[dict],
    video_width: int,
    video_height: int,
    output_dir: str,
    target_lang: str,
    inpaint_patches: Optional[dict[int, np.ndarray]],
) -> list[dict]:
    """
    Render overlays for tracked (premium) mode.

    Creates separate overlay PNGs for each keyframe in the bbox_timeline,
    so text position follows camera movement.
    """
    overlays = []
    overlay_idx = 0

    for gi, g in enumerate(text_groups):
        translated = g.get("translated_text", g["text"])
        style = g.get("style", {})
        patch = inpaint_patches.get(gi) if inpaint_patches else None
        timeline = g.get("bbox_timeline", [])

        if len(timeline) <= 1:
            # No tracking data — single static overlay
            start = g["start_time"]
            end = g["end_time"]
            single = render_single_overlay(
                text=translated, bbox=g["bbox"], style=style,
                video_width=video_width, video_height=video_height,
                target_lang=target_lang, inpaint_patch=patch,
            )
            overlay_path = os.path.join(output_dir, f"overlay_{overlay_idx:04d}.png")
            single.save(overlay_path, "PNG")
            overlays.append({"path": overlay_path, "start_time": start, "end_time": end})
            overlay_idx += 1
            continue

        # Create one overlay per keyframe interval in the timeline
        for ki in range(len(timeline)):
            t_start, bbox_at_t = timeline[ki]
            # End time: next keyframe's time, or group end_time for the last keyframe
            if ki + 1 < len(timeline):
                t_end = timeline[ki + 1][0]
            else:
                t_end = g["end_time"]

            if t_end <= t_start:
                continue

            single = render_single_overlay(
                text=translated, bbox=bbox_at_t, style=style,
                video_width=video_width, video_height=video_height,
                target_lang=target_lang, inpaint_patch=patch,
            )
            overlay_path = os.path.join(output_dir, f"overlay_{overlay_idx:04d}.png")
            single.save(overlay_path, "PNG")
            overlays.append({"path": overlay_path, "start_time": t_start, "end_time": t_end})
            overlay_idx += 1

    return overlays


def render_text_overlays(
    text_groups: list[dict],
    video_width: int,
    video_height: int,
    output_dir: str,
    target_lang: str = "en",
    inpaint_patches: Optional[dict[int, np.ndarray]] = None,
) -> list[dict]:
    """
    Render overlay PNG images for all text groups.

    For tracked groups (premium mode with bbox_timeline), creates per-keyframe
    overlays so text follows camera movement. For static groups (fast/quality),
    merges overlapping time ranges into single composite PNGs.

    Returns:
        List of {"path": str, "start_time": float, "end_time": float} for each overlay.
    """
    os.makedirs(output_dir, exist_ok=True)

    # Premium tracked mode: per-keyframe overlays
    if _has_bbox_timeline(text_groups):
        overlays = _render_tracked_overlays(
            text_groups, video_width, video_height,
            output_dir, target_lang, inpaint_patches,
        )
        log.info(f"Rendered {len(overlays)} tracked overlay images")
        return overlays

    # Static mode: group by time range and merge into composite PNGs
    overlays = []
    time_groups: dict[tuple[float, float], list[tuple[int, dict]]] = {}
    for i, g in enumerate(text_groups):
        key = (g["start_time"], g["end_time"])
        if key not in time_groups:
            time_groups[key] = []
        time_groups[key].append((i, g))

    overlay_idx = 0
    for (start, end), items in time_groups.items():
        composite = Image.new("RGBA", (video_width, video_height), (0, 0, 0, 0))

        for group_idx, g in items:
            translated = g.get("translated_text", g["text"])
            style = g.get("style", {})
            patch = inpaint_patches.get(group_idx) if inpaint_patches else None

            single = render_single_overlay(
                text=translated, bbox=g["bbox"], style=style,
                video_width=video_width, video_height=video_height,
                target_lang=target_lang, inpaint_patch=patch,
            )
            composite = Image.alpha_composite(composite, single)

        overlay_path = os.path.join(output_dir, f"overlay_{overlay_idx:04d}.png")
        composite.save(overlay_path, "PNG")
        overlays.append({"path": overlay_path, "start_time": start, "end_time": end})
        overlay_idx += 1

    log.info(f"Rendered {len(overlays)} overlay images")
    return overlays
