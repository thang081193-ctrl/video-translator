"""OCR detector -- frame extraction and EasyOCR text detection."""

import json
import os
import subprocess
import threading

import cv2
import numpy as np

from pipeline.config import cfg
from pipeline.logger import get_logger

log = get_logger("OCR")

# ---------------------------------------------------------------------------
# EasyOCR reader cache (avoid re-loading models every call)
# ---------------------------------------------------------------------------
_ocr_lock = threading.Lock()
_ocr_readers: dict[str, "easyocr.Reader"] = {}


def _get_ocr_reader(langs: list[str], gpu: bool = True) -> "easyocr.Reader":
    """Get or create a cached EasyOCR Reader for the given languages."""
    import easyocr
    key = f"{','.join(sorted(langs))}|gpu={gpu}"
    with _ocr_lock:
        if key not in _ocr_readers:
            _ocr_readers[key] = easyocr.Reader(langs, gpu=gpu)
        return _ocr_readers[key]


# ---------------------------------------------------------------------------
# Style extraction helpers (used by detect_text_regions)
# ---------------------------------------------------------------------------

def extract_text_style(frame_path: str, bbox: list[int]) -> dict:
    """
    Extract visual style (colors, font height) from a text region in a frame.

    Uses Otsu thresholding on the V-channel (HSV) to separate foreground text
    from background, then computes mean colors for each.

    Returns dict with fg_color, bg_color (hex), font_height_px, bg_opacity.
    """
    img = cv2.imread(frame_path)
    if img is None:
        return {"fg_color": "#FFFFFF", "bg_color": "#000000", "font_height_px": 24, "bg_opacity": 0.85}

    h_img, w_img = img.shape[:2]
    x1, y1, x2, y2 = bbox
    # Clamp to image bounds
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(w_img, x2), min(h_img, y2)

    if x2 <= x1 or y2 <= y1:
        return {"fg_color": "#FFFFFF", "bg_color": "#000000", "font_height_px": 24, "bg_opacity": 0.85}

    crop = img[y1:y2, x1:x2]

    # Convert to HSV -> use V-channel for Otsu threshold
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    v_channel = hsv[:, :, 2]
    _, mask = cv2.threshold(v_channel, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    # Determine which side is text: text is usually the minority of pixels
    fg_count = np.count_nonzero(mask)
    bg_count = mask.size - fg_count
    if fg_count > bg_count:
        # Bright region is bigger -> text is the dark part, invert
        mask = cv2.bitwise_not(mask)

    fg_pixels = crop[mask == 255]
    bg_pixels = crop[mask == 0]

    def _mean_color_hex(pixels) -> str:
        if len(pixels) == 0:
            return "#FFFFFF"
        mean_bgr = np.mean(pixels, axis=0).astype(int)
        return f"#{mean_bgr[2]:02X}{mean_bgr[1]:02X}{mean_bgr[0]:02X}"

    fg_color = _mean_color_hex(fg_pixels)
    bg_color = _mean_color_hex(bg_pixels)

    # Estimate font height from the tallest contour in the mask
    font_height_px = y2 - y1  # fallback
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if contours:
        max_h = max(cv2.boundingRect(c)[3] for c in contours)
        if max_h > 4:
            font_height_px = max_h

    # Check contrast -- if too low, fall back to white on black
    fg_bgr = np.mean(fg_pixels, axis=0) if len(fg_pixels) > 0 else np.array([255, 255, 255])
    bg_bgr = np.mean(bg_pixels, axis=0) if len(bg_pixels) > 0 else np.array([0, 0, 0])
    luminance_diff = abs(float(np.mean(fg_bgr)) - float(np.mean(bg_bgr)))
    if luminance_diff < 40:  # contrast too low, unreliable
        fg_color = "#FFFFFF"
        bg_color = "#000000"

    return {
        "fg_color": fg_color,
        "bg_color": bg_color,
        "font_height_px": int(font_height_px),
        "bg_opacity": 0.85,
    }


# Cache loaded frames to avoid repeated I/O during style extraction
_frame_cache: dict[str, any] = {}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_key_frames(
    video_path: str,
    output_dir: str,
    interval: float = 2.0,
) -> list[dict]:
    """
    Extract frames from video at regular intervals.
    Returns list of {"time": float, "path": str}.
    """
    os.makedirs(output_dir, exist_ok=True)

    # Get video duration
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-show_entries", "format=duration", video_path],
        capture_output=True, text=True, check=True, timeout=120,
    )
    duration = float(json.loads(result.stdout)["format"]["duration"])

    # Extract frames at fixed intervals
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-i", video_path,
            "-vf", f"fps=1/{interval}",
            "-qscale:v", "2",
            os.path.join(output_dir, "frame_%04d.jpg"),
        ],
        capture_output=True, text=True, check=True, timeout=120,
    )

    # Build frame list with timestamps
    frames = []
    frame_files = sorted(f for f in os.listdir(output_dir) if f.startswith("frame_"))
    for i, fname in enumerate(frame_files):
        frames.append({
            "time": i * interval,
            "path": os.path.join(output_dir, fname),
        })

    log.info(f"Extracted {len(frames)} key frames")
    return frames


def detect_text_regions(
    frame_paths: list[dict],
    lang: str = "en",
) -> list[dict]:
    """
    Run EasyOCR on each frame to detect text bounding boxes.
    Returns list of {"time": float, "texts": [{"bbox": [...], "text": str, "confidence": float}]}.
    """
    # Map common language codes to EasyOCR codes
    ocr_lang_map = {
        "zh": "ch_sim", "ja": "ja", "ko": "ko", "en": "en",
        "fr": "fr", "es": "es", "de": "de", "pt": "pt",
        "it": "it", "ru": "ru", "ar": "ar", "hi": "hi",
        "th": "th", "vi": "vi", "id": "id",
    }
    ocr_lang = ocr_lang_map.get(lang, "en")

    # Always include English alongside the source language
    langs = list(set([ocr_lang, "en"]))
    try:
        reader = _get_ocr_reader(langs, gpu=True)
    except RuntimeError:
        log.warning("GPU not available, falling back to CPU for OCR...")
        reader = _get_ocr_reader(langs, gpu=False)

    results = []
    for i, frame in enumerate(frame_paths):
        log.info(f"OCR frame {i + 1}/{len(frame_paths)}...")
        detections = reader.readtext(frame["path"])
        texts = []
        for bbox, text, conf in detections:
            if conf < 0.6 or len(text.strip()) < 3:
                continue
            x_coords = [p[0] for p in bbox]
            y_coords = [p[1] for p in bbox]
            text_bbox = [int(min(x_coords)), int(min(y_coords)),
                         int(max(x_coords)), int(max(y_coords))]
            style = extract_text_style(frame["path"], text_bbox)
            texts.append({
                "bbox": text_bbox,
                "text": text.strip(),
                "confidence": conf,
                "style": style,
            })
        results.append({"time": frame["time"], "texts": texts})
        log.debug(f"{len(texts)} texts found")

    return results
