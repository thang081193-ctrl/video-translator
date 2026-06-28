#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""outro_detect.py — READ-ONLY brand-outro presence detector + end-frame montage.

For every deliverable mp4 across every pack x lang-folder x V1(_out)/V2(_out_v2):
  * full-population tally: "is the last frame a flat brand-outro CARD?" (presence assert,
    the INVERSE of how retrim_endcards misused the same scene/flat-frame idea), and
  * a stratified end-frame montage (per lang-folder sample) the human eyeballs before destroy.

This is a DETECTOR. It NEVER trims, re-encodes in place, or otherwise modifies any mp4 —
it only reads frames into throwaway jpgs under --out. The heuristic is adapted from the
proven last-frame logic in _vast_pull/score_trim_append.py (there it located the
content->card scene boundary; here we assert a flat card actually remains at EOF).

Counts are GENERIC: derived from whatever mp4 are on disk, never hardcoded to a batch.
Writes <out>/outro_report.json and <out>/montage_NN.jpg contact sheets.

Usage:
  outro_detect.py --jobs-root /workspace/jobs --out <dir> --packs p1,p2,... [--per-stratum 6]
"""
import argparse
import glob
import json
import os
import random
import subprocess
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

CARD_COV_THRESHOLD = 0.55  # top-3 quantized colors must cover >=55% of frame => flat card


def _ffprobe_duration(mp4):
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", mp4],
            capture_output=True, text=True,
        ).stdout.strip()
        return float(out)
    except Exception:
        return 0.0


def last_frame_jpg(mp4, out):
    """Grab a ~0.3s-before-EOF thumbnail to a jpg (read-only). Returns True if written."""
    dur = _ffprobe_duration(mp4)
    t = max(0.0, dur - 0.3)
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.2f}", "-i", mp4,
         "-frames:v", "1", "-vf", "scale=216:384", "-q:v", "4", out],
        capture_output=True,
    )
    return os.path.exists(out)


def tail_is_card(mp4):
    """Heuristic: sample the last frame at 64x64; if the top-3 quantized colors cover
    >= CARD_COV_THRESHOLD of the frame, a flat brand-outro card is present.
    Returns (is_card: bool, coverage: float). Best-effort, advisory for the human."""
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-sseof", "-3", "-i", mp4,
         "-vf", "scale=64:64,format=rgb24", "-frames:v", "1", "-f", "rawvideo", "-"],
        capture_output=True,
    )
    data = r.stdout
    if len(data) < 64 * 64 * 3:
        return (False, 0.0)
    px = [tuple(b // 32 for b in data[i:i + 3]) for i in range(0, 64 * 64 * 3, 3)]
    c = Counter(px)
    top3 = sum(n for _, n in c.most_common(3))
    cov = top3 / len(px) if px else 0.0
    return (cov >= CARD_COV_THRESHOLD, round(cov, 3))


def _collect_mp4s(base):
    mp4s = []
    for sub in ("_out_v2", "_out"):
        mp4s += [
            f for f in glob.glob(os.path.join(base, sub, "**", "*.mp4"), recursive=True)
            if not f.endswith((".tmp.mp4", ".bak"))
        ]
    return mp4s


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs-root", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--packs", required=True,
                    help="comma-separated pack names under --jobs-root")
    ap.add_argument("--per-stratum", type=int, default=6)
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args(argv)

    os.makedirs(a.out, exist_ok=True)
    report = {}
    thumbs = []

    for pack in [p.strip() for p in a.packs.split(",") if p.strip()]:
        base = os.path.join(a.jobs_root, pack)
        mp4s = _collect_mp4s(base)
        if not mp4s:
            report[pack] = {"total": 0, "note": "NO OUTPUT"}
            print(f"{pack}: NO OUTPUT", flush=True)
            continue

        # stratify by immediate parent folder (== lang folder) so every lang x V1/V2 is sampled
        by_lang = {}
        for f in mp4s:
            by_lang.setdefault(os.path.basename(os.path.dirname(f)), []).append(f)
        sample = []
        for _lang, fs in by_lang.items():
            random.shuffle(fs)
            sample += fs[:a.per_stratum]

        # full-population card tally (cheap) so an UNSAMPLED missing-outro file is still caught
        cards = 0
        nocard = []

        def probe(f):
            ok, cov = tail_is_card(f)
            return (f, ok, cov)

        with ThreadPoolExecutor(max_workers=max(1, a.workers)) as ex:
            futs = [ex.submit(probe, f) for f in mp4s]
            for fut in as_completed(futs):
                f, ok, _cov = fut.result()
                if ok:
                    cards += 1
                else:
                    nocard.append(os.path.relpath(f, base))

        # montage thumbs from the stratified sample (read-only frame grabs)
        for f in sample:
            j = os.path.join(a.out, pack + "__" + os.path.basename(f) + ".jpg")
            if last_frame_jpg(f, j):
                thumbs.append(j)

        report[pack] = {
            "total": len(mp4s),
            "tail_is_card": cards,
            "tail_is_card_frac": round(cards / len(mp4s), 3),
            "suspect_no_outro": sorted(nocard)[:50],
            "suspect_count": len(nocard),
        }
        print(
            f"{pack}: {cards}/{len(mp4s)} have a card-tail "
            f"({report[pack]['tail_is_card_frac'] * 100:.0f}%); "
            f"{len(nocard)} suspect-no-outro",
            flush=True,
        )

    # tile thumbs into contact-sheet jpgs (12 per sheet)
    for i in range(0, len(thumbs), 12):
        grp = thumbs[i:i + 12]
        if not grp:
            break
        sheet = os.path.join(a.out, f"montage_{i // 12:02d}.jpg")
        cols = min(4, len(grp))
        rows = (len(grp) + 3) // 4
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error"]
            + sum((["-i", t] for t in grp), [])
            + ["-filter_complex", f"tile={cols}x{rows}", sheet],
            capture_output=True,
        )

    report_path = os.path.join(a.out, "outro_report.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1)
    flagged = sum(r.get("suspect_count", 0) for r in report.values())
    print(f"\nOUTRO REPORT: {report_path} | total flagged-no-outro={flagged}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
