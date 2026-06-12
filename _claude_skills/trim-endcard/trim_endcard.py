"""
trim_endcard.py — standalone competitor end-card trimmer.

Walks a folder of .mp4 files, detects any competitor outro/end-card appended
to the tail, and outputs trimmed copies (or trims in-place with --inplace).
Skips files where no clear end-card is found.

Detection v2 — reverse frame-matching (primary): the tail window is sampled
at 4 fps in small grayscale; each frame is compared against the LAST frame
(the card the video ends on). Card membership = small mean diff AND few moved
pixels, which tolerates animated CTAs (pulsing button) that freeze/scene
detectors miss, while a real content cut fails both. Multi-card outros
("TRY NOW!" → "Download Now") are walked by re-anchoring the reference at
each plateau; the content→card boundary is then refined at 12 fps and cut
with a 0.1 s pre-roll (removes fade-in remnants). Guards: card plateau must
be ≥0.7 s (sub-1 s only across a hard cut), must NOT fill the whole scan
window, scan window = min(14 s, 45% of duration).

Fallback (numpy unavailable): the old scene-change scan at threshold 0.08,
earliest change in the last (1-min_drop_pct) of duration.

Usage:
    python trim_endcard.py --src <folder> --dst <out_folder>
                           [--inplace]
                           [--min-drop-pct 0.7]
                           [--min-tail-s 0.3]
                           [--workers 4]
                           [--limit 0]
                           [--dry-run]
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path


# ── Detection ─────────────────────────────────────────────────────────────────

def _ffprobe_duration(path: str) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True,
    )
    return float(r.stdout.strip())


def _read_tail_frames(video_path: str, t0: float, dur: float,
                      fps: float = 4.0, w: int = 96, h: int = 170):
    """Decode a window to small grayscale frames (numpy N×h×w int16), or None."""
    import numpy as np
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{t0:.3f}", "-i", video_path,
         "-t", f"{dur:.3f}", "-vf", f"fps={fps},scale={w}:{h}",
         "-pix_fmt", "gray", "-f", "rawvideo", "-"],
        capture_output=True, timeout=120,
    )
    n = len(r.stdout) // (w * h)
    if n == 0:
        return None
    return (np.frombuffer(r.stdout[: n * w * h], dtype=np.uint8)
            .reshape(n, h, w).astype(np.int16))


def detect_endcard_v2(video_path: str, src_dur: float, *,
                      fps: float = 4.0, max_tail_s: float = 14.0,
                      max_tail_pct: float = 0.45, min_card_s: float = 0.7,
                      max_cards: int = 3):
    """Reverse frame-matching detector. Returns (status, cut):
    ("ok", t) card found / ("none", None) no card / ("unavail", None) fallback.

    Mirrors pipeline/brand_pass.py::_detect_endcard_start_v2 — keep in sync.
    """
    try:
        import numpy as np
    except ImportError:
        return "unavail", None

    def cardlike(frame, ref):
        d = abs(frame - ref)
        return d.mean() <= 8.0 and (d > 25).mean() <= 0.15

    def still_frac(seg):
        """Card = mostly perfectly-still pairs (pulsing CTA ≈ 0.5); real
        content moves every frame (≈ 0.0, even slow pans)."""
        if len(seg) < 2:
            return 1.0
        return float(np.mean([abs(seg[k + 1] - seg[k]).mean() < 1.0
                              for k in range(len(seg) - 1)]))

    window = min(max_tail_s, src_dur * max_tail_pct)
    if window < min_card_s + 0.5:
        return "none", None
    t0 = max(0.0, src_dur - window)
    frames = _read_tail_frames(video_path, t0, window + 0.5, fps=fps)
    if frames is None or len(frames) < 3:
        return "unavail", None

    n = len(frames)
    min_card_f = max(2, int(round(min_card_s * fps)))
    earliest = n - 1
    idx = n - 1
    cards = 0
    while cards < max_cards:
        ref = frames[idx]
        start = idx
        j = idx - 1
        while j >= 0 and cardlike(frames[j], ref):
            start = j
            j -= 1
        plen = idx - start + 1
        if plen < min_card_f or still_frac(frames[start: idx + 1]) < 0.45:
            if cards == 0:
                return "none", None
            break
        cards += 1
        earliest = start
        if j < 0:
            break
        idx = j

    if earliest <= 0:
        return "none", None              # card fills the window — refuse
    cut_t = t0 + earliest / fps
    tail_len = src_dur - cut_t
    if tail_len < min_card_s:
        return "none", None
    boundary_jump = abs(frames[earliest] - frames[earliest - 1]).mean()
    if tail_len < 1.0 and boundary_jump < 20.0:
        return "none", None              # gentle sub-second boundary = settling shot

    f0 = max(0.0, cut_t - 1.0)
    fine = _read_tail_frames(video_path, f0, (cut_t - f0) + 0.5, fps=12.0)
    if fine is not None and len(fine) >= 4:
        ref = fine[-1]
        k = len(fine) - 1
        j = k - 1
        while j >= 0 and cardlike(fine[j], ref):
            k = j
            j -= 1
        refined = f0 + k / 12.0
        if 0 < refined <= cut_t + 0.4:
            cut_t = refined
    return "ok", max(0.5, cut_t - 0.10)


def detect_endcard_start(video_path: str,
                         min_drop_pct: float = 0.7,
                         min_tail_s: float = 0.3) -> float | None:
    """Return timestamp (s) where competitor end-card starts, or None.

    Primary: reverse frame-matching v2 (see detect_endcard_v2) — its verdict
    is trusted both ways. The legacy scene-change scan (threshold 0.08,
    earliest change in the last (1-min_drop_pct)) runs only when v2 is
    unavailable (no numpy / decode failure).
    """
    try:
        dur = _ffprobe_duration(video_path)
        status, cut = detect_endcard_v2(video_path, dur)
        if status == "ok":
            return cut
        if status == "none":
            return None
        # ---- fallback: legacy scene-change scan ----
        r = subprocess.run(
            ["ffmpeg", "-i", video_path,
             "-vf", "select='gte(scene,0.08)',showinfo",
             "-vsync", "0", "-an", "-f", "null", "-"],
            capture_output=True, text=True, timeout=120,
        )
        times = [float(m.group(1))
                 for m in re.finditer(r"pts_time:(\d+\.?\d*)", r.stderr)]
        if not times:
            return None
        window_start = dur * min_drop_pct
        window_end   = dur - min_tail_s
        valid = [t for t in times if window_start <= t <= window_end]
        if not valid:
            return None
        return min(valid)
    except Exception as e:
        print(f"  [WARN] detection failed: {e}", file=sys.stderr)
        return None


# ── Worker ────────────────────────────────────────────────────────────────────

def _process_one(args):
    src_path, dst_path, min_drop_pct, min_tail_s, dry_run = args
    src = Path(src_path)
    dst = Path(dst_path)

    if dst.exists() and dst.stat().st_size > 10_000:
        return ("skip", src.name, 0.0, "already exists")

    t0 = time.time()
    try:
        cut = detect_endcard_start(str(src), min_drop_pct, min_tail_s)
        if cut is None:
            if not dry_run:
                dst.parent.mkdir(parents=True, exist_ok=True)
                # No endcard found — copy as-is (stream copy, instant)
                subprocess.run(
                    ["ffmpeg", "-y", "-i", str(src),
                     "-c", "copy", str(dst)],
                    capture_output=True, check=True,
                )
            return ("passthrough", src.name, time.time() - t0, "no endcard")
        else:
            dur = _ffprobe_duration(str(src))
            tail_s = dur - cut
            msg = f"cut at {cut:.2f}s (removed {tail_s:.2f}s tail)"
            if not dry_run:
                dst.parent.mkdir(parents=True, exist_ok=True)
                subprocess.run(
                    ["ffmpeg", "-y", "-i", str(src),
                     "-t", f"{cut:.4f}",
                     "-c", "copy", "-avoid_negative_ts", "make_zero",
                     str(dst)],
                    capture_output=True, check=True,
                )
            return ("trimmed", src.name, time.time() - t0, msg)
    except Exception as e:
        return ("err", src.name, time.time() - t0,
                f"{type(e).__name__}: {e}\n{traceback.format_exc()[-400:]}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(
        description="Trim competitor end-cards from a folder of ad videos.")
    ap.add_argument("--src",           required=True, help="Source folder")
    ap.add_argument("--dst",           default=None,
                    help="Output folder (default: <src>_trimmed)")
    ap.add_argument("--inplace",       action="store_true",
                    help="Overwrite originals (uses a temp file, then renames)")
    ap.add_argument("--min-drop-pct",  type=float, default=0.7,
                    help="Earliest cut point as fraction of video (default 0.7 = last 30%%)")
    ap.add_argument("--min-tail-s",    type=float, default=0.3,
                    help="Minimum endcard length in seconds to bother trimming (default 0.3)")
    ap.add_argument("--workers",       type=int, default=4)
    ap.add_argument("--limit",         type=int, default=0, help="0 = all")
    ap.add_argument("--dry-run",       action="store_true",
                    help="Detect only — print what would be trimmed, no file writes")
    args = ap.parse_args()

    src_root = Path(args.src).resolve()
    if not src_root.is_dir():
        sys.exit(f"src not a directory: {src_root}")

    if args.inplace:
        dst_root = src_root
    elif args.dst:
        dst_root = Path(args.dst).resolve()
    else:
        dst_root = src_root.parent / (src_root.name + "_trimmed")

    # Collect jobs (flat only — one folder at a time)
    mp4s = sorted(src_root.glob("*.mp4"))
    if not mp4s:
        sys.exit(f"No .mp4 files found under {src_root}")
    if args.limit:
        mp4s = mp4s[:args.limit]

    jobs = [
        (str(p), str(dst_root / p.name),
         args.min_drop_pct, args.min_tail_s, args.dry_run)
        for p in mp4s
    ]

    mode = "DRY-RUN" if args.dry_run else ("INPLACE" if args.inplace else f"-> {dst_root}")
    print(f"Total: {len(jobs)} videos  workers={args.workers}  {mode}", flush=True)

    trimmed = passthrough = skipped = errors = 0
    total_removed_s = 0.0
    t_start = time.time()

    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(_process_one, j): j for j in jobs}
        for n, fut in enumerate(as_completed(futs), 1):
            status, name, dur, msg = fut.result()
            if status == "trimmed":
                trimmed += 1
                # Parse removed seconds from msg for reporting
                m = re.search(r"removed ([\d.]+)s", msg)
                if m:
                    total_removed_s += float(m.group(1))
                tag = f"[TRIM]  {name}: {msg}"
            elif status == "passthrough":
                passthrough += 1
                tag = f"[PASS]  {name}: {msg}"
            elif status == "skip":
                skipped += 1
                tag = f"[SKIP]  {name}"
            else:
                errors += 1
                tag = f"[ERR]   {name}: {msg}"
            print(f"[{n:>3}/{len(jobs)}] {tag}", flush=True)

    elapsed = time.time() - t_start
    print(f"\nDONE in {elapsed:.1f}s — "
          f"trimmed={trimmed}  passthrough={passthrough}  "
          f"skipped={skipped}  errors={errors}  "
          f"total_tail_removed={total_removed_s:.1f}s", flush=True)
    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
