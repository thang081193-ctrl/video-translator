"""Generic brand-pass batch wrapper (Claude Code skill).

Walks a source folder (flat or 1 level of language subfolders), applies the
V4c brand-pass transform to every .mp4 inside, writes outputs under --dst-root
preserving structure. Skip-if-exists, multi-process, deterministic per-file
seed when --seed-base is given.

Usage:
    python -u run.py --src-root <in> --dst-root <out> --watermark <logo.png> \\
                     --outro-title <brand> --outro-subtitle <tagline> \\
                     [--brand-bg <1080x1920.png>] [--trim-endcard] \\
                     [--workers 4] [--limit 0] [--seed-base 20260516] \\
                     [--watermark-size 140] [--outro-logo-size 300]
"""
from __future__ import annotations

import argparse
import csv
import os
import random
import sys
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

REPO_ROOT = Path(os.environ.get("VIDEO_TRANSLATOR_ROOT",
                                r"D:/Dev/Tools/Video Translator"))
if not (REPO_ROOT / "pipeline" / "brand_pass.py").exists():
    sys.exit(f"ERROR: brand_pass.py not found under {REPO_ROOT}. "
             f"Set VIDEO_TRANSLATOR_ROOT env var or fix the path in this script.")
sys.path.insert(0, str(REPO_ROOT))

from pipeline.brand_pass import brand_pass_video  # noqa: E402


def process_one(args):
    (src_path, dst_path, seed, watermark, watermark_size,
     outro_title, outro_subtitle, outro_logo, outro_logo_size,
     brand_bg, trim_endcard, bgm_replace, outro_video) = args
    src = Path(src_path)
    dst = Path(dst_path)
    if dst.exists() and dst.stat().st_size > 100_000:
        return ("skip", src.name, 0.0, "")
    dst.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    try:
        kwargs = dict(
            input_path=str(src),
            output_path=str(dst),
            outro_title=outro_title,
            outro_subtitle=outro_subtitle,
            trim_endcard=trim_endcard,
            random_seed=seed,
        )
        if watermark:
            kwargs["watermark_image"] = watermark
            kwargs["watermark_size"] = watermark_size
        if outro_logo:
            kwargs["outro_logo_image"] = outro_logo
            kwargs["outro_logo_size"] = outro_logo_size
        if outro_video:
            kwargs["outro_video"] = outro_video
        if brand_bg:
            kwargs["pad_bg_image"] = brand_bg
        if bgm_replace:
            kwargs["bgm_replace_path"] = bgm_replace
        brand_pass_video(**kwargs)
        return ("ok", src.name, time.time() - t0, "")
    except Exception as e:
        return ("err", src.name, time.time() - t0,
                f"{type(e).__name__}: {e}\n{traceback.format_exc()[-500:]}")


def pick_bgm(filename: str, cluster_map: dict, pool_root: Path,
             mode: str, rng: random.Random) -> str | None:
    """Pick a BGM replacement track for a source filename.

    by-mood:  look up filename in cluster_map → random pick from <pool>/<cluster>/
    random:   random pick from all *.mp3/*.wav under <pool>/ (recursive)
    None:     no replacement (skip)

    Falls back to random across the whole pool if cluster subfolder is missing.
    """
    if mode == "by-mood":
        cluster = cluster_map.get(filename)
        if cluster:
            subdir = pool_root / cluster
            if subdir.is_dir():
                tracks = (sorted(subdir.glob("*.mp3"))
                          + sorted(subdir.glob("*.wav"))
                          + sorted(subdir.glob("*.m4a")))
                if tracks:
                    return str(rng.choice(tracks))
    all_tracks = (sorted(pool_root.rglob("*.mp3"))
                  + sorted(pool_root.rglob("*.wav"))
                  + sorted(pool_root.rglob("*.m4a")))
    return str(rng.choice(all_tracks)) if all_tracks else None


def load_cluster_map(csv_path: Path) -> dict:
    """Load file→cluster mapping from a CSV with columns `file,cluster`."""
    if not csv_path.is_file():
        return {}
    with open(csv_path, encoding="utf-8") as f:
        return {r["file"]: r["cluster"] for r in csv.DictReader(f) if r.get("cluster")}


def collect_jobs(src_root: Path, dst_root: Path):
    """Walk src_root: flat (.mp4 direct children) or 1-level lang subfolders.

    Excludes any subfolder that IS the dst_root (handles dst_root nested under
    src_root, where prior outputs would otherwise get re-scanned as inputs).

    Returns list of (src_path, dst_path) tuples preserving structure.
    """
    jobs = []
    dst_resolved = dst_root.resolve()
    flat_videos = sorted(src_root.glob("*.mp4"))
    if flat_videos:
        for mp4 in flat_videos:
            jobs.append((str(mp4), str(dst_root / mp4.name)))
    for sub in sorted(src_root.iterdir()):
        if not sub.is_dir():
            continue
        # Skip dst_root (exact match) AND any sibling "_*" convention folder
        # (e.g. _branded, _branded_v2, _tmp) — these are typically prior outputs
        # or work folders, not language subfolders.
        if sub.resolve() == dst_resolved:
            continue
        if sub.name.startswith("_"):
            continue
        out_sub = dst_root / sub.name
        for mp4 in sorted(sub.glob("*.mp4")):
            jobs.append((str(mp4), str(out_sub / mp4.name)))
    return jobs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src-root", required=True)
    ap.add_argument("--dst-root", required=True)
    ap.add_argument("--watermark", default=None, help="PNG logo for corner watermark")
    ap.add_argument("--watermark-size", type=int, default=140)
    ap.add_argument("--outro-title", default="DecoAI")
    ap.add_argument("--outro-subtitle", default="Free AI Home Design")
    ap.add_argument("--outro-logo", default=None,
                    help="Logo PNG for outro card (defaults to --watermark if not set)")
    ap.add_argument("--outro-logo-size", type=int, default=300)
    ap.add_argument("--outro-video", default=None,
                    help="Pre-made MP4 outro card. If set, replaces Pillow-generated outro.")
    ap.add_argument("--brand-bg", default=None,
                    help="1080x1920 brand-designed PNG canvas for non-9:16 sources")
    ap.add_argument("--trim-endcard", action="store_true",
                    help="Auto-detect + trim competitor end-card from source tail")
    ap.add_argument("--bgm-pool", default=None,
                    help="Folder of royalty-free BGM tracks. With --bgm-mode by-mood, "
                         "expects cluster subfolders (e.g. A_cinematic/, B_indiepop/).")
    ap.add_argument("--bgm-mode", choices=["by-mood", "random"], default="by-mood",
                    help="by-mood: pick track from <pool>/<cluster>/ using --bgm-cluster-map; "
                         "random: pick any track under <pool>/.")
    ap.add_argument("--bgm-cluster-map", default=None,
                    help="CSV path mapping `file,cluster` for by-mood mode. "
                         "Defaults to <bgm-pool>/cluster_map.csv if present.")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int, default=0, help="0 = all videos")
    ap.add_argument("--seed-base", type=int, default=0,
                    help="0 = random per file; >0 = deterministic (file N → seed_base + N)")
    args = ap.parse_args()

    src_root = Path(args.src_root).resolve()
    dst_root = Path(args.dst_root).resolve()
    if not src_root.is_dir():
        sys.exit(f"src-root not a directory: {src_root}")
    for p in [args.watermark, args.outro_logo, args.brand_bg, args.outro_video]:
        if p and not Path(p).is_file():
            sys.exit(f"file not found: {p}")
    outro_logo = args.outro_logo or args.watermark

    raw_jobs = collect_jobs(src_root, dst_root)
    if args.limit:
        raw_jobs = raw_jobs[:args.limit]
    if not raw_jobs:
        sys.exit(f"No .mp4 files found under {src_root}")

    # BGM pool setup (optional)
    bgm_pool = None
    cluster_map = {}
    if args.bgm_pool:
        bgm_pool = Path(args.bgm_pool).resolve()
        if not bgm_pool.is_dir():
            sys.exit(f"--bgm-pool not a directory: {bgm_pool}")
        cmap_path = (Path(args.bgm_cluster_map) if args.bgm_cluster_map
                     else bgm_pool / "cluster_map.csv")
        cluster_map = load_cluster_map(cmap_path)
        if args.bgm_mode == "by-mood" and not cluster_map:
            sys.exit(f"--bgm-mode by-mood requires cluster_map.csv at {cmap_path}")

    jobs = []
    for i, (src, dst) in enumerate(raw_jobs):
        seed = (args.seed_base + i) if args.seed_base else None
        bgm_replace = None
        if bgm_pool:
            rng = random.Random(seed if seed is not None else hash(src))
            bgm_replace = pick_bgm(Path(src).name, cluster_map, bgm_pool,
                                    args.bgm_mode, rng)
        jobs.append((src, dst, seed, args.watermark, args.watermark_size,
                     args.outro_title, args.outro_subtitle, outro_logo,
                     args.outro_logo_size, args.brand_bg, args.trim_endcard,
                     bgm_replace, args.outro_video))
    print(f"Total jobs: {len(jobs)}  workers={args.workers}  "
          f"endcard_trim={args.trim_endcard}  brand_bg={'yes' if args.brand_bg else 'no'}  "
          f"bgm_pool={'yes (mode=' + args.bgm_mode + ')' if bgm_pool else 'no (use source BGM)'}",
          flush=True)

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
            if n % 3 == 0 or n == len(jobs):
                avg = sum(durations)/len(durations) if durations else 0
                eta_s = (len(jobs) - n) * (avg / args.workers if avg else 0)
                elapsed = time.time() - t_start
                print(f"[{n:>3}/{len(jobs)}] ok={ok} skip={skip} err={err}  "
                      f"avg/file={avg:.1f}s  elapsed={elapsed/60:.1f}m  ETA={eta_s/60:.1f}m",
                      flush=True)

    print(f"\nDONE: ok={ok} skip={skip} err={err}  "
          f"total_elapsed={(time.time()-t_start)/60:.1f}m", flush=True)
    if errors:
        print("\nErrors:")
        for e in errors[:20]:
            print(f"  {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
