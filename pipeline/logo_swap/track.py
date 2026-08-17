"""Track a baked-in logo across frames with multi-scale template matching.

Why matchTemplate and not a real tracker: requirements.txt pins
opencv-python-headless, which ships no contrib module -- there is no CSRT/KCF
and no cv2.legacy, only TrackerMIL. Template matching is both the measured-good
choice and the only one available.

Measured on this batch (720x1280, 19 scales): a FULL-frame multi-scale match is
~430 ms/frame, i.e. ~3 h for 35 clips. Restricting the search to a ROI around
the previous hit with a narrow scale band is ~9 ms/frame. So the full-frame path
runs only to acquire (at seed_t, and after the track is lost).

Gating is deliberately two-pass and offline. A whole track is a few thousand
floats, so looking forward is free -- and it lets short runs be deleted, which a
causal gate cannot do. A 0.3 s flash of OUR logo in the wrong place reads worse
than 0.3 s of the competitor's.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import cv2
import numpy as np

from pipeline.logger import get_logger
from pipeline.logo_swap.spec import SearchCfg

log = get_logger("LogoSwap")

# sample states
HIT = "hit"        # score >= accept
WEAK = "weak"      # drop <= score < accept
MISS = "miss"      # score < drop
HOLD = "hold"      # occluded: composite at a predicted position
OFF = "off"        # do not composite


@dataclass(frozen=True)
class TrackSample:
    n: int
    t: float
    x: float            # top-left, pixels, in the source frame
    y: float
    scale: float
    score: float
    scene_diff: float
    state: str

    @property
    def on(self) -> bool:
        return self.state in (HIT, WEAK, HOLD)


def seed_template(video: str, t: float, box_px: tuple[int, int, int, int],
                  fps: float) -> np.ndarray:
    """Grab the grayscale template patch at time `t`."""
    cap = cv2.VideoCapture(video)
    try:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(round(t * fps)))
        ok, frame = cap.read()
        if not ok:
            raise RuntimeError(f"{video}: cannot read seed frame at t={t}")
        x, y, w, h = box_px
        patch = frame[y:y + h, x:x + w]
        if patch.size == 0:
            raise RuntimeError(f"{video}: seed box {box_px} is empty")
        return cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY)
    finally:
        cap.release()


def _match(gray: np.ndarray, tpl: np.ndarray,
           scales: list[float]) -> tuple[float, int, int, float]:
    """Best (score, x, y, scale) of `tpl` in `gray` over `scales`."""
    best = (-1.0, 0, 0, 1.0)
    gh, gw = gray.shape[:2]
    for s in scales:
        t = cv2.resize(tpl, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
        th, tw = t.shape[:2]
        if th < 4 or tw < 4 or th > gh or tw > gw:
            continue
        res = cv2.matchTemplate(gray, t, cv2.TM_CCOEFF_NORMED)
        _, mx, _, loc = cv2.minMaxLoc(res)
        if mx > best[0]:
            best = (float(mx), int(loc[0]), int(loc[1]), s)
    return best


def acquire(gray: np.ndarray, tpl: np.ndarray, cfg: SearchCfg) -> tuple[float, int, int, float]:
    """Full-frame multi-scale search. Expensive -- call rarely."""
    return _match(gray, tpl, cfg.scales())


def _acquire_roi(gray: np.ndarray, tpl: np.ndarray, cfg: SearchCfg,
                 px: float, py: float, pscale: float,
                 tpl_w: int, tpl_h: int) -> tuple[float, int, int, float]:
    """Search a padded window around the previous hit, narrow scale band."""
    gh, gw = gray.shape[:2]
    pad = max(24, int(cfg.roi_pad_frac * gw))
    bw, bh = int(tpl_w * pscale), int(tpl_h * pscale)
    x0 = max(0, int(px) - pad)
    y0 = max(0, int(py) - pad)
    x1 = min(gw, int(px) + bw + pad)
    y1 = min(gh, int(py) + bh + pad)
    roi = gray[y0:y1, x0:x1]
    d = cfg.scale_step
    band = [round(s, 4) for s in
            (pscale - 2 * d, pscale - d, pscale, pscale + d, pscale + 2 * d)
            if cfg.scale_lo - 1e-9 <= s <= cfg.scale_hi + 1e-9]
    if not band:
        band = [pscale]
    score, rx, ry, s = _match(roi, tpl, band)
    return score, rx + x0, ry + y0, s


def track_logo(video: str, tpl: np.ndarray, seed_px: tuple[int, int, int, int],
               *, t0: float, t1: float, fps: float,
               cfg: SearchCfg | None = None) -> list[TrackSample]:
    """Walk [t0, t1] frame by frame and return one raw sample per frame.

    `state` here is only the raw score verdict (hit/weak/miss); the ON/OFF
    decision is gate_track's job.
    """
    cfg = cfg or SearchCfg()
    tpl_h, tpl_w = tpl.shape[:2]
    cap = cv2.VideoCapture(video)
    out: list[TrackSample] = []
    try:
        n0, n1 = int(round(t0 * fps)), int(round(t1 * fps))
        cap.set(cv2.CAP_PROP_POS_FRAMES, n0)
        px, py, pscale = float(seed_px[0]), float(seed_px[1]), 1.0
        have_prev = False
        prev_small: np.ndarray | None = None

        for n in range(n0, n1 + 1):
            ok, frame = cap.read()
            if not ok:
                break
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

            # full-frame diff -> tells ABSENT (scene change) from OCCLUDED
            small = cv2.resize(gray, (160, 284), interpolation=cv2.INTER_AREA)
            if prev_small is None:
                scene_diff = 0.0
            else:
                scene_diff = float(np.abs(small.astype(np.int16) -
                                          prev_small.astype(np.int16)).mean())
            prev_small = small

            if have_prev and scene_diff <= cfg.scene_diff:
                score, x, y, s = _acquire_roi(gray, tpl, cfg, px, py, pscale,
                                              tpl_w, tpl_h)
                if score < cfg.drop:                      # ROI lost it -> widen
                    fscore, fx, fy, fs = acquire(gray, tpl, cfg)
                    # Only teleport on a CONFIDENT full-frame hit. Accepting any
                    # improvement lets a weak match elsewhere in frame drag the
                    # overlay across the picture -- clips 81/86 jumped 82px in a
                    # single frame that way.
                    if fscore >= cfg.accept:
                        score, x, y, s = fscore, fx, fy, fs
            else:
                score, x, y, s = acquire(gray, tpl, cfg)

            state = HIT if score >= cfg.accept else (WEAK if score >= cfg.drop else MISS)
            if state != MISS:
                px, py, pscale = float(x), float(y), s
                have_prev = True
            out.append(TrackSample(n=n, t=n / fps, x=float(x), y=float(y),
                                   scale=s, score=score, scene_diff=scene_diff,
                                   state=state))
    finally:
        cap.release()
    return out


def gate_track(samples: list[TrackSample],
               cfg: SearchCfg | None = None) -> list[TrackSample]:
    """Schmitt trigger + run filters. Decides which frames get composited.

    Rules, in order:
      * score >= accept                     -> ON
      * scene change (diff > scene_diff)    -> OFF at once, no hold. The mark is
                                               genuinely absent (endcard / store
                                               page), so holding would float our
                                               logo over a card for ~6 frames.
      * score < accept, no scene change     -> OCCLUDED: hold up to hold_frames
                                               at a damped predicted position
                                               (a hand sweeping the icon).
      * ON runs  < min_run_frames           -> deleted (no 0.3 s flashes)
      * OFF runs < hold_frames              -> bridged to ON
    """
    cfg = cfg or SearchCfg()
    if not samples:
        return []

    out: list[TrackSample] = []
    miss_streak = 0
    last_on: TrackSample | None = None
    vx = vy = 0.0

    for s in samples:
        if s.scene_diff > cfg.scene_diff:
            miss_streak = cfg.hold_frames          # kill the hold immediately
            last_on = None
            out.append(replace(s, state=OFF))
            continue

        if s.score >= cfg.accept:
            if last_on is not None:
                vx, vy = s.x - last_on.x, s.y - last_on.y
            miss_streak = 0
            last_on = s
            out.append(replace(s, state=HIT))
            continue

        # below accept -> occluded or fading
        miss_streak += 1
        if last_on is not None and miss_streak <= cfg.hold_frames:
            # predict from the last good position, damping the velocity so a
            # long hold doesn't sail the logo off across the frame
            damp = 0.8 ** miss_streak
            out.append(replace(s, state=HOLD,
                               x=last_on.x + vx * miss_streak * damp,
                               y=last_on.y + vy * miss_streak * damp,
                               scale=last_on.scale))
        else:
            last_on = None
            out.append(replace(s, state=OFF))

    out = _bridge_short_off(out, cfg.hold_frames)
    out = _drop_short_on(out, cfg.min_run_frames)
    return out


def _runs(samples: list[TrackSample]) -> list[tuple[int, int, bool]]:
    """Contiguous (start_idx, end_idx_exclusive, is_on) runs."""
    runs, i = [], 0
    while i < len(samples):
        on = samples[i].on
        j = i
        while j < len(samples) and samples[j].on == on:
            j += 1
        runs.append((i, j, on))
        i = j
    return runs


def _bridge_short_off(samples: list[TrackSample], max_gap: int) -> list[TrackSample]:
    """Fill brief OFF gaps between two ON runs by interpolating position."""
    out = list(samples)
    for a, b, on in _runs(samples):
        if on or a == 0 or b >= len(samples) or (b - a) > max_gap:
            continue
        lo, hi = out[a - 1], out[b]
        span = b - a + 1
        for k in range(a, b):
            f = (k - a + 1) / span
            out[k] = replace(out[k], state=HOLD,
                             x=lo.x + (hi.x - lo.x) * f,
                             y=lo.y + (hi.y - lo.y) * f,
                             scale=lo.scale + (hi.scale - lo.scale) * f)
    return out


def _drop_short_on(samples: list[TrackSample], min_run: int) -> list[TrackSample]:
    """Delete ON runs that never really acquired the mark.

    Counts HIT frames only, never HOLD. A HOLD frame is a guess, so letting it
    count towards the run length lets a 4-frame blip pad itself out to the
    threshold with 6 held frames and survive -- which is exactly the 0.3s flash
    this filter exists to remove.
    """
    out = list(samples)
    for a, b, on in _runs(samples):
        if not on:
            continue
        if sum(1 for k in range(a, b) if samples[k].state == HIT) < min_run:
            for k in range(a, b):
                out[k] = replace(out[k], state=OFF)
    return out


def smooth_track(samples: list[TrackSample], *, median_win: int = 5,
                 pos_alpha: float = 0.35,
                 scale_alpha: float = 0.15) -> list[TrackSample]:
    """Median-then-EMA per ON run, then quantize to integer pixels.

    Scale is smoothed harder than position on purpose: a breathing size is far
    more visible than a wobbling position. Quantizing last avoids resampling the
    logo to a fractional size every frame, which shimmers.
    """
    out = list(samples)
    for a, b, on in _runs(samples):
        if not on or b - a < 2:
            continue
        xs = np.array([out[i].x for i in range(a, b)], dtype=np.float64)
        ys = np.array([out[i].y for i in range(a, b)], dtype=np.float64)
        ss = np.array([out[i].scale for i in range(a, b)], dtype=np.float64)
        xs, ys, ss = (_median(v, median_win) for v in (xs, ys, ss))
        xs, ys = _ema(xs, pos_alpha), _ema(ys, pos_alpha)
        ss = _ema(ss, scale_alpha)
        for k, i in enumerate(range(a, b)):
            out[i] = replace(out[i], x=float(round(xs[k])), y=float(round(ys[k])),
                             scale=float(ss[k]))
    return out


def _median(v: np.ndarray, win: int) -> np.ndarray:
    if win < 3 or v.size < win:
        return v
    if win % 2 == 0:
        win += 1
    pad = win // 2
    padded = np.pad(v, pad, mode="edge")
    return np.array([np.median(padded[i:i + win]) for i in range(v.size)])


def _ema(v: np.ndarray, alpha: float) -> np.ndarray:
    if v.size == 0:
        return v
    out = np.empty_like(v)
    out[0] = v[0]
    for i in range(1, v.size):
        out[i] = alpha * v[i] + (1 - alpha) * out[i - 1]
    return out


def track_report(samples: list[TrackSample], tpl_w: int, tpl_h: int) -> dict:
    """QA numbers for the gate CSV. Empty dict when nothing was tracked."""
    if not samples:
        return {}
    on = [s for s in samples if s.on]
    sc = np.array([s.score for s in samples])
    runs = _runs(samples)
    flips = sum(1 for _, _, o in runs if o)
    d = {
        "frames": len(samples),
        "on_frames": len(on),
        "on_pct": round(100 * len(on) / len(samples), 1),
        "score_p05": round(float(np.percentile(sc, 5)), 3),
        "score_med": round(float(np.median(sc)), 3),
        "on_runs": flips,
        "hold_frames": sum(1 for s in samples if s.state == HOLD),
        "scene_cuts": sum(1 for s in samples if s.scene_diff > 25.0),
    }
    if on:
        xs = np.array([s.x for s in on]); ys = np.array([s.y for s in on])
        ss = np.array([s.scale for s in on])
        d |= {
            "travel_x": int(xs.max() - xs.min()),
            "travel_y": int(ys.max() - ys.min()),
            "scale_lo": round(float(ss.min()), 3),
            "scale_hi": round(float(ss.max()), 3),
            "box_px": f"{int(tpl_w*np.median(ss))}x{int(tpl_h*np.median(ss))}",
        }
        # jitter after smoothing -- the flag for "swimming"
        if len(on) > 1:
            dx = np.abs(np.diff(xs)); dy = np.abs(np.diff(ys)); ds = np.abs(np.diff(ss))
            d |= {"max_dpos": round(float(max(dx.max(), dy.max())), 2),
                  "max_dscale": round(float(ds.max()), 4)}
    return d
