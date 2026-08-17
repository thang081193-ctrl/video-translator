"""Replace competitor branding baked into video pixels with our own.

Built for rebranding scraped competitor ads: the app icon composited onto a
phone screen an actor is holding, plus the fixed-position logo cards, name pills
and end cards those ads carry. Runs as a PRE-PASS at source resolution, before
pipeline.brand_pass -- see compose.py and render.py for why both of those
choices are load-bearing rather than incidental.

Typical use:

    from pipeline.logo_swap import load_spec, render_clip, spec_path

    spec = load_spec(spec_path(src_root, video_id))
    qa = render_clip(spec, src_mp4, out_mp4, logo="Logo.png")
"""

from __future__ import annotations

from pipeline.logo_swap.compose import (
    FrameOp, VideoInfo, composite_frame, kept_segments, plan_tracked,
    probe_video, render_clip,
)
from pipeline.logo_swap.render import (
    GradeCal, VariantCache, alpha_paste, apply_grade, blur_plate,
    calibrate_grade, flat_plate, grade_to_plate, inner_plate, load_logo,
    ring_pixels,
    rounded_mask, sampled_plate, soft_shadow,
)
from pipeline.logo_swap.spec import (
    FILL_BLUR, FILL_CLEAN_PLATE, FILL_LOGO_ROUNDED, FILL_LOGO_SQUARE, FILLS,
    SCHEMA_VERSION, Box, ClipSpec, CutSpec, SearchCfg, StaticSpec, TrackedSpec,
    load_spec, save_spec, spec_path, validate_spec,
)
from pipeline.logo_swap.track import (
    HIT, HOLD, MISS, OFF, WEAK, TrackSample, acquire, gate_track, seed_template,
    smooth_track, track_logo, track_report,
)

__all__ = [
    # spec
    "Box", "ClipSpec", "CutSpec", "SearchCfg", "StaticSpec", "TrackedSpec",
    "load_spec", "save_spec", "spec_path", "validate_spec", "SCHEMA_VERSION",
    "FILLS", "FILL_LOGO_ROUNDED", "FILL_LOGO_SQUARE", "FILL_CLEAN_PLATE",
    "FILL_BLUR",
    # track
    "TrackSample", "acquire", "gate_track", "seed_template", "smooth_track",
    "track_logo", "track_report", "HIT", "WEAK", "MISS", "HOLD", "OFF",
    # render
    "GradeCal", "VariantCache", "alpha_paste", "apply_grade", "blur_plate",
    "calibrate_grade", "flat_plate", "grade_to_plate", "inner_plate", "load_logo",
    "ring_pixels", "rounded_mask", "sampled_plate", "soft_shadow",
    # compose
    "FrameOp", "VideoInfo", "composite_frame", "kept_segments", "plan_tracked",
    "probe_video", "render_clip",
]
