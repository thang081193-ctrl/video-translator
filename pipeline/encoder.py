"""Hardware encoder detection — NVENC vs libx264.

Centralized so subtitle burning, video conversion, and any other future
ffmpeg-driven step share one detection cache and one set of encoding flags.
"""

from __future__ import annotations

import subprocess

from pipeline.config import cfg
from pipeline.logger import get_logger

log = get_logger("Encoder")

_nvenc_available: bool | None = None


def check_nvenc() -> bool:
    """Cached probe for NVENC availability. Respects cfg.burn.use_nvenc."""
    global _nvenc_available
    if not cfg.burn.use_nvenc:
        return False
    if _nvenc_available is not None:
        return _nvenc_available
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True, text=True, timeout=cfg.ffmpeg.timeout_short,
        )
        _nvenc_available = "h264_nvenc" in result.stdout
    except Exception:
        _nvenc_available = False
    if _nvenc_available:
        log.info("NVENC hardware encoder detected — using GPU encoding")
    return _nvenc_available


def video_encoding_args(bitrate: str | None = None) -> list[str]:
    """Return ffmpeg video encoding flags.

    NVENC when available; libx264 otherwise. When `bitrate` is provided
    (e.g. "8M" for Reels output) the encoder is configured for VBR with
    that target — used by the convert step to hit Meta's bitrate spec.
    Without `bitrate` the encoder runs in CRF/CQ quality mode — used by
    subtitle burning where we don't want a hard size cap.
    """
    if check_nvenc():
        if bitrate:
            return [
                "-c:v", "h264_nvenc",
                "-preset", cfg.burn.nvenc_preset,
                "-rc", "vbr",
                "-b:v", bitrate,
                "-maxrate", bitrate,
                "-profile:v", "high",
            ]
        return [
            "-c:v", "h264_nvenc",
            "-preset", cfg.burn.nvenc_preset,
            "-cq", str(cfg.burn.nvenc_cq),
        ]
    if bitrate:
        return [
            "-c:v", "libx264",
            "-preset", cfg.burn.preset,
            "-b:v", bitrate,
            "-profile:v", "high",
        ]
    return ["-crf", str(cfg.burn.crf), "-preset", cfg.burn.preset]
