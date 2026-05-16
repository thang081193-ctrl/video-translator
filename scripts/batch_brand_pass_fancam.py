"""Batch brand-pass for all FANCAM videos.

Reads from 1605/FANCAM/<lang>/, writes to 1605/_branded/FANCAM/<lang>/ using
Artify Gen brand (logo overlay + outro card), with end-card auto-trim.

Run:
    python -u scripts/batch_brand_pass_fancam.py [--workers N] [--limit N] [--seed-base N]
"""
from __future__ import annotations

import argparse
import os
import sys
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from pipeline.brand_pass import brand_pass_video

SRC_ROOT = Path(r"D:/Dev/App Details/Artify Gen/Video/1605/FANCAM")
DST_ROOT = Path(r"D:/Dev/App Details/Artify Gen/Video/1605/_branded/FANCAM")
LOGO     = Path(r"D:/Dev/App Details/Artify Gen/Logo.png")
BRAND_TITLE = "Artify Gen"
BRAND_SUB   = "AI Photo Studio"


def process_one(args):
    src_path_str, dst_path_str, seed = args
    src = Path(src_path_str)
    dst = Path(dst_path_str)
    if dst.exists() and dst.stat().st_size > 100_000:
        return ("skip", src.name, 0.0, "")
    dst.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    try:
        brand_pass_video(
            input_path=str(src),
            output_path=str(dst),
            watermark_image=str(LOGO),
            watermark_size=140,
            outro_logo_image=str(LOGO),
            outro_logo_size=300,
            outro_title=BRAND_TITLE,
            outro_subtitle=BRAND_SUB,
            trim_endcard=True,
            random_seed=seed,
        )
        return ("ok", src.name, time.time() - t0, "")
    except Exception as e:
        return ("err", src.name, time.time() - t0,
                f"{type(e).__name__}: {e}\n{traceback.format_exc()[-400:]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int, default=0, help="0 = all")
    ap.add_argument("--seed-base", type=int, default=20260516,
                    help="seed for file N = seed_base + N (deterministic)")
    args = ap.parse_args()

    jobs = []
    for lang_dir in sorted(SRC_ROOT.iterdir()):
        if not lang_dir.is_dir(): continue
        out_lang = DST_ROOT / lang_dir.name
        for i, mp4 in enumerate(sorted(lang_dir.glob("*.mp4"))):
            seed = args.seed_base + len(jobs)
            jobs.append((str(mp4), str(out_lang / mp4.name), seed))
    if args.limit:
        jobs = jobs[:args.limit]
    print(f"Total jobs: {len(jobs)}  workers={args.workers}", flush=True)

    ok = skip = err = 0
    errors = []
    durations = []
    t_start = time.time()
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(process_one, j): j for j in jobs}
        for n, fut in enumerate(as_completed(futs), 1):
            status, name, dur, msg = fut.result()
            if status == "ok":
                ok += 1
                durations.append(dur)
            elif status == "skip":
                skip += 1
            else:
                err += 1
                errors.append(f"{name}: {msg}")
            if n % 5 == 0 or n == len(jobs):
                avg = sum(durations)/len(durations) if durations else 0
                eta_s = (len(jobs) - n) * (avg / args.workers if avg else 0)
                elapsed = time.time() - t_start
                print(f"[{n:>3}/{len(jobs)}] ok={ok} skip={skip} err={err}  "
                      f"avg/file={avg:.1f}s  elapsed={elapsed/60:.1f}m  ETA={eta_s/60:.1f}m",
                      flush=True)

    print(f"\nDONE: ok={ok} skip={skip} err={err}  total_elapsed={(time.time()-t_start)/60:.1f}m",
          flush=True)
    if errors:
        print("\nErrors:")
        for e in errors[:20]:
            print(f"  {e}")


if __name__ == "__main__":
    main()
