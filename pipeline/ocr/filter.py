"""OCR filter -- drawtext generation, overlay images, and inpainting."""

import os
import platform
import unicodedata

import cv2
import numpy as np

from pipeline.config import cfg
from pipeline.logger import get_logger

log = get_logger("OCR")


# ---------------------------------------------------------------------------
# Font maps -- cross-platform (ffmpeg drawtext paths)
# ---------------------------------------------------------------------------
if platform.system() == "Windows":
    # ffmpeg on Windows accepts /Windows/Fonts/ paths (without drive letter).
    # Used for ffmpeg drawtext (fast OCR mode). For most non-CJK scripts the
    # pipeline auto-upgrades to overlay mode which uses Pillow (ocr_render.py).
    FONT_MAP = {
        # CJK
        "zh": "/Windows/Fonts/msyh.ttc",
        "ja": "/Windows/Fonts/msgothic.ttc",
        "ko": "/Windows/Fonts/malgun.ttf",
        # SE Asia
        "th": "/Windows/Fonts/leelawad.ttf",
        # Indic
        "hi": "/Windows/Fonts/mangal.ttf",
        "mr": "/Windows/Fonts/mangal.ttf",
        "bn": "/Windows/Fonts/vrinda.ttf",
        "te": "/Windows/Fonts/gautami.ttf",
        "ta": "/Windows/Fonts/latha.ttf",
        # Arabic script (RTL)
        "ar": "/Windows/Fonts/arial.ttf",
        "ur": "/Windows/Fonts/arial.ttf",
        "fa": "/Windows/Fonts/arial.ttf",
        # Greek + Latin
        "el": "/Windows/Fonts/arial.ttf",
        "vi": "/Windows/Fonts/arial.ttf",
    }
    DEFAULT_FONT = "/Windows/Fonts/arial.ttf"
    FONT_FALLBACKS = [
        "/Windows/Fonts/seguisym.ttf",
        "/Windows/Fonts/segoeui.ttf",
        "/Windows/Fonts/tahoma.ttf",
        "/Windows/Fonts/msyh.ttc",
    ]
else:
    # Linux (Docker container with Noto fonts installed via Dockerfile).
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
        # Arabic script (RTL)
        "ar": "/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf",
        "ur": "/usr/share/fonts/truetype/noto/NotoNastaliqUrdu-Regular.ttf",
        "fa": "/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf",
        # Greek + Latin / Cyrillic
        "el": "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
        "vi": "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
    }
    DEFAULT_FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    FONT_FALLBACKS = [
        "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_ffmpeg_font(target_lang: str) -> str:
    """Resolve font path for ffmpeg drawtext, with fallback chain."""
    primary = FONT_MAP.get(target_lang, DEFAULT_FONT)
    if platform.system() == "Windows":
        native_path = "C:" + primary.replace("/", "\\\\")
        if os.path.isfile(native_path):
            return primary
        for fb in FONT_FALLBACKS:
            native_fb = "C:" + fb.replace("/", "\\\\")
            if os.path.isfile(native_fb):
                return fb
    else:
        if os.path.isfile(primary):
            return primary
        for fb in FONT_FALLBACKS:
            if os.path.isfile(fb):
                return fb
    return DEFAULT_FONT


def _escape_drawtext(text: str) -> str:
    """Escape text for ffmpeg drawtext filter (passed via subprocess, no shell)."""
    # Normalize Unicode to NFC (precomposed) -- critical for Vietnamese diacritics
    text = unicodedata.normalize("NFC", text)
    # ffmpeg drawtext escaping: https://ffmpeg.org/ffmpeg-filters.html#drawtext-1
    # Must escape: \ ' : ; ,  and also [ ] =
    # Order: backslash first, then the rest
    text = text.replace("\\", "\\\\")
    text = text.replace("'", "\u2019")  # replace with Unicode right single quote
    text = text.replace(":", "\\:")
    text = text.replace("%", "%%")
    return text


# ---------------------------------------------------------------------------
# Inpainting
# ---------------------------------------------------------------------------

def inpaint_text_region(
    frame: np.ndarray,
    bbox: list[int],
    method: str = "telea",
) -> np.ndarray:
    """
    Remove text from a frame region using OpenCV inpainting.

    Creates a text mask via Otsu threshold, dilates it, then inpaints
    to reconstruct the background behind the text.

    Args:
        frame: Full frame as BGR numpy array.
        bbox: [x1, y1, x2, y2] bounding box of text region.
        method: Inpainting method -- "telea" (default) or "ns" (Navier-Stokes).

    Returns:
        Inpainted crop as BGRA numpy array (with alpha=255).
    """
    h_img, w_img = frame.shape[:2]
    x1, y1, x2, y2 = bbox
    padding = 6
    x1p = max(0, x1 - padding)
    y1p = max(0, y1 - padding)
    x2p = min(w_img, x2 + padding)
    y2p = min(h_img, y2 + padding)

    crop = frame[y1p:y2p, x1p:x2p].copy()
    if crop.size == 0:
        return crop

    # Create text mask via Otsu on V-channel
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    v_channel = hsv[:, :, 2]
    _, mask = cv2.threshold(v_channel, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    # Ensure text pixels are white in mask (minority = text)
    fg_count = np.count_nonzero(mask)
    if fg_count > mask.size - fg_count:
        mask = cv2.bitwise_not(mask)

    # Dilate mask to cover anti-aliasing edges
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    mask = cv2.dilate(mask, kernel, iterations=2)

    # Inpaint
    flag = cv2.INPAINT_TELEA if method == "telea" else cv2.INPAINT_NS
    inpainted = cv2.inpaint(crop, mask, inpaintRadius=5, flags=flag)

    # Convert to BGRA
    alpha = np.full((*inpainted.shape[:2], 1), 255, dtype=np.uint8)
    inpainted_rgba = np.concatenate([inpainted, alpha], axis=2)

    return inpainted_rgba


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_drawtext_filter(
    text_groups: list[dict],
    video_width: int,
    video_height: int,
    target_lang: str = "en",
) -> str:
    """
    Generate ffmpeg filter string that covers original text and draws translated text.

    Uses extracted style (colors, font size) from each text group when available,
    centers text within bbox, and auto-fits font size to prevent overflow.
    """
    font_path = _resolve_ffmpeg_font(target_lang)
    filters = []

    for g in text_groups:
        x1, y1, x2, y2 = g["bbox"]
        translated = g.get("translated_text", g["text"])
        style = g.get("style", {})

        start = g["start_time"]
        end = g["end_time"]
        padding = 4

        bbox_w = x2 - x1
        bbox_h = y2 - y1

        # --- Extract style or use defaults ---
        bg_color = style.get("bg_color", "#000000").replace("#", "0x")
        fg_color = style.get("fg_color", "#FFFFFF").replace("#", "0x")
        bg_opacity = style.get("bg_opacity", 0.85)

        # 1. Cover original text with background-colored box
        filters.append(
            f"drawbox=x={x1 - padding}:y={y1 - padding}"
            f":w={bbox_w + padding * 2}:h={bbox_h + padding * 2}"
            f":color={bg_color}@{bg_opacity:.2f}:t=fill"
            f":enable='between(t,{start:.2f},{end:.2f})'"
        )

        # 2. Auto-fit font size: start from bbox height, shrink if text overflows width
        font_size = max(14, int(bbox_h * 0.75))
        # Estimate rendered width -- 0.55 * font_size per char is a rough average
        estimated_width = len(translated) * font_size * 0.55
        if estimated_width > bbox_w and len(translated) > 0:
            font_size = max(14, int(bbox_w / (len(translated) * 0.55)))

        escaped = _escape_drawtext(translated)

        # 3. Draw translated text -- centered horizontally and vertically within bbox
        # ffmpeg drawtext supports text_w / text_h dynamic variables
        center_x = f"{x1}+({bbox_w}-text_w)/2"
        center_y = f"{y1}+({bbox_h}-text_h)/2"

        filters.append(
            f"drawtext=text='{escaped}'"
            f":fontfile={font_path}"
            f":x={center_x}:y={center_y}"
            f":fontsize={font_size}:fontcolor={fg_color}"
            f":enable='between(t,{start:.2f},{end:.2f})'"
        )

    return ",".join(filters)


def generate_overlay_images(
    text_groups: list[dict],
    video_width: int,
    video_height: int,
    output_dir: str,
    target_lang: str = "en",
    inpaint_patches: dict[int, np.ndarray] | None = None,
) -> list[dict]:
    """
    Generate pre-rendered overlay PNGs for all text groups using Pillow.

    This is the Phase 2 replacement for generate_drawtext_filter().
    Returns list of {"path": str, "start_time": float, "end_time": float}.
    """
    from pipeline.ocr_render import render_text_overlays

    overlay_dir = os.path.join(output_dir, "_ocr_overlays")
    return render_text_overlays(
        text_groups=text_groups,
        video_width=video_width,
        video_height=video_height,
        output_dir=overlay_dir,
        target_lang=target_lang,
        inpaint_patches=inpaint_patches,
    )


def inpaint_all_regions(
    video_path: str,
    text_groups: list[dict],
    frames_dir: str,
) -> dict[int, np.ndarray]:
    """
    Inpaint text regions for all text groups.

    Extracts the frame closest to each group's start_time and inpaints
    the text region. Returns a dict mapping group index to inpainted patch.
    """
    patches: dict[int, np.ndarray] = {}

    # Collect unique frame times needed
    frame_map: dict[float, str] = {}
    if os.path.isdir(frames_dir):
        for f in sorted(os.listdir(frames_dir)):
            if f.startswith("frame_") and f.endswith(".jpg"):
                # frame_0001.jpg -> index 0 -> time = index * interval
                try:
                    idx = int(f.replace("frame_", "").replace(".jpg", "")) - 1
                    frame_map[idx * 2.0] = os.path.join(frames_dir, f)
                except ValueError:
                    pass

    for gi, g in enumerate(text_groups):
        target_time = g["start_time"]
        # Find closest frame
        best_path = None
        best_diff = float("inf")
        for t, path in frame_map.items():
            diff = abs(t - target_time)
            if diff < best_diff:
                best_diff = diff
                best_path = path

        if best_path is None:
            continue

        frame = cv2.imread(best_path)
        if frame is None:
            continue

        try:
            patch = inpaint_text_region(frame, g["bbox"])
            patches[gi] = patch
        except Exception:
            continue

    log.info(f"Inpainted {len(patches)} text regions")
    return patches
