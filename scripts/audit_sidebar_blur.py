#!/usr/bin/env python3
"""Audit finished 1080x1920 Reels for the side-blur "squish" artifact.

THE ARTIFACT (the bug we are hunting):
    A 9:16 source was wrongly cropped horizontally into its real content
    (the old _detect_side_blur false-positive) or anamorphic pixels were
    declared square without resampling. Either way the sharp content ends up
    SQUISHED into a NARROW center column and blurred copies of the content fill
    the LEFT and RIGHT sides. On screen: thin people / oval buttons in the
    middle, soft blurred bars left & right ("bóp ảnh").

WHAT WE MUST **NOT** FLAG:
    (b) Clean full-bleed 9:16  -> sharp edge-to-edge, no bars.
    (c) Legit TOP/BOTTOM blur-pad (the INTENDED look for 1:1 / 4:5 sources) ->
        sharp full-width content band in the MIDDLE, blurred bars on
        TOP and BOTTOM. Crucially the blur is horizontal bands, not vertical.

CORE SIGNAL:
    Per-COLUMN sharpness profile (Laplacian variance of each column, averaged
    over several sampled frames). The squish signature is a sharp center
    plateau flanked by two LOW-sharpness side regions that are roughly
    SYMMETRIC. Top/bottom pad has NO such left/right sharpness drop (its blur is
    in the row direction, not the column direction), so its column profile is
    flat across the full width. Full-bleed is also flat across the full width.

Usage:
    python audit_sidebar_blur.py FILE_OR_DIR [FILE_OR_DIR ...]
Exit code: nonzero if any file is flagged (AFFECTED>0).
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile

try:
    import cv2
    import numpy as np
except ImportError:
    sys.stderr.write("ERROR: this audit needs opencv-python + numpy installed.\n")
    sys.exit(2)


# ---------------------------------------------------------------------------
# Tunables (see THRESHOLDS doc in the spec). All are conservative to avoid
# false positives on a no-manual-QA batch.
# ---------------------------------------------------------------------------
N_FRAMES = 7              # frames sampled evenly between 15%..85% of duration
COL_BANDS = 108           # downsample width into this many column-bands (1080/108 = 10px/band)
EDGE_IGNORE_FRAC = 0.02   # ignore outermost 2% of width (codec ring at extreme edge)
MIN_BAR_FRAC = 0.06       # each side bar must be >= 6% of width to count (avoids hairline)
MAX_BAR_FRAC = 0.34       # but < 34% per side (else it's not "narrowed content", it's something else)
SHARP_RATIO = 0.45        # a side band is "blurred" if its sharpness <= 0.45 * center sharpness
SYMMETRY_TOL = 0.45       # |leftbar - rightbar| / max(...) must be <= 0.45 (bars roughly symmetric)
CENTER_MIN_VAR = 8.0      # center must actually be sharp (lap-var floor) to call the sides "blurred"
NOT_PAD_MARGIN = 0.02     # side bars must exceed top/bottom bars by this fraction (vs legit top/bot pad)


def _ffprobe_duration(path: str) -> float:
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, check=True,
        )
        return float(r.stdout.strip())
    except Exception:
        return 0.0


def _extract_frames(path: str, n: int) -> list["np.ndarray"]:
    dur = _ffprobe_duration(path)
    if dur <= 0:
        dur = 3.0
    frames = []
    with tempfile.TemporaryDirectory() as tmp:
        for i in range(n):
            t = dur * (0.15 + 0.70 * i / max(n - 1, 1))
            fp = os.path.join(tmp, f"f{i}.png")
            r = subprocess.run(
                ["ffmpeg", "-y", "-ss", f"{t:.2f}", "-i", path,
                 "-frames:v", "1", fp],
                capture_output=True, timeout=30,
            )
            if r.returncode == 0 and os.path.isfile(fp):
                g = cv2.imread(fp, cv2.IMREAD_GRAYSCALE)
                if g is not None:
                    frames.append(g)
    return frames


def _col_sharpness(frames: list["np.ndarray"], n_bands: int) -> "np.ndarray":
    """Mean per-column-band Laplacian variance across frames.

    For each frame: compute the Laplacian, take abs, then for each vertical
    column compute the variance of the Laplacian down that column. A blurred
    column has near-zero high-freq energy -> low variance; a sharp column has
    high variance. Average over frames, then bin columns into n_bands.
    """
    profiles = []
    for g in frames:
        lap = cv2.Laplacian(g, cv2.CV_64F, ksize=3)
        # per-column variance (variance of laplacian values down each column)
        col_var = lap.var(axis=0)            # shape (W,)
        profiles.append(col_var)
    prof = np.mean(profiles, axis=0)         # shape (W,)
    W = prof.shape[0]
    # bin into n_bands
    idx = (np.arange(W) * n_bands // W)
    banded = np.zeros(n_bands)
    counts = np.zeros(n_bands)
    np.add.at(banded, idx, prof)
    np.add.at(counts, idx, 1)
    counts[counts == 0] = 1
    return banded / counts


def _row_sharpness(frames: list["np.ndarray"], n_bands: int) -> "np.ndarray":
    """Mean per-row-band Laplacian variance across frames (for the top/bot guard)."""
    profiles = []
    for g in frames:
        lap = cv2.Laplacian(g, cv2.CV_64F, ksize=3)
        row_var = lap.var(axis=1)            # shape (H,)
        profiles.append(row_var)
    prof = np.mean(profiles, axis=0)
    Hh = prof.shape[0]
    idx = (np.arange(Hh) * n_bands // Hh)
    banded = np.zeros(n_bands)
    counts = np.zeros(n_bands)
    np.add.at(banded, idx, prof)
    np.add.at(counts, idx, 1)
    counts[counts == 0] = 1
    return banded / counts


def _measure_bar(profile: "np.ndarray") -> dict:
    """Given a 1-D sharpness profile (already banded), measure how many bands
    on EACH end are 'blurred' relative to the sharp center plateau.

    Returns dict with left_frac, right_frac (fraction of width that is a blurred
    side bar), center_var, side_floor, symmetry.
    """
    n = profile.shape[0]
    ig = max(1, int(round(n * EDGE_IGNORE_FRAC)))
    p = profile[ig:n - ig] if n - 2 * ig > 4 else profile
    m = p.shape[0]
    # center plateau = median of the middle 40% (robust to text/no-text)
    c0, c1 = int(m * 0.30), int(m * 0.70)
    center_var = float(np.median(p[c0:c1])) if c1 > c0 else float(np.median(p))
    thr = center_var * SHARP_RATIO
    # walk in from the left while bands are below threshold (i.e. blurred)
    left = 0
    while left < m and p[left] <= thr:
        left += 1
    right = 0
    while right < m and p[m - 1 - right] <= thr:
        right += 1
    left_frac = left / n
    right_frac = right / n
    denom = max(left_frac, right_frac, 1e-9)
    symmetry = abs(left_frac - right_frac) / denom
    return {
        "left_frac": left_frac,
        "right_frac": right_frac,
        "center_var": center_var,
        "symmetry": symmetry,
    }


def audit_file(path: str) -> tuple[bool, str]:
    """Return (flagged, reason)."""
    w_h = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0", path],
        capture_output=True, text=True,
    )
    dims = w_h.stdout.strip()
    frames = _extract_frames(path, N_FRAMES)
    if not frames:
        return False, f"SKIP (no frames decodable) [{dims}]"

    col = _col_sharpness(frames, COL_BANDS)
    cm = _measure_bar(col)

    left_f, right_f = cm["left_frac"], cm["right_frac"]
    center_var = cm["center_var"]
    symmetry = cm["symmetry"]

    # --- decision gates (ALL must hold to FLAG) ---
    # 1. center must actually be sharp content
    g_center = center_var >= CENTER_MIN_VAR
    # 2. BOTH sides must have a blurred bar of meaningful width
    g_both = (left_f >= MIN_BAR_FRAC) and (right_f >= MIN_BAR_FRAC)
    # 3. neither bar so wide it's not a "narrowed center" anymore
    g_width = (left_f <= MAX_BAR_FRAC) and (right_f <= MAX_BAR_FRAC)
    # 4. bars roughly symmetric (squish pads both sides ~equally)
    g_sym = symmetry <= SYMMETRY_TOL

    # 5. TOP/BOTTOM guard: if the ROW profile shows an EQUAL OR LARGER blurred
    #    band on top/bottom than the column profile shows on left/right, the
    #    blur lives in the row direction -> this is legit top/bottom pad,
    #    NOT a side-bar squish. (Full-bleed has neither; this guard is inert.)
    row = _row_sharpness(frames, COL_BANDS)
    rm = _measure_bar(row)
    topbot_frac = max(rm["left_frac"], rm["right_frac"])  # reuse: ends of row profile = top/bottom
    sidebar_frac = max(left_f, right_f)
    g_not_pad = sidebar_frac > (topbot_frac + NOT_PAD_MARGIN)  # sides must dominate over top/bottom

    flagged = g_center and g_both and g_width and g_sym and g_not_pad

    reason = (
        f"[{dims}] L={left_f*100:4.1f}% R={right_f*100:4.1f}% "
        f"center_lapvar={center_var:7.1f} sym={symmetry:.2f} "
        f"topbot={topbot_frac*100:4.1f}% "
        f"| gates: center={int(g_center)} both={int(g_both)} "
        f"width={int(g_width)} sym={int(g_sym)} not_pad={int(g_not_pad)}"
    )
    return flagged, reason


def _iter_mp4s(args: list[str]):
    for a in args:
        if os.path.isdir(a):
            for root, _d, files in os.walk(a):
                for fn in sorted(files):
                    if fn.lower().endswith(".mp4"):
                        yield os.path.join(root, fn)
        elif os.path.isfile(a) and a.lower().endswith(".mp4"):
            yield a


def main(argv: list[str]) -> int:
    targets = argv[1:]
    if not targets:
        sys.stderr.write("usage: audit_sidebar_blur.py FILE_OR_DIR [...]\n")
        return 2
    affected = 0
    total = 0
    for fp in _iter_mp4s(targets):
        total += 1
        try:
            flagged, reason = audit_file(fp)
        except Exception as e:
            print(f"ERROR  {os.path.basename(fp)}: {e}")
            continue
        verdict = "FLAG " if flagged else "clean"
        if flagged:
            affected += 1
        print(f"{verdict}  {os.path.basename(fp)}  {reason}")
    print(f"AFFECTED={affected} (of {total})")
    return 1 if affected > 0 else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
