"""
trim_endcard.py — standalone competitor end-card trimmer.

Walks a folder of .mp4 files, detects any static competitor outro/end-card
appended to the tail, and outputs trimmed copies (or trims in-place with
--inplace). Skips files where no clear end-card is found.

Detection: ffmpeg scene-change at threshold 0.08 → earliest scene change
in the last (1-min_drop_pct) of duration that leaves at least min_tail_s
of content after trim. This catches both hard cuts AND soft fades into
competitor outro cards, and handles multi-card outros (e.g. "TRY NOW!" →
"Download Now" sequence) by cutting at the first card.

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


def detect_endcard_start(video_path: str,
                         min_drop_pct: float = 0.7,
                         min_tail_s: float = 0.3) -> float | None:
    """Return timestamp (s) where competitor end-card starts, or None.

    Uses ffmpeg scene-change at threshold 0.08 to catch both hard cuts and
    soft fades. Takes the EARLIEST scene change in the last (1-min_drop_pct)
    portion of the video — so multi-card outros are trimmed from the first card.
    """
    try:
        dur = _ffprobe_duration(video_path)
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
        cut = min(valid)
        return cut
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
