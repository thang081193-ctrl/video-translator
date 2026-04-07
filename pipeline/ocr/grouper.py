"""OCR grouper -- text grouping, persistence tracking, and Kalman filtering."""

from collections import Counter

import cv2
import numpy as np

from pipeline.config import cfg
from pipeline.logger import get_logger

log = get_logger("OCR")


# ---------------------------------------------------------------------------
# Helpers: bbox overlap, text similarity, style resolution
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Kalman-filter-based bounding box tracker
# ---------------------------------------------------------------------------

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

        # Process noise -- low, text moves slowly
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

        # Outside range -- use nearest
        if time < self.timeline[0][0]:
            return self.timeline[0][1]
        return self.timeline[-1][1]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

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

    log.info(f"Grouped {len(groups)} persistent text regions")
    return groups


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

    log.info(f"Grouped {len(filtered_groups)} persistent text regions (tracked)")
    return filtered_groups
