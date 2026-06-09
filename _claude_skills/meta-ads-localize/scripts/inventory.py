#!/usr/bin/env python3
"""ffprobe inventory of a batch folder: resolution / aspect / duration + junk detection.
Usage: python inventory.py --root "<batch folder>"
"""
import argparse
import json
import os
import subprocess
import sys
from collections import Counter

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def cls(ar):
    if ar is None:
        return "?"
    if abs(ar - 0.5625) < 0.02:
        return "9:16 OK"
    return "taller-than-9:16" if ar < 0.5625 else f"WIDE({ar})"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    a = ap.parse_args()

    rows, junk = [], []
    for dp, _, fs in os.walk(a.root):
        rel = os.path.relpath(dp, a.root)
        if any(s.startswith(("_", ".")) for s in rel.split(os.sep)):
            continue
        for f in sorted(fs):
            p = os.path.join(dp, f)
            if f == ".DS_Store" or ".part" in f or f.endswith(".temp.mp4") or f.lower().endswith(".m4a"):
                junk.append(p)
                continue
            if not f.lower().endswith(".mp4"):
                continue
            try:
                out = subprocess.run(
                    ["ffprobe", "-v", "error", "-select_streams", "v:0",
                     "-show_entries", "stream=width,height:format=duration", "-of", "json", p],
                    capture_output=True, text=True).stdout
                j = json.loads(out)
                st = j["streams"][0]
                w, h = st["width"], st["height"]
                dur = float(j.get("format", {}).get("duration", 0))
                ar = round(w / h, 3) if h else None
                rows.append((os.path.relpath(p, a.root), w, h, ar, round(dur, 1)))
            except Exception as e:
                rows.append((os.path.relpath(p, a.root), None, None, None, str(e)[:30]))

    cnt = Counter()
    for rel, w, h, ar, dur in rows:
        c = cls(ar)
        cnt[c] += 1
        wh = f"{w}x{h}" if w else "??"
        print(f"{wh:<11} ar={ar} {dur}s  {c:<16} {rel}")

    print("\n=== SUMMARY ===")
    print("total mp4:", len(rows))
    for k, v in cnt.most_common():
        print(f"  {k}: {v}")
    over = [r for r in rows if r[1] and r[1] > 1080]
    if over:
        print(f"  >1080px wide ({len(over)}) — downscale to 1080x1920 during dub/convert")
    print(f"\n=== JUNK / incomplete ({len(junk)}) ===")
    for j in junk:
        print("  ", os.path.relpath(j, a.root))


if __name__ == "__main__":
    main()
