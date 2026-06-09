#!/usr/bin/env python3
"""Make 4-up frame grids (frames at 8/35/65/92%) for vision classification.
Writes to $TEMP/<batch>_grids/ ; grid filename = <relpath-with-__>.jpg
Usage: python make_grids.py --root "<batch folder>" [--out <dir>]
Then hand the grid paths to vision subagents to tag {angle, lang}.
"""
import argparse
import os
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    batch = os.path.basename(os.path.normpath(a.root)).replace(" ", "_")
    out = a.out or os.path.join(os.environ.get("TEMP", "/tmp"), batch + "_grids")
    os.makedirs(out, exist_ok=True)

    vids = []
    for dp, _, fs in os.walk(a.root):
        rel = os.path.relpath(dp, a.root)
        if any(s.startswith(("_", ".")) for s in rel.split(os.sep)):
            continue
        for f in sorted(fs):
            if f.lower().endswith(".mp4") and not f.endswith(".temp.mp4") and ".part" not in f:
                vids.append(os.path.join(dp, f))

    made = 0
    for v in vids:
        tag = os.path.relpath(v, a.root).replace(os.sep, "__")[:-4]
        grid = os.path.join(out, tag + ".jpg")
        if os.path.exists(grid):
            continue
        dur = float(subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", v],
            capture_output=True, text=True).stdout.strip() or 10)
        seeks = [max(0.5, min(p * dur, dur - 0.5)) for p in (0.08, 0.35, 0.65, 0.92)]
        inp = []
        for s in seeks:
            inp += ["-ss", f"{s:.2f}", "-i", v]
        fc = ("[0:v]select=eq(n\\,0),scale=320:-2,setsar=1[a];"
              "[1:v]select=eq(n\\,0),scale=320:-2,setsar=1[b];"
              "[2:v]select=eq(n\\,0),scale=320:-2,setsar=1[c];"
              "[3:v]select=eq(n\\,0),scale=320:-2,setsar=1[d];"
              "[a][b]hstack=inputs=2[ab];[c][d]hstack=inputs=2[cd];[ab][cd]vstack=inputs=2[out]")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error"] + inp +
                       ["-filter_complex", fc, "-map", "[out]", "-frames:v", "1", "-q:v", "3", grid],
                       stderr=subprocess.DEVNULL)
        made += 1
    print(f"grids: {made} made, {len(vids)} videos total -> {out}")


if __name__ == "__main__":
    main()
