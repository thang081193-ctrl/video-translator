# -*- coding: utf-8 -*-
"""Append an outro to every file in a LIST (in place, atomic). Re-encodes via
concat filter so content+outro unify (fps/SAR/pix_fmt/audio). Idempotent via a
done-log: already-done files are skipped, so it is safe to re-run/resume.
Usage: python append_outro_list.py <list.txt> <outro.mp4> [done.txt] [workers]"""
import os, sys, subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed

LIST = sys.argv[1]
OUTRO = sys.argv[2]
DONE = sys.argv[3] if len(sys.argv) > 3 else LIST + ".done"
WORKERS = int(sys.argv[4]) if len(sys.argv) > 4 else 8

files = [l.strip() for l in open(LIST, encoding="utf-8") if l.strip()]
done = set()
if os.path.exists(DONE):
    done = set(l.strip() for l in open(DONE, encoding="utf-8") if l.strip())
todo = [f for f in files if f not in done and os.path.exists(f)]

def one(v):
    tmp = v + ".outro.tmp.mp4"
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", v, "-i", OUTRO, "-filter_complex",
           "[0:v]fps=30,scale=1080:1920,setsar=1,format=yuv420p[v0];"
           "[1:v]fps=30,scale=1080:1920,setsar=1,format=yuv420p[v1];"
           "[v0][0:a][v1][1:a]concat=n=2:v=1:a=1[v][a]",
           "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-crf", "19",
           "-preset", "veryfast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
           "-movflags", "+faststart", tmp]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode == 0 and os.path.exists(tmp) and os.path.getsize(tmp) > 50000:
        os.replace(tmp, v)
        return (v, "ok")
    if os.path.exists(tmp):
        try: os.remove(tmp)
        except OSError: pass
    return (v, "FAIL " + (r.stderr or "")[-200:])

if not os.path.isfile(OUTRO):
    sys.exit("outro not found: " + OUTRO)
print(f"append: {len(todo)} todo ({len(done)} already done) | outro={os.path.basename(OUTRO)} | workers={WORKERS}", flush=True)
ok = 0; fails = []
with ThreadPoolExecutor(max_workers=WORKERS) as ex, open(DONE, "a", encoding="utf-8") as dl:
    futs = {ex.submit(one, v): v for v in todo}
    for k, fut in enumerate(as_completed(futs), 1):
        v, st = fut.result()
        if st == "ok":
            ok += 1; dl.write(v + "\n"); dl.flush()
        else:
            fails.append((v, st))
        if k % 40 == 0 or k == len(todo):
            print(f"  {k}/{len(todo)} ok={ok} fail={len(fails)}", flush=True)
print(f"APPEND DONE {ok}/{len(todo)} | fails={len(fails)}", flush=True)
for v, st in fails[:10]:
    print("FAIL", os.path.basename(v), st)
