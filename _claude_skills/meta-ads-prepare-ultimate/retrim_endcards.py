# -*- coding: utf-8 -*-
"""Remove the trailing pink end-card sequence (competitor 'Download Now' card +
the pipeline's auto-generated outro card) from finished outputs, by COLOR.

Pink/lavender card: bright>185, R-G>6, B-G>2  (calibrated: card R246 G232 B244 vs
content R99 G107 B69). Finds the trailing contiguous card run and trims to the
content end. Re-encodes only the kept portion (no Demucs) + 0.25s audio fade-out.

mode 'test' -> 6 sample videos to _retrim_test/ (no overwrite) + new last frames.
mode 'all'  -> every output, trimmed IN PLACE (atomic via temp).
"""
import os, subprocess, sys, tempfile, shutil, collections
from PIL import Image
from concurrent.futures import ThreadPoolExecutor, as_completed
CARD_THRESH = 0.55  # top-3 quantized-color coverage; card 0.71-0.78 vs content 0.20-0.41

# DST is resolved in __main__ from argv via argparse — NO destructive default here.
# (Footgun fix: a stray/empty arg like `--help` must never fall through to an
# in-place trim of some hard-coded folder.)
DST = None
TEST_SAMPLE_ROOT = r"D:\Dev\App Details\Plant Identifier\video\0506"  # only `test` mode samples
FPS = 4

def dur_of(v):
    r = subprocess.run(["ffprobe","-v","error","-show_entries","format=duration",
                        "-of","csv=p=0",v], capture_output=True, text=True)
    try: return float(r.stdout.strip())
    except Exception: return 0.0

def _dom3(im):
    q = [(r//28, g//28, b//28) for r, g, b in im.getdata()]
    c = collections.Counter(q); n = len(q)
    return sum(v for _, v in c.most_common(3)) / n

def is_card_frame(path):
    """A flat graphic end-card (competitor app card OR auto-generated outro):
    top-3 quantized-color coverage. Flat colored cards (green/blue/pink) score
    ~0.7-0.8; photographic content ~0.2-0.4. (Busy graphic-promo ads that are
    card-like throughout are intentionally NOT over-trimmed — the max-trim guard
    keeps their creative intact and only the trailing outro card is removed.)"""
    im = Image.open(path).convert("RGB").resize((90, 160))
    return _dom3(im) >= CARD_THRESH

def detect_content_end(v, dur):
    win = min(8.0, dur*0.65); start = max(0.0, dur-win)
    tmp = tempfile.mkdtemp()
    subprocess.run(["ffmpeg","-v","error","-ss",f"{start}","-i",v,
                    "-vf",f"fps={FPS},scale=90:160","-y",os.path.join(tmp,"f_%03d.png")],
                   capture_output=True)
    fr = sorted(os.listdir(tmp))
    cards = [is_card_frame(os.path.join(tmp,fn)) for fn in fr]
    shutil.rmtree(tmp, ignore_errors=True)
    if not cards or not cards[-1]: return None
    # walk back, bridging up to GAP non-card frames (transition between the
    # competitor end-card and the auto-generated outro card)
    GAP = 3
    i = len(cards)-1; run_start = i; gap = 0
    while i >= 0:
        if cards[i]:
            run_start = i; gap = 0
        else:
            gap += 1
            if gap > GAP: break
        i -= 1
    if (len(cards)-run_start)/FPS < 0.8: return None
    ce = start + run_start/FPS - 0.12
    if ce < 1.0 or (dur-ce) > dur*0.62: return None
    return round(ce, 2)

def trim_to(v, t, out):
    fs = max(0.0, t-0.25)
    subprocess.run(["ffmpeg","-v","error","-i",v,"-t",f"{t}",
                    "-c:v","libx264","-crf","20","-preset","fast","-pix_fmt","yuv420p",
                    "-c:a","aac","-b:a","128k","-af",f"afade=t=out:st={fs}:d=0.25",
                    "-movflags","+faststart","-y",out], capture_output=True)

def all_videos():
    out=[]
    for root,_,files in os.walk(DST):
        for f in files:
            if f.lower().endswith(".mp4"): out.append(os.path.join(root,f))
    return out

def process(v, replace, testdir=None):
    dur = dur_of(v)
    if dur < 2: return (v,"too-short",None,dur)
    ce = detect_content_end(v, dur)
    if ce is None: return (v,"NO-CARD",None,dur)
    if replace:
        tmp = v+".trim.mp4"; trim_to(v,ce,tmp)
        if os.path.getsize(tmp) > 50000:
            os.replace(tmp,v); return (v,"trimmed",ce,dur)
        os.remove(tmp); return (v,"FAIL-enc",ce,dur)
    else:
        bn = os.path.splitext(os.path.basename(v))[0]
        out = os.path.join(testdir, bn+".mp4"); trim_to(v,ce,out)
        # new last frame for verification
        nd = dur_of(out)
        subprocess.run(["ffmpeg","-v","error","-ss",f"{max(0,nd-0.15)}","-i",out,
                        "-frames:v","1","-y",os.path.join(testdir,bn+"_newlast.jpg")],capture_output=True)
        return (v,"trimmed",ce,dur)

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(prog="retrim_endcards.py",
        description="Trim the trailing pink end-card (competitor 'Download Now' + the "
                    "pipeline's auto outro card) from finished outputs, by colour.")
    ap.add_argument("mode", choices=["test", "all"],
        help="test = 6 sample clips -> _retrim_test/ (no overwrite); "
             "all = trim EVERY .mp4 under <dst> IN PLACE")
    ap.add_argument("dst", nargs="?", default=None,
        help="campaign-tree dir. REQUIRED for 'all' (in-place) — there is no default, so a "
             "stray arg like --help can never nuke a folder.")
    args = ap.parse_args()
    mode = args.mode
    if mode == "all" and not args.dst:
        ap.error("'all' trims IN PLACE — pass an explicit <dst> dir (refusing to use a default).")
    DST = args.dst or TEST_SAMPLE_ROOT
    if mode == "test":
        td = r"C:\Users\Thang Dep Dai\Downloads\_retrim_test"; os.makedirs(td, exist_ok=True)
        samples = ["English/EN_040601","Deutsch/DE_0506008","Español/ES_0506001",
                   "Français/FR_040602","العربية/AR_0506005","Italiano/IT_040603"]
        for s in samples:
            v = os.path.join(DST, s+".mp4")
            if not os.path.exists(v): print("MISS",s); continue
            r = process(v, replace=False, testdir=td)
            print(f"{os.path.basename(v):22} {r[1]:9} content_end={r[2]}  (was {r[3]:.2f}s, cut {r[3]-(r[2] or r[3]):.2f}s)")
        print(f"\ntrimmed test files + *_newlast.jpg -> {td}")
    else:
        vids = all_videos()
        print(f"processing {len(vids)} videos (replace in place)...", flush=True)
        res = {"trimmed":0,"NO-CARD":[], "FAIL-enc":[], "too-short":0}
        with ThreadPoolExecutor(max_workers=4) as ex:
            futs={ex.submit(process,v,True):v for v in vids}
            for k,fut in enumerate(as_completed(futs),1):
                v,st,ce,dur = fut.result()
                if st=="trimmed": res["trimmed"]+=1
                elif st=="too-short": res["too-short"]+=1
                else: res.setdefault(st,[]).append(os.path.basename(v))
                if k%40==0: print(f"  {k}/{len(vids)}...", flush=True)
        print(f"\ntrimmed: {res['trimmed']}  too-short: {res['too-short']}")
        print(f"NO-CARD (left as-is, review): {len(res['NO-CARD'])}")
        for n in res["NO-CARD"][:30]: print("   ", n)
        if res["FAIL-enc"]: print("FAIL-enc:", res["FAIL-enc"])
