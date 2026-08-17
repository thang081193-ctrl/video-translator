"""Tests for pipeline.logo_swap -- geometry, gating, smoothing, spec IO.

The gate tests are the load-bearing ones. Two failure modes cost real money
here: our logo flashing on for a few frames in the wrong place (worse than
leaving the competitor's), and our logo floating over an end card because a
low score was read as "occluded" instead of "absent".
"""

from __future__ import annotations

from fractions import Fraction

import numpy as np
import pytest

from pipeline.logo_swap import render as R
from pipeline.logo_swap import track as T
from pipeline.logo_swap.compose import FrameOp, composite_frame, kept_segments
from pipeline.logo_swap.spec import (
    FILL_CLEAN_PLATE, FILL_LOGO_ROUNDED, Box, ClipSpec, CutSpec, SearchCfg,
    StaticSpec, TrackedSpec, load_spec, save_spec, validate_spec,
)


# ── Box ──────────────────────────────────────────────────────────────────────

def test_box_px_roundtrip():
    b = Box.from_px(100, 200, 50, 60, 400, 800)
    assert b.to_px(400, 800) == (100, 200, 50, 60)


def test_box_scales_to_a_sibling_resolution():
    """The whole reason geometry is normalized: duplicate scrapes differ in size."""
    b = Box.from_px(307, 393, 45, 44, 404, 720)
    x, y, w, h = b.to_px(360, 640)          # the 360x640 twin of the same creative
    assert (x, y) == (274, 349)
    assert (w, h) == (40, 39)


def test_box_to_px_clamps_inside_frame():
    x, y, w, h = Box(0.95, 0.95, 0.20, 0.20).to_px(100, 100)
    assert x + w <= 100 and y + h <= 100


def test_box_to_px_never_degenerate():
    _, _, w, h = Box(0.5, 0.5, 0.0, 0.0).to_px(100, 100)
    assert w >= 1 and h >= 1


def test_box_pad_grows_and_stays_normalized():
    p = Box(0.4, 0.4, 0.2, 0.2).pad(0.5)
    assert p.w == pytest.approx(0.4) and p.h == pytest.approx(0.4)
    assert p.x == pytest.approx(0.3) and p.y == pytest.approx(0.3)


def test_box_pad_clamps_at_the_edge():
    p = Box(0.0, 0.0, 0.2, 0.2).pad(0.5)
    assert p.x == 0.0 and p.y == 0.0
    assert p.x + p.w <= 1.0 and p.y + p.h <= 1.0


# ── SearchCfg ────────────────────────────────────────────────────────────────

def test_scales_span_the_configured_band():
    s = SearchCfg(scale_lo=0.9, scale_hi=1.1, scale_step=0.05).scales()
    assert s[0] == pytest.approx(0.9)
    assert s[-1] <= 1.1 + 1e-9
    assert all(b > a for a, b in zip(s, s[1:]))


# ── cuts ─────────────────────────────────────────────────────────────────────

def _spec(cuts, dur=40.0):
    return ClipSpec(video_id="v", authored_dims=(360, 640), fps=Fraction(25, 1),
                    duration=dur, cuts=cuts)


def test_no_cuts_keeps_everything():
    assert kept_segments(_spec([]), 40.0) == [(0.0, 40.0)]


def test_tail_cut_to_eof():
    assert kept_segments(_spec([CutSpec("endcard", 31.85, None)]), 40.0) == [(0.0, 31.85)]


def test_middle_cut_splits_into_two_segments():
    segs = kept_segments(_spec([CutSpec("storepage", 10.0, 12.0)]), 40.0)
    assert segs == [(0.0, 10.0), (12.0, 40.0)]


def test_overlapping_cuts_are_merged():
    segs = kept_segments(_spec([CutSpec("a", 10.0, 15.0), CutSpec("b", 12.0, 20.0)]), 40.0)
    assert segs == [(0.0, 10.0), (20.0, 40.0)]


def test_kept_duration_matches_the_segments():
    s = _spec([CutSpec("a", 10.0, 12.0), CutSpec("endcard", 35.0, None)])
    assert s.kept_duration() == pytest.approx(33.0)
    assert sum(e - b for b, e in kept_segments(s, 40.0)) == pytest.approx(33.0)


def test_is_cut_boundaries_are_half_open():
    s = _spec([CutSpec("a", 10.0, 12.0)])
    assert not s.is_cut(9.99) and s.is_cut(10.0) and s.is_cut(11.99)
    assert not s.is_cut(12.0)


# ── gating ───────────────────────────────────────────────────────────────────

def _samples(scores, scene=None):
    scene = scene or [0.0] * len(scores)
    return [T.TrackSample(n=i, t=i / 25.0, x=100.0, y=100.0, scale=1.0,
                          score=sc, scene_diff=sd, state=T.HIT)
            for i, (sc, sd) in enumerate(zip(scores, scene))]


CFG = SearchCfg(accept=0.72, drop=0.55, hold_frames=6, min_run_frames=8)


def test_strong_track_is_all_on():
    out = T.gate_track(_samples([0.95] * 30), CFG)
    assert all(s.on for s in out)


def test_brief_dip_is_held_not_dropped():
    """A hand sweeping over the icon must not turn our logo off."""
    scores = [0.95] * 12 + [0.30] * 3 + [0.95] * 12
    out = T.gate_track(_samples(scores), CFG)
    assert all(s.on for s in out)
    assert [s.state for s in out[12:15]] == [T.HOLD] * 3


def test_long_dropout_turns_off_after_hold():
    scores = [0.95] * 12 + [0.20] * 20 + [0.95] * 12
    out = T.gate_track(_samples(scores), CFG)
    assert all(s.on for s in out[:12])
    assert not out[-14].on                       # deep inside the dropout
    assert all(s.on for s in out[-12:])


def test_scene_change_turns_off_immediately_without_holding():
    """An end card is ABSENT, not occluded -- holding would float the logo on it."""
    scores = [0.95] * 12 + [0.20] * 12
    scene = [0.0] * 12 + [60.0] + [5.0] * 11
    out = T.gate_track(_samples(scores, scene), CFG)
    assert out[11].on
    assert not out[12].on                        # no hold frames at all
    assert not any(s.on for s in out[12:])


def test_short_on_run_is_deleted():
    """A 4-frame flash of OUR logo reads worse than 4 frames of theirs."""
    scores = [0.20] * 20 + [0.95] * 4 + [0.20] * 20
    out = T.gate_track(_samples(scores), CFG)
    assert not any(s.on for s in out)


def test_long_enough_on_run_survives():
    scores = [0.20] * 20 + [0.95] * 12 + [0.20] * 20
    out = T.gate_track(_samples(scores), CFG)
    assert sum(s.on for s in out) >= 12


def test_hold_prediction_follows_the_last_velocity():
    s = _samples([0.95, 0.95, 0.95, 0.10, 0.10])
    s = [T.TrackSample(n=i, t=i / 25, x=100.0 + 10 * i, y=50.0, scale=1.0,
                       score=sc.score, scene_diff=0.0, state=T.HIT)
         for i, sc in enumerate(s)]
    out = T.gate_track(s, SearchCfg(accept=0.72, drop=0.55, hold_frames=6,
                                    min_run_frames=1))
    assert out[3].state == T.HOLD
    assert out[3].x > out[2].x                   # kept moving in the same direction
    assert out[4].x - out[3].x < out[2].x - out[1].x   # but damped


def test_empty_track_is_handled():
    assert T.gate_track([], CFG) == []
    assert T.smooth_track([]) == []
    assert T.track_report([], 10, 10) == {}


# ── smoothing ────────────────────────────────────────────────────────────────

def test_smoothing_kills_a_single_frame_spike():
    s = [T.TrackSample(n=i, t=i / 25, x=(300.0 if i == 10 else 100.0), y=100.0,
                       scale=1.0, score=0.95, scene_diff=0.0, state=T.HIT)
         for i in range(30)]
    out = T.smooth_track(s)
    assert abs(out[10].x - 100.0) < 15.0


def test_smoothing_quantizes_to_integer_pixels():
    s = [T.TrackSample(n=i, t=i / 25, x=100.4, y=100.6, scale=1.0,
                       score=0.95, scene_diff=0.0, state=T.HIT) for i in range(20)]
    out = T.smooth_track(s)
    assert all(float(v.x).is_integer() and float(v.y).is_integer() for v in out)


def test_smoothing_leaves_off_frames_alone():
    s = _samples([0.95] * 10 + [0.10] * 20)
    gated = T.gate_track(s, CFG)
    out = T.smooth_track(gated)
    assert [a.state for a in out] == [b.state for b in gated]


def test_short_gaps_are_bridged_into_one_run():
    """Anti-flicker: a 4-frame gap between two solid runs must not split them."""
    scores = [0.95] * 12 + [0.10] * 4 + [0.95] * 12
    rep = T.track_report(T.gate_track(_samples(scores), CFG), 40, 40)
    assert rep["on_runs"] == 1


def test_report_counts_genuinely_separate_runs():
    """Gaps longer than hold+bridge stay separate, and the report says so."""
    scores = ([0.95] * 12 + [0.10] * 30) * 3
    rep = T.track_report(T.gate_track(_samples(scores), CFG), 40, 40)
    assert rep["on_runs"] == 3


# ── masks and variants ───────────────────────────────────────────────────────

def test_rounded_mask_clears_the_corners_but_fills_the_centre():
    m = R.rounded_mask(80, 80, 0.3, feather_px=0.0)
    assert m[40, 40] == 255
    assert m[0, 0] == 0


def test_square_mask_fills_the_corners():
    m = R.rounded_mask(60, 60, 0.3, 0.0, shape=R.SHAPE_SQUARE)
    assert m[0, 0] == 255 and m[-1, -1] == 255


def test_mask_radius_uses_the_shorter_side():
    """A wide box must keep circular corners, not elliptical ones."""
    m = R.rounded_mask(200, 40, 0.5, 0.0)
    assert m[20, 100] == 255
    assert m[0, 0] == 0 and m[0, -1] == 0


def test_variant_cache_reuses_by_size():
    logo = np.dstack([np.full((64, 64, 3), 200, np.uint8),
                      np.full((64, 64), 255, np.uint8)])
    c = R.VariantCache(logo)
    c.get(30, 30, 0.22, 1.0)
    c.get(30, 30, 0.22, 1.0)
    assert len(c) == 1
    c.get(31, 31, 0.22, 1.0)
    assert len(c) == 2


def test_variant_premultiply_composites_to_the_logo_colour():
    logo = np.dstack([np.full((64, 64, 3), 200, np.uint8),
                      np.full((64, 64), 255, np.uint8)])
    premul, inv = R.VariantCache(logo).get(40, 40, 0.0, 0.0, shape=R.SHAPE_SQUARE)
    dst = np.zeros((40, 40, 3), np.uint8)
    out = ((premul + dst.astype(np.uint16) * inv) // 255).astype(np.uint8)
    assert abs(int(out[20, 20, 0]) - 200) <= 1


# ── plates ───────────────────────────────────────────────────────────────────

def test_flat_plate_takes_the_surrounding_colour_not_the_mark():
    frame = np.full((100, 100, 3), 240, np.uint8)     # light screen
    frame[40:60, 40:60] = 10                          # dark competitor tile
    plate = R.flat_plate(frame, (40, 40, 20, 20))
    assert plate.shape == (20, 20, 3)
    assert plate.mean() > 200                          # sampled the screen, not the tile


def test_ring_pixels_excludes_the_box_interior():
    frame = np.zeros((60, 60, 3), np.uint8)
    frame[20:40, 20:40] = 255
    ring = R.ring_pixels(frame, (20, 20, 20, 20), width=3)
    assert len(ring) > 0
    assert ring.max() == 0                             # only the black surround


# ── compositing ──────────────────────────────────────────────────────────────

def _op(**kw):
    base = dict(target_id="t", x=20, y=20, w=30, h=30, fill=FILL_LOGO_ROUNDED,
                corner_radius=0.22, feather_px=0.0, grade=False, cal=None,
                plate=None, inset=0)
    return FrameOp(**(base | kw))


def _cache():
    logo = np.dstack([np.full((64, 64, 3), 255, np.uint8),
                      np.full((64, 64), 255, np.uint8)])
    return R.VariantCache(logo)


def test_composite_covers_the_mark_and_leaves_the_rest():
    frame = np.full((100, 100, 3), 30, np.uint8)
    frame[20:50, 20:50] = 200                          # the mark
    out = composite_frame(frame.copy(), [_op()], _cache(), np.random.default_rng(0))
    assert out[35, 35, 0] > 200                        # centre replaced by the logo
    assert out[5, 5, 0] == 30                          # untouched elsewhere


def test_clean_plate_over_a_solid_mark_takes_the_surroundings():
    """A uniform box IS the mark, so its own colour is useless -- use the ring."""
    frame = np.full((100, 100, 3), 40, np.uint8)
    frame[20:50, 20:50] = 220
    out = composite_frame(frame.copy(), [_op(fill=FILL_CLEAN_PLATE)],
                          _cache(), np.random.default_rng(0))
    assert out[35, 35, 0] < 80


def test_clean_plate_over_text_takes_the_card_colour():
    """Text on a card: the card is the majority, and the ring is off-card.

    Sampling the ring here is what put a grey bar across clip 1's white name
    pill -- the ring straddled the pill edge and caught the scene behind it.
    """
    frame = np.full((100, 100, 3), 30, np.uint8)       # dark scene
    frame[20:50, 20:50] = 235                          # white card
    frame[30:40, 24:46] = 10                           # dark text on the card
    out = composite_frame(frame.copy(), [_op(fill=FILL_CLEAN_PLATE)],
                          _cache(), np.random.default_rng(0))
    assert out[35, 35, 0] > 200                        # card colour, not the scene
    assert out[35, 35, 0] != 30


def test_logo_composites_onto_the_frame_with_no_plate_ring():
    """No rectangular plate: outside the rounded corners the frame survives.

    Painting a plate over the cover rect and drawing the rounded logo on top
    leaves that plate showing at the corners, where it reads as a coloured
    border (clip 80 picked up a tan ring from the actor's hand).
    """
    frame = np.full((100, 100, 3), 30, np.uint8)
    frame[18:52, 18:52] = 200                          # the mark
    frame[0:18, :] = 77                                # distinct surroundings
    out = composite_frame(frame.copy(), [_op(x=20, y=20, w=30, h=30,
                                             corner_radius=0.4)],
                          _cache(), np.random.default_rng(0))
    assert out[35, 35, 0] > 200                        # logo drawn in the middle
    assert out[5, 50, 0] == 77                         # outside the box untouched
    # the extreme corner of the cover rect keeps whatever was already there,
    # rather than a flat fill sampled from the surroundings
    assert out[20, 20, 0] != 30 or out[20, 20, 0] == 200


def test_composite_clips_at_the_frame_edge():
    frame = np.full((60, 60, 3), 30, np.uint8)
    out = composite_frame(frame.copy(), [_op(x=45, y=45, w=30, h=30)],
                          _cache(), np.random.default_rng(0))
    assert out.shape == (60, 60, 3)


def test_tiny_op_is_skipped_not_crashed():
    frame = np.full((60, 60, 3), 30, np.uint8)
    out = composite_frame(frame.copy(), [_op(w=2, h=2)], _cache(),
                          np.random.default_rng(0))
    assert np.array_equal(out, frame)


# ── spec IO ──────────────────────────────────────────────────────────────────

def _full_spec():
    return ClipSpec(
        video_id="sample_clip_a", authored_dims=(404, 720),
        fps=Fraction(30, 1), duration=37.71, creative_group="cg_a",
        cuts=[CutSpec("endcard", 31.85, None)],
        statics=[StaticSpec(id="s1", box=Box(0.05, 0.74, 0.17, 0.10),
                            t=(0.0, 5.2), plate_from=6.4)],
        tracked=[TrackedSpec(id="t1", seed_t=8.0,
                             seed_box=Box(0.76, 0.55, 0.11, 0.06),
                             cover_box=Box(0.75, 0.54, 0.13, 0.07),
                             t=(0.0, 31.85))],
        reviewed_by="me", reviewed_at="2026-08-14")


def test_spec_roundtrip(tmp_path):
    a = _full_spec()
    b = load_spec(save_spec(a, tmp_path / "s.json"))
    assert b.video_id == a.video_id
    assert b.fps == a.fps and isinstance(b.fps, Fraction)
    assert b.cuts == a.cuts
    assert b.tracked[0].cover_box == a.tracked[0].cover_box
    assert b.statics[0].plate_from == 6.4
    assert b.tracked[0].search == a.tracked[0].search


def test_spec_without_cover_box_roundtrips_as_none(tmp_path):
    s = _full_spec()
    s.tracked = [TrackedSpec(id="t1", seed_t=1.0, seed_box=Box(0.1, 0.1, 0.1, 0.1),
                             t=(0.0, 10.0))]
    assert load_spec(save_spec(s, tmp_path / "s.json")).tracked[0].cover_box is None


def test_save_is_atomic_leaving_no_temp_files(tmp_path):
    save_spec(_full_spec(), tmp_path / "s.json")
    assert [p.name for p in tmp_path.iterdir()] == ["s.json"]


def test_bad_schema_version_is_rejected(tmp_path):
    p = tmp_path / "s.json"
    save_spec(_full_spec(), p)
    p.write_text(p.read_text(encoding="utf-8").replace('"schema": 1', '"schema": 99'),
                 encoding="utf-8")
    with pytest.raises(ValueError, match="schema"):
        load_spec(p)


# ── validation ───────────────────────────────────────────────────────────────

def test_valid_spec_has_no_complaints():
    assert validate_spec(_full_spec(), 404, 720, 37.71) == []


def test_sibling_resolution_is_allowed_same_aspect():
    """404x720 geometry applied to the 360x640 twin must NOT be an error."""
    errs = validate_spec(_full_spec(), 360, 640, 37.71)
    assert not any("aspect" in e for e in errs)


def test_mismatched_aspect_is_flagged():
    assert any("aspect" in e for e in validate_spec(_full_spec(), 720, 720, 37.71))


def test_duration_mismatch_is_flagged():
    assert any("duration" in e for e in validate_spec(_full_spec(), 404, 720, 50.0))


def test_unnormalized_box_is_flagged():
    s = _full_spec()
    s.statics = [StaticSpec(id="s1", box=Box(307, 393, 45, 44), t=(0.0, 5.0))]
    assert any("normalized" in e for e in validate_spec(s, 404, 720, 37.71))


def test_duplicate_target_ids_are_flagged():
    s = _full_spec()
    s.tracked[0] = TrackedSpec(id="s1", seed_t=8.0,
                               seed_box=Box(0.7, 0.5, 0.1, 0.1), t=(0.0, 30.0))
    assert any("duplicate" in e for e in validate_spec(s, 404, 720, 37.71))


def test_seed_inside_a_cut_is_flagged():
    s = _full_spec()
    s.tracked = [TrackedSpec(id="t1", seed_t=35.0,
                             seed_box=Box(0.7, 0.5, 0.1, 0.1), t=(0.0, 37.0))]
    assert any("inside a cut" in e for e in validate_spec(s, 404, 720, 37.71))


def test_untrackably_small_template_is_flagged():
    s = _full_spec()
    s.tracked = [TrackedSpec(id="t1", seed_t=8.0,
                             seed_box=Box(0.5, 0.5, 0.01, 0.01), t=(0.0, 30.0))]
    assert any("too small" in e for e in validate_spec(s, 404, 720, 37.71))


def test_unknown_fill_is_flagged():
    s = _full_spec()
    s.statics = [StaticSpec(id="s1", box=Box(0.1, 0.1, 0.1, 0.1),
                            t=(0.0, 5.0), fill="rainbow")]
    assert any("unknown fill" in e for e in validate_spec(s, 404, 720, 37.71))
