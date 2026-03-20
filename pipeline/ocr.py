"""OCR pipeline — detect, group, translate, and overlay on-screen text."""

import json
import os
import subprocess

from pipeline.translate import translate_segments


# Font map for non-Latin scripts (Windows paths — omit drive letter for ffmpeg compat)
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
        capture_output=True, text=True, check=True,
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
        capture_output=True, text=True, check=True,
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
    import easyocr

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
    reader = easyocr.Reader(langs, gpu=True)

    results = []
    for i, frame in enumerate(frame_paths):
        print(f"    OCR frame {i + 1}/{len(frame_paths)}...", end="", flush=True)
        detections = reader.readtext(frame["path"])
        texts = []
        for bbox, text, conf in detections:
            if conf < 0.5 or len(text.strip()) < 2:
                continue
            x_coords = [p[0] for p in bbox]
            y_coords = [p[1] for p in bbox]
            texts.append({
                "bbox": [int(min(x_coords)), int(min(y_coords)),
                         int(max(x_coords)), int(max(y_coords))],
                "text": text.strip(),
                "confidence": conf,
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


def group_persistent_texts(
    frame_results: list[dict],
    iou_threshold: float = 0.3,
    text_similarity_threshold: float = 0.6,
) -> list[dict]:
    """
    Group text regions that persist across multiple consecutive frames.
    Returns list of {"text": str, "bbox": [...], "start_time": float, "end_time": float}.
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
                groups[best_group]["end_time"] = time + 2.0  # extend by interval
                matched_groups.add(best_group)
            else:
                groups.append({
                    "text": text_item["text"],
                    "bbox": text_item["bbox"],
                    "start_time": max(0, time - 0.5),
                    "end_time": time + 2.0,
                })

    # Filter: keep only texts that persist for at least 2 seconds (appeared in 2+ frames)
    groups = [g for g in groups if g["end_time"] - g["start_time"] >= 2.0]

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
        group["translated_text"] = translation_map.get(group["text"], group["text"])

    print(f"  Translated {len(unique_texts)} unique on-screen texts")
    return text_groups


def _escape_drawtext(text: str) -> str:
    """Escape text for ffmpeg drawtext filter (passed via subprocess, no shell)."""
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
    """
    font_path = FONT_MAP.get(target_lang, DEFAULT_FONT)
    filters = []

    for g in text_groups:
        x1, y1, x2, y2 = g["bbox"]
        translated = g.get("translated_text", g["text"])

        start = g["start_time"]
        end = g["end_time"]
        padding = 4

        # 1. Cover original text with dark box
        filters.append(
            f"drawbox=x={x1 - padding}:y={y1 - padding}"
            f":w={x2 - x1 + padding * 2}:h={y2 - y1 + padding * 2}"
            f":color=black@0.85:t=fill"
            f":enable='between(t,{start:.2f},{end:.2f})'"
        )

        # 2. Draw translated text
        font_size = max(14, int((y2 - y1) * 0.7))
        escaped = _escape_drawtext(translated)

        filters.append(
            f"drawtext=text='{escaped}'"
            f":fontfile={font_path}"
            f":x={x1}:y={y1 + 2}"
            f":fontsize={font_size}:fontcolor=white"
            f":enable='between(t,{start:.2f},{end:.2f})'"
        )

    return ",".join(filters)
