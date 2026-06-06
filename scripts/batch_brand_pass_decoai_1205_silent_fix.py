"""Brand-pass retry for silent-ad files that failed with `tts.mp3 invalid` error.

Per brand-pass SKILL §8 — silent ads have no voice, Whisper returns empty transcript,
Edge TTS produces 0-byte mp3, ffmpeg mix fails. Fix: pass explicit `transcript` fallback.

Cycles through 5 brand-aligned voiceover lines per file index for variety.
"""
from __future__ import annotations

import argparse
import sys
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from pipeline.brand_pass import brand_pass_video

SRC_ROOT = Path(r"D:/Dev/App Details/Home Decor/Video/1205")
DST_ROOT = Path(r"D:/Dev/App Details/Home Decor/Video/1205/_branded")
LOGO     = Path(r"D:/Dev/App Details/Home Decor/DecoAI/Logo.png")
WORK_ROOT = Path(r"D:/temp/brandpass")
BRAND_TITLE = "DecoAI"
BRAND_SUB   = "Redesign Any Room in Seconds"

# Brand-aligned fallback transcripts (rotated per file index)
FALLBACK_TRANSCRIPTS = [
    "Snap a photo. AI redesigns your room in seconds. Try DecoAI free today.",
    "Stop overthinking decor. DecoAI gives you the design in five seconds.",
    "Any room, any style. DecoAI handles it. Free to try right now.",
    "Designed by AI in seconds. DecoAI is your interior design assistant.",
    "Try DecoAI free. Redesign any room from a single photo.",
]

# Missing files identified from previous batch failures
MISSING_FILES = [
    ("English", "EN_1205018.mp4"), ("English", "EN_1205020.mp4"),
    ("English", "EN_1205024.mp4"), ("English", "EN_1205027.mp4"),
    ("English", "EN_1205029.mp4"), ("English", "EN_1205031.mp4"),
    ("English", "EN_1205032.mp4"), ("English", "EN_1205033.mp4"),
    ("English", "EN_1205035.mp4"), ("English", "EN_1205036.mp4"),
    ("English", "EN_1205037.mp4"), ("English", "EN_1205039.mp4"),
    ("English", "EN_1205041.mp4"), ("English", "EN_1205043.mp4"),
    ("English", "EN_1205044.mp4"), ("English", "EN_1205045.mp4"),
    ("English", "EN_1205046.mp4"), ("English", "EN_1205048.mp4"),
    ("English", "EN_1205049.mp4"), ("English", "EN_1205050.mp4"),
    ("English", "EN_1205051.mp4"), ("English", "EN_1205052.mp4"),
    ("English", "EN_1205053.mp4"), ("English", "EN_1205054.mp4"),
    ("English", "EN_1205056.mp4"), ("English", "EN_1205057.mp4"),
    ("English", "EN_1205058.mp4"), ("English", "EN_1205060.mp4"),
    ("English", "EN_1205061.mp4"), ("English", "EN_1205064.mp4"),
    ("English", "EN_1205065.mp4"), ("English", "EN_1205068.mp4"),
    ("English", "EN_1205072.mp4"), ("English", "EN_1205073.mp4"),
    ("English", "EN_1205075.mp4"), ("English", "EN_1205076.mp4"),
    ("English", "EN_1205078.mp4"), ("English", "EN_1205079.mp4"),
    ("English", "EN_1205080.mp4"), ("English", "EN_1205081.mp4"),
    ("English", "EN_1205082.mp4"), ("English", "EN_1205084.mp4"),
    ("English", "EN_1205087.mp4"), ("English", "EN_1205089.mp4"),
    ("English", "EN_1205090.mp4"), ("English", "EN_1205091.mp4"),
    ("English", "EN_1205092.mp4"), ("English", "EN_1205093.mp4"),
    ("English", "EN_1205094.mp4"), ("English", "EN_1205095.mp4"),
    ("English", "EN_1205096.mp4"), ("English", "EN_1205097.mp4"),
    ("English", "EN_1205098.mp4"), ("English", "EN_1205099.mp4"),
    ("English", "EN_1205100.mp4"), ("English", "EN_1205101.mp4"),
    ("Español", "ES_120502.mp4"),
    ("Русский", "RU_120501.mp4"),
]


def process_one(args):
    src_path_str, dst_path_str, seed, transcript = args
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
            work_root=str(WORK_ROOT),
            transcript=transcript,
        )
        return ("ok", src.name, time.time() - t0, "")
    except Exception as e:
        return ("err", src.name, time.time() - t0,
                f"{type(e).__name__}: {e}\n{traceback.format_exc()[-400:]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--seed-base", type=int, default=20260518)
    args = ap.parse_args()

    jobs = []
    for idx, (lang, fname) in enumerate(MISSING_FILES):
        src = SRC_ROOT / lang / fname
        dst = DST_ROOT / lang / fname
        seed = args.seed_base + 10000 + idx  # offset to differ from main batch seeds
        transcript = FALLBACK_TRANSCRIPTS[idx % len(FALLBACK_TRANSCRIPTS)]
        jobs.append((str(src), str(dst), seed, transcript))
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
