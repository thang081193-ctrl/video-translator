# -*- coding: utf-8 -*-
"""ScoreDeck remediation: trim trailing competitor end-card (capped to last 7s) +
append the ScoreDeck outro, in place, atomic. Idempotent via done-log.
Heuristic: the content->competitor-endcard boundary is the EARLIEST hard scene cut
(scene>0.35) within the last 7s. If none -> file ends on content, no trim (just
append outro). Caps trim at 7s so long skits are never deep-cut.
Usage: python score_trim_append.py <list.txt> <outro.mp4> [done.txt] [workers]"""
import os, sys, subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed

LIST = sys.argv[1]
OUTRO = sys.argv[2]
DONE = sys.argv[3] if len(sys.argv) > 3 else LIST + ".done"
WORKERS = int(sys.argv[4]) if len(sys.argv) > 4 else 8

def dur(f):
    try: return float(subprocess.run(["ffprobe","-v","error","-show_entries","format=duration","-of","csv=p=0",f],capture_output=True,text=True).stdout.strip())
    except: return 0.0

def cuts(f, a, b):
    r = subprocess.run(["ffmpeg","-v","error","-ss",f"{a:.2f}","-t",f"{b-a:.2f}","-i",f,
        "-vf","select='gt(scene,0.35)',metadata=print:file=-","-an","-f","null","-"],
        capture_output=True, text=True)
    ts = []
    for ln in (r.stdout + r.stderr).splitlines():
        if "pts_time" in ln:
            try: ts.append(a + float(ln.split("pts_time:")[1].split()[0]))
            except: pass
    return ts

def trim_point(f):
    d = dur(f)
    if d < 5: return d, d, False
    lo = max(1.0, d - 7.0); hi = d - 1.2
    sc = cuts(f, lo, hi)
    if sc: return min(sc), d, True
    return d, d, False

def one(v):
    tp, d, trimmed = trim_point(v)
    tmp = v + ".sc.tmp.mp4"
    inp = ["-t", f"{tp:.2f}", "-i", v] if trimmed else ["-i", v]
    cmd = ["ffmpeg","-y","-v","error"] + inp + ["-i", OUTRO, "-filter_complex",
        "[0:v]fps=30,scale=1080:1920,setsar=1,format=yuv420p[v0];"
        "[1:v]fps=30,scale=1080:1920,setsar=1,format=yuv420p[v1];"
        "[v0][0:a][v1][1:a]concat=n=2:v=1:a=1[v][a]",
        "-map","[v]","-map","[a]","-c:v","libx264","-crf","20","-preset","veryfast",
        "-pix_fmt","yuv420p","-c:a","aac","-b:a","128k","-movflags","+faststart", tmp]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode == 0 and os.path.exists(tmp) and os.path.getsize(tmp) > 50000:
        os.replace(tmp, v)
        return (v, "ok", f"trim@{tp:.1f}/{d:.1f}" if trimmed else f"notrim/{d:.1f}")
    if os.path.exists(tmp):
        try: os.remove(tmp)
        except OSError: pass
    return (v, "FAIL", (r.stderr or "")[-160:])

if not os.path.isfile(OUTRO):
    sys.exit("outro not found: " + OUTRO)
files = [l.strip() for l in open(LIST, encoding="utf-8") if l.strip()]
done = set(l.strip() for l in open(DONE, encoding="utf-8")) if os.path.exists(DONE) else set()
todo = [f for f in files if f not in done and os.path.exists(f)]
print(f"score patch: {len(todo)} todo ({len(done)} done) | workers={WORKERS}", flush=True)
ok = 0; fails = []; trimmed_n = 0
with ThreadPoolExecutor(max_workers=WORKERS) as ex, open(DONE, "a", encoding="utf-8") as dl:
    futs = {ex.submit(one, v): v for v in todo}
    for k, fut in enumerate(as_completed(futs), 1):
        v, st, info = fut.result()
        if st == "ok":
            ok += 1; dl.write(v + "\n"); dl.flush()
            if info.startswith("trim"): trimmed_n += 1
        else:
            fails.append((v, info))
        if k % 40 == 0 or k == len(todo):
            print(f"  {k}/{len(todo)} ok={ok} trimmed={trimmed_n} fail={len(fails)}", flush=True)
print(f"SCORE PATCH DONE {ok}/{len(todo)} | trimmed={trimmed_n} notrim={ok-trimmed_n} | fails={len(fails)}", flush=True)
for v, info in fails[:10]:
    print("FAIL", os.path.basename(v), info)
