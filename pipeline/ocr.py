"""OCR pipeline — detect, group, translate, and overlay on-screen text."""

import json
import os
import platform
import subprocess
import threading
import unicodedata
from collections import Counter

import cv2
import numpy as np

from pipeline.translate import translate_segments


# ---------------------------------------------------------------------------
# Font maps — cross-platform (ffmpeg drawtext paths)
# ---------------------------------------------------------------------------
if platform.system() == "Windows":
    # ffmpeg on Windows accepts /Windows/Fonts/ paths (without drive letter)
    FONT_MAP = {
        "zh": "/Windows/Fonts/msyh.ttc",
        "ja": "/Windows/Fonts/msgothic.ttc",
        "ko": "/Windows/Fonts/malgun.ttf",
        "th": "/Windows/Fonts/leelawad.ttf",
        "hi": "/Windows/Fonts/mangal.ttf",
        "ar": "/Windows/Fonts/arial.ttf",
        "vi": "/Windows/Fonts/arial.ttf",
    }
    DEFAULT_FONT = "/Windows/Fonts/arial.ttf"
    FONT_FALLBACKS = [
        "/Windows/Fonts/segoeui.ttf",
        "/Windows/Fonts/tahoma.ttf",
        "/Windows/Fonts/msyh.ttc",
    ]
else:
    # Linux (Docker container with Noto fonts installed)
    FONT_MAP = {
        "zh": "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "ja": "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "ko": "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "th": "/usr/share/fonts/truetype/noto/NotoSansThai-Regular.ttf",
        "hi": "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf",
        "ar": "/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf",
        "vi": "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
    }
    DEFAULT_FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    FONT_FALLBACKS = [
        "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]

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

    # Convert to HSV → use V-channel for Otsu threshold
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    v_channel = hsv[:, :, 2]
    _, mask = cv2.threshold(v_channel, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    # Determine which side is text: text is usually the minority of pixels
    fg_count = np.count_nonzero(mask)
    bg_count = mask.size - fg_count
    if fg_count > bg_count:
        # Bright region is bigger → text is the dark part, invert
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

    # Check contrast — if too low, fall back to white on black
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

    print(f"  Extracted {len(frames)} key frames")
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
        print("  GPU not available, falling back to CPU for OCR...")
        reader = _get_ocr_reader(langs, gpu=False)

    results = []
    for i, frame in enumerate(frame_paths):
        print(f"    OCR frame {i + 1}/{len(frame_paths)}...", end="", flush=True)
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
        print(f" {len(texts)} texts found")

    return results


def _iou(a: list, b: list) -> float:
    """Intersection over Union of two bounding boxes [x1, y1, x2, y2]."""
    x1 = max(a[0], b[0])
    y1 = max(a[1], b[1])
    x2 = min(a[2], b[2])
    y2 = min(a[3], b[3])
    inter = max(0, x2 - x1) * max(0, y2 - y1)
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0


def _text_similarity(a: str, b: str) -> float:
    """Simple text similarity ratio."""
    a_lower, b_lower = a.lower(), b.lower()
    if a_lower == b_lower:
        return 1.0
    # Character overlap ratio
    common = sum(1 for c in a_lower if c in b_lower)
    return (2 * common) / (len(a_lower) + len(b_lower)) if (len(a_lower) + len(b_lower)) > 0 else 0


def _quantize_color(hex_color: str, bucket: int = 32) -> str:
    """Quantize a hex color to nearest bucket for stable mode calculation."""
    r = int(hex_color[1:3], 16)
    g = int(hex_color[3:5], 16)
    b = int(hex_color[5:7], 16)
    r = (r // bucket) * bucket
    g = (g // bucket) * bucket
    b = (b // bucket) * bucket
    return f"#{min(r,255):02X}{min(g,255):02X}{min(b,255):02X}"


def _resolve_style_samples(style_samples: list[dict]) -> dict:
    """Resolve multiple style samples into a single style using mode (most frequent)."""
    if not style_samples:
        return {"fg_color": "#FFFFFF", "bg_color": "#000000", "font_height_px": 24, "bg_opacity": 0.85}

    # Quantize colors and pick mode
    fg_quantized = [_quantize_color(s.get("fg_color", "#FFFFFF")) for s in style_samples]
    bg_quantized = [_quantize_color(s.get("bg_color", "#000000")) for s in style_samples]

    fg_mode = Counter(fg_quantized).most_common(1)[0][0]
    bg_mode = Counter(bg_quantized).most_common(1)[0][0]

    # Use the original (non-quantized) color closest to the mode
    # Find the first sample whose quantized fg matches the mode
    for s, fq in zip(style_samples, fg_quantized):
        if fq == fg_mode:
            fg_color = s.get("fg_color", "#FFFFFF")
            break
    else:
        fg_color = fg_mode

    for s, bq in zip(style_samples, bg_quantized):
        if bq == bg_mode:
            bg_color = s.get("bg_color", "#000000")
            break
    else:
        bg_color = bg_mode

    # Median font height
    heights = [s.get("font_height_px", 24) for s in style_samples]
    font_height_px = int(np.median(heights))

    # Mean opacity
    opacities = [s.get("bg_opacity", 0.85) for s in style_samples]
    bg_opacity = round(float(np.mean(opacities)), 2)

    return {
        "fg_color": fg_color,
        "bg_color": bg_color,
        "font_height_px": font_height_px,
        "bg_opacity": bg_opacity,
    }


def group_persistent_texts(
    frame_results: list[dict],
    iou_threshold: float = 0.3,
    text_similarity_threshold: float = 0.6,
) -> list[dict]:
    """
    Group text regions that persist across multiple consecutive frames.
    Accumulates bbox and style samples, resolves via median/mode.
    Returns list of {"text": str, "bbox": [...], "style": {...}, "start_time": float, "end_time": float}.
    """
    groups: list[dict] = []

    for frame in frame_results:
        time = frame["time"]
        matched_groups = set()

        for text_item in frame["texts"]:
            best_group = None
            best_score = 0

            for gi, group in enumerate(groups):
                if gi in matched_groups:
                    continue
                iou_score = _iou(text_item["bbox"], group["bbox"])
                sim_score = _text_similarity(text_item["text"], group["text"])

                if iou_score >= iou_threshold and sim_score >= text_similarity_threshold:
                    score = iou_score + sim_score
                    if score > best_score:
                        best_score = score
                        best_group = gi

            if best_group is not None:
                groups[best_group]["end_time"] = time + 2.0
                groups[best_group]["bbox_samples"].append(text_item["bbox"])
                groups[best_group]["style_samples"].append(text_item.get("style", {}))
                matched_groups.add(best_group)
            else:
                groups.append({
                    "text": text_item["text"],
                    "bbox": text_item["bbox"],
                    "bbox_samples": [text_item["bbox"]],
                    "style_samples": [text_item.get("style", {})],
                    "start_time": max(0, time - 0.5),
                    "end_time": time + 2.0,
                })

    # Filter: keep only texts that persist for at least 3 seconds AND appeared in 2+ frames
    groups = [g for g in groups if g["end_time"] - g["start_time"] >= 3.0 and len(g["bbox_samples"]) >= 2]

    # Deduplicate near-identical texts at similar positions
    deduped: list[dict] = []
    for g in groups:
        is_dup = False
        for existing in deduped:
            iou_score = _iou(g["bbox"], existing["bbox"])
            sim_score = _text_similarity(g["text"], existing["text"])
            if iou_score >= 0.5 or (iou_score >= 0.2 and sim_score >= 0.7):
                # Keep the one with more samples (more persistent)
                if len(g["bbox_samples"]) > len(existing["bbox_samples"]):
                    deduped.remove(existing)
                    deduped.append(g)
                is_dup = True
                break
        if not is_dup:
            deduped.append(g)
    groups = deduped

    # Resolve bbox (median) and style (mode) from accumulated samples
    for g in groups:
        samples = g["bbox_samples"]
        if len(samples) > 1:
            arr = np.array(samples)
            g["bbox"] = [int(v) for v in np.median(arr, axis=0)]
        g["style"] = _resolve_style_samples(g["style_samples"])
        # Clean up internal tracking fields
        del g["bbox_samples"]
        del g["style_samples"]

    print(f"  Grouped {len(groups)} persistent text regions")
    return groups


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
        # Normalize to NFC — ensures Vietnamese diacritics are precomposed
        group["translated_text"] = unicodedata.normalize("NFC", translated_text)

    print(f"  Translated {len(unique_texts)} unique on-screen texts")
    return text_groups


def _escape_drawtext(text: str) -> str:
    """Escape text for ffmpeg drawtext filter (passed via subprocess, no shell)."""
    # Normalize Unicode to NFC (precomposed) — critical for Vietnamese diacritics
    text = unicodedata.normalize("NFC", text)
    # ffmpeg drawtext escaping: https://ffmpeg.org/ffmpeg-filters.html#drawtext-1
    # Must escape: \ ' : ; ,  and also [ ] =
    # Order: backslash first, then the rest
    text = text.replace("\\", "\\\\")
    text = text.replace("'", "\u2019")  # replace with Unicode right single quote
    text = text.replace(":", "\\:")
    text = text.replace("%", "%%")
    return text


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
        # Estimate rendered width — 0.55 * font_size per char is a rough average
        estimated_width = len(translated) * font_size * 0.55
        if estimated_width > bbox_w and len(translated) > 0:
            font_size = max(14, int(bbox_w / (len(translated) * 0.55)))

        escaped = _escape_drawtext(translated)

        # 3. Draw translated text — centered horizontally and vertically within bbox
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


# ─── Phase 3: Inpainting & Tracking ─────────────────────────────────────────

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
        method: Inpainting method — "telea" (default) or "ns" (Navier-Stokes).

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
                # frame_0001.jpg → index 0 → time = index * interval
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

    print(f"  Inpainted {len(patches)} text regions")
    return patches


class BboxTracker:
    """
    Kalman-filter-based bounding box tracker for smooth text position.

    Tracks [x1, y1, x2, y2] with a constant-velocity model.
    Uses OpenCV's KalmanFilter internally.
    """

    def __init__(self, initial_bbox: list[int]):
        # State: [x1, y1, x2, y2, dx1, dy1, dx2, dy2]
        # Measurement: [x1, y1, x2, y2]
        self.kf = cv2.KalmanFilter(8, 4)

        # Transition matrix (constant velocity)
        self.kf.transitionMatrix = np.eye(8, dtype=np.float32)
        for i in range(4):
            self.kf.transitionMatrix[i, i + 4] = 1.0

        # Measurement matrix
        self.kf.measurementMatrix = np.zeros((4, 8), dtype=np.float32)
        for i in range(4):
            self.kf.measurementMatrix[i, i] = 1.0

        # Process noise — low, text moves slowly
        self.kf.processNoiseCov = np.eye(8, dtype=np.float32) * 0.01

        # Measurement noise
        self.kf.measurementNoiseCov = np.eye(4, dtype=np.float32) * 1.0

        # Error covariance
        self.kf.errorCovPost = np.eye(8, dtype=np.float32)

        # Initial state
        self.kf.statePost = np.array(
            [*initial_bbox, 0, 0, 0, 0], dtype=np.float32
        ).reshape(8, 1)

        self.timeline: list[tuple[float, list[int]]] = []

    def predict(self) -> list[int]:
        """Predict next bbox position."""
        pred = self.kf.predict()
        return [int(pred[i, 0]) for i in range(4)]

    def update(self, measured_bbox: list[int], time: float) -> list[int]:
        """Update with measurement, return filtered bbox."""
        self.kf.predict()
        measurement = np.array(measured_bbox, dtype=np.float32).reshape(4, 1)
        corrected = self.kf.correct(measurement)
        filtered = [int(corrected[i, 0]) for i in range(4)]
        self.timeline.append((time, filtered))
        return filtered

    def get_bbox_at_time(self, time: float) -> list[int]:
        """Interpolate bbox at a given time from the timeline."""
        if not self.timeline:
            return [0, 0, 0, 0]
        if len(self.timeline) == 1:
            return self.timeline[0][1]

        # Find surrounding keyframes
        for i in range(len(self.timeline) - 1):
            t0, bbox0 = self.timeline[i]
            t1, bbox1 = self.timeline[i + 1]
            if t0 <= time <= t1:
                # Linear interpolation
                if t1 == t0:
                    return bbox0
                alpha = (time - t0) / (t1 - t0)
                return [int(bbox0[j] + alpha * (bbox1[j] - bbox0[j])) for j in range(4)]

        # Outside range — use nearest
        if time < self.timeline[0][0]:
            return self.timeline[0][1]
        return self.timeline[-1][1]


def group_persistent_texts_tracked(
    frame_results: list[dict],
    iou_threshold: float = 0.3,
    text_similarity_threshold: float = 0.6,
) -> list[dict]:
    """
    Group text regions with Kalman filter tracking for smooth bbox positions.

    Enhanced version of group_persistent_texts() that maintains per-group
    BboxTracker for camera-motion-resilient text positioning.

    Returns list of dicts with bbox_timeline for per-frame positioning.
    """
    groups: list[dict] = []
    trackers: list[BboxTracker] = []

    for frame in frame_results:
        time = frame["time"]
        matched_groups = set()

        # Cache predicted bboxes ONCE per frame to avoid Kalman state drift
        predicted_bboxes = [t.predict() for t in trackers] if trackers else []

        for text_item in frame["texts"]:
            best_group = None
            best_score = 0

            for gi, group in enumerate(groups):
                if gi in matched_groups:
                    continue
                # Use cached prediction (no repeated predict() calls)
                predicted = predicted_bboxes[gi]
                iou_score = _iou(text_item["bbox"], predicted)
                sim_score = _text_similarity(text_item["text"], group["text"])

                if iou_score >= iou_threshold and sim_score >= text_similarity_threshold:
                    score = iou_score + sim_score
                    if score > best_score:
                        best_score = score
                        best_group = gi

            if best_group is not None:
                groups[best_group]["end_time"] = time + 2.0
                filtered = trackers[best_group].update(text_item["bbox"], time)
                groups[best_group]["bbox"] = filtered
                groups[best_group]["style_samples"].append(text_item.get("style", {}))
                matched_groups.add(best_group)
            else:
                tracker = BboxTracker(text_item["bbox"])
                tracker.update(text_item["bbox"], time)
                trackers.append(tracker)
                groups.append({
                    "text": text_item["text"],
                    "bbox": text_item["bbox"],
                    "style_samples": [text_item.get("style", {})],
                    "start_time": max(0, time - 0.5),
                    "end_time": time + 2.0,
                })

    # Filter: keep only texts that persist for at least 2 seconds
    filtered_groups = []
    for g, tracker in zip(groups, trackers):
        if g["end_time"] - g["start_time"] >= 2.0:
            g["style"] = _resolve_style_samples(g["style_samples"])
            g["bbox_timeline"] = tracker.timeline
            # Use median bbox as the default static position
            if tracker.timeline:
                bboxes = [b for _, b in tracker.timeline]
                arr = np.array(bboxes)
                g["bbox"] = [int(v) for v in np.median(arr, axis=0)]
            del g["style_samples"]
            filtered_groups.append(g)

    print(f"  Grouped {len(filtered_groups)} persistent text regions (tracked)")
    return filtered_groups


# ─── Phase 2: Overlay image generation ───────────────────────────────────────

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
