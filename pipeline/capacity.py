"""Capacity planning — how many Gemini keys do you need for a batch?

Free tier limits are per-PROJECT (not per-key). One project = one bucket.
Adding multiple keys on the same project doesn't increase capacity. To
scale you must create new GCP projects (no billing) and put one key on
each. This module computes the minimum project count needed.

Two caps gate a batch:
  RPM (per-minute, sliding window) — burst limit. Tightest for fast workloads.
  RPD (per-day, resets at 07:00 UTC) — total limit. Tightest for huge batches.

The estimate takes max(keys_for_rpm, keys_for_rpd) and adds 1 for safety
margin (retries, alignment loss). Numbers come from
`pipeline.quota.MODEL_RATE_LIMITS` — verified against Google's published
limits and observed 429 responses on 2026-05.

Usage from CLI:
    python -m pipeline.capacity --videos 56 --langs 3
    python -m pipeline.capacity --videos 100 --langs 1 --avg-segments 60
    python -m pipeline.capacity --videos 30 --langs 3 --model gemini-2.5-flash
"""
from __future__ import annotations

import argparse
import math
from dataclasses import dataclass

from pipeline.quota import MODEL_RATE_LIMITS, DEFAULT_MODEL, model_limits


@dataclass(frozen=True)
class Estimate:
    """Result of capacity planning."""
    videos: int
    langs: int
    avg_segments: int
    batch_size: int
    model: str
    # Derived totals
    batches_per_video: int          # ceil(avg_segments / batch_size) per lang
    calls_per_video: int            # batches_per_video * langs
    total_calls: int                # calls_per_video * videos
    peak_rpm: int                   # max calls during a single video's translate stage
    # Per-cap key requirements
    keys_for_rpm: int
    keys_for_rpd: int
    keys_recommended: int           # max + 1 safety margin
    # Echoed limits for transparency
    rpm_cap: int
    rpd_cap: int

    def explain(self) -> str:
        L = []
        L.append(f"Workload: {self.videos} videos x {self.langs} langs, "
                 f"~{self.avg_segments} segments/video, batch_size={self.batch_size}")
        L.append(f"Model: {self.model}  (free-tier per-project caps: "
                 f"{self.rpm_cap} RPM, {self.rpd_cap} RPD)")
        L.append(f"Per video: {self.batches_per_video} batches x {self.langs} langs "
                 f"= {self.calls_per_video} calls")
        L.append(f"Total calls for batch: {self.total_calls}")
        L.append(f"Peak RPM (single-video burst): {self.peak_rpm}")
        L.append("")
        L.append(f"Keys needed by RPM: ceil({self.peak_rpm}/{self.rpm_cap}) "
                 f"= {self.keys_for_rpm}")
        L.append(f"Keys needed by RPD: ceil({self.total_calls}/{self.rpd_cap}) "
                 f"= {self.keys_for_rpd}")
        L.append(f"=> Recommended: {self.keys_recommended} keys "
                 f"(max + 1 for retry headroom)")
        L.append("")
        L.append("Setup: each key on its own no-billing GCP project.")
        L.append("  https://aistudio.google.com/apikey -> Create API key -> New project")
        return "\n".join(L)


def estimate_keys_needed(
    videos: int,
    langs: int = 1,
    avg_segments: int = 30,
    batch_size: int = 20,
    model: str = DEFAULT_MODEL,
    safety_margin: int = 1,
) -> Estimate:
    """Compute minimum key (= project) count for a translate batch.

    Args:
        videos: number of input videos.
        langs: number of target languages per video (3 = fr/it/tr).
        avg_segments: average Whisper segments per video. 15s ad video ≈ 5-15
            segments; longer narrated content ≈ 30-60. When unsure, use 30
            (conservative — slightly over-provisions).
        batch_size: matches `cfg.translate.batch_size`. The pipeline groups
            this many segments per API call.
        model: which Gemini model. Determines the per-project caps.
        safety_margin: extra keys added on top of the cap-derived minimum.
            Default 1 covers retry overhead + a single key going slow.

    Returns:
        Estimate with full breakdown — print `.explain()` for human output.
    """
    if videos < 1 or langs < 1:
        raise ValueError("videos and langs must be ≥ 1")

    limits = model_limits(model)
    rpm_cap = limits["rpm"]
    rpd_cap = limits["rpd"]

    batches_per_video = max(1, math.ceil(avg_segments / batch_size))
    calls_per_video = batches_per_video * langs
    total_calls = calls_per_video * videos

    # Peak RPM: assume worst-case the entire single-video burst lands inside
    # one minute. For typical 15-30s video processing this is realistic.
    peak_rpm = calls_per_video

    keys_for_rpm = max(1, math.ceil(peak_rpm / rpm_cap))
    keys_for_rpd = max(1, math.ceil(total_calls / rpd_cap))
    recommended = max(keys_for_rpm, keys_for_rpd) + safety_margin

    return Estimate(
        videos=videos,
        langs=langs,
        avg_segments=avg_segments,
        batch_size=batch_size,
        model=model,
        batches_per_video=batches_per_video,
        calls_per_video=calls_per_video,
        total_calls=total_calls,
        peak_rpm=peak_rpm,
        keys_for_rpm=keys_for_rpm,
        keys_for_rpd=keys_for_rpd,
        keys_recommended=recommended,
        rpm_cap=rpm_cap,
        rpd_cap=rpd_cap,
    )


def _cli() -> None:
    parser = argparse.ArgumentParser(
        description="Estimate Gemini API keys needed for a translate batch.",
    )
    parser.add_argument("--videos", type=int, required=True,
                        help="number of videos in the batch")
    parser.add_argument("--langs", type=int, default=1,
                        help="target languages per video (default 1)")
    parser.add_argument("--avg-segments", type=int, default=30,
                        help="avg Whisper segments per video (default 30)")
    parser.add_argument("--batch-size", type=int, default=20,
                        help="cfg.translate.batch_size (default 20)")
    parser.add_argument("--model", type=str, default=DEFAULT_MODEL,
                        choices=sorted(MODEL_RATE_LIMITS.keys()),
                        help="Gemini model name")
    parser.add_argument("--safety", type=int, default=1,
                        help="extra keys above minimum (default 1)")
    args = parser.parse_args()

    est = estimate_keys_needed(
        videos=args.videos,
        langs=args.langs,
        avg_segments=args.avg_segments,
        batch_size=args.batch_size,
        model=args.model,
        safety_margin=args.safety,
    )
    print(est.explain())


if __name__ == "__main__":
    _cli()
