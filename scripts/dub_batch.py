#!/usr/bin/env python3
"""Batch-dub <ANGLE>/EN/*.mp4 -> FR+TR (1080x1920 CRF), sharing Demucs+Whisper
per source across langs. Optionally downscale the EN originals in place.

  python scripts/dub_batch.py --en-dir ".../STORAGE_VO/EN" --out-root ".../STORAGE_VO" \
      --langs fr,tr --crf 23 --downscale-en
"""
import argparse
import glob
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)


def main():
    from dotenv import load_dotenv
    load_dotenv()
    from pipeline.logger import setup_logging
    setup_logging(mode="cli")
    from dub_one import prepare_source, dub_lang

    ap = argparse.ArgumentParser()
    ap.add_argument("--en-dir", required=True, help="folder of <ANGLE>_EN_*.mp4")
    ap.add_argument("--out-root", required=True, help="parent for <LANG>/ sibling folders")
    ap.add_argument("--langs", default="fr,tr")
    ap.add_argument("--whisper", default="small")
    ap.add_argument("--crf", type=int, default=23)
    ap.add_argument("--scale", default="1080:1920")
    ap.add_argument("--downscale-en", action="store_true",
                    help="also downscale EN originals to --scale in place")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()

    langs = [x.strip() for x in a.langs.split(",") if x.strip()]
    srcs = sorted(glob.glob(os.path.join(a.en_dir, "*.mp4")))
    if a.limit:
        srcs = srcs[:a.limit]
    print(f"{len(srcs)} sources x {len(langs)} langs = {len(srcs)*len(langs)} dubs "
          f"(crf{a.crf} scale {a.scale})", flush=True)

    done = skip = fail = 0
    t0 = time.time()
    for i, src in enumerate(srcs, 1):
        stem = os.path.splitext(os.path.basename(src))[0]      # STORAGE_VO_EN_280501
        angle, _, dateseq = stem.rsplit("_", 2)
        outs = {l: os.path.join(a.out_root, l.upper(), f"{angle}_{l.upper()}_{dateseq}.mp4")
                for l in langs}
        todo = [l for l in langs
                if not (os.path.exists(outs[l]) and os.path.getsize(outs[l]) > 100 * 1024)]
        if not todo:
            print(f"[{i}/{len(srcs)}] {stem}: all exist, skip", flush=True)
            skip += len(langs)
            continue

        work = tempfile.mkdtemp(prefix="dubsrc_")
        try:
            print(f"[{i}/{len(srcs)}] {stem}: prepare (Demucs+Whisper)...", flush=True)
            no_vocals, segs, det = prepare_source(src, work, a.whisper, "en")
            if not segs:
                print("   NO SPEECH -> skip all langs", flush=True)
                skip += len(langs)
                continue
            for lang in todo:
                try:
                    print(f"   -> {lang}", flush=True)
                    dub_lang(src, segs, no_vocals, lang, outs[lang], work, a.crf, a.scale)
                    done += 1
                except Exception as e:
                    fail += 1
                    print(f"   FAIL {lang}: {e}", flush=True)
        except Exception as e:
            fail += len(todo)
            print(f"   FAIL prepare: {e}", flush=True)
        finally:
            shutil.rmtree(work, ignore_errors=True)

    if a.downscale_en:
        print(f"\ndownscaling EN originals -> {a.scale} ...", flush=True)
        for src in srcs:
            tmp = src + ".1080.mp4"
            try:
                subprocess.run(
                    ["ffmpeg", "-y", "-loglevel", "error", "-i", src, "-vf", f"scale={a.scale}",
                     "-c:v", "libx264", "-crf", str(a.crf), "-preset", "fast",
                     "-pix_fmt", "yuv420p", "-c:a", "copy", tmp], check=True)
                os.replace(tmp, src)
                print(f"   EN: {os.path.basename(src)}", flush=True)
            except Exception as e:
                print(f"   EN FAIL {os.path.basename(src)}: {e}", flush=True)
                if os.path.exists(tmp):
                    os.remove(tmp)

    print(f"\n=== DONE: {done} dubbed, {skip} skipped, {fail} failed "
          f"in {(time.time()-t0)/60:.1f} min ===", flush=True)


if __name__ == "__main__":
    main()
