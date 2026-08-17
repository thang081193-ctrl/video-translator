"""Generate a self-contained HTML box picker for a logo-swap batch.

Authoring geometry is the one step in this pipeline that genuinely needs a
human. Five automatic proposers were tried on this batch -- saturated-blob,
temporal-stability, frozen-pixel voting, full-frame template match, and
hint-restricted template match -- and each failed differently: they lock onto
the Google Play badge, the blurred background, or the inner glyph instead of
the whole tile. They are useful as PRE-FILLS and useless as decisions.

An OpenCV picker is not an option here: requirements.txt pins
opencv-python-headless, which is built with GUI: NONE, so cv2.namedWindow
raises. This emits a plain HTML file instead -- open it in any browser, drag
boxes, press Copy. No install, no server, works over a file:// URL.

    python scripts/logoswap_pick.py --src <batch dir> [--out picker.html]

Two frames per clip: an early one (the start-of-clip competitor logo card) and
a mid one (the app icon on the phone the actor holds). Draw on whichever
applies, then paste the JSON back.
"""

from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path

import cv2

MAX_W = 520          # on-screen width cap; the readout stays in source pixels


def _frame_b64(path: Path, t: float, quality: int = 88) -> tuple[str, int, int]:
    cap = cv2.VideoCapture(str(path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 25
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(t * fps))
    ok, frame = cap.read()
    cap.release()
    if not ok:
        raise RuntimeError(f"cannot read {path} at t={t}")
    h, w = frame.shape[:2]
    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return base64.b64encode(buf).decode("ascii"), w, h


PAGE = """<!doctype html><meta charset="utf-8">
<title>logo-swap box picker</title>
<style>
 body{font:13px/1.45 system-ui,sans-serif;margin:0;background:#111;color:#ddd}
 header{position:sticky;top:0;background:#181818;border-bottom:1px solid #333;
        padding:10px 14px;z-index:10;display:flex;gap:12px;align-items:center;flex-wrap:wrap}
 button{font:12px system-ui;padding:6px 12px;border-radius:5px;border:1px solid #444;
        background:#252525;color:#ddd;cursor:pointer}
 button:hover{background:#303030} button.on{background:#2d5aa0;border-color:#4a7fd0}
 .clip{border-bottom:1px solid #2a2a2a;padding:14px}
 .clip h3{margin:0 0 8px;font-size:14px;color:#7fd07f;font-weight:600}
 .panes{display:flex;gap:16px;flex-wrap:wrap}
 .pane{position:relative;display:inline-block}
 .pane canvas{position:absolute;left:0;top:0;cursor:crosshair}
 .pane .cap{font-size:11px;color:#999;margin-bottom:3px}
 .out{font:11px ui-monospace,monospace;color:#8fc;white-space:pre;margin-top:6px}
 #json{width:100%;height:170px;background:#0c0c0c;color:#9f9;border:1px solid #333;
       font:11px ui-monospace,monospace;padding:8px;box-sizing:border-box}
</style>
<header>
  <b>logo-swap box picker</b>
  <span>Mode:</span>
  <button id="mT1" class="on">T1 &mdash; icon on the phone</button>
  <button id="mS1">S1 &mdash; static card / name pill</button>
  <button id="mXX">X &mdash; wipe only (competitor TEXT)</button>
  <button id="clr">Clear this clip</button>
  <button id="cp">Copy JSON</button>
  <span style="color:#888">drag a box &middot; drag again to replace &middot;
        cover the WHOLE mark incl. its tile &middot;
        X = paint over, no logo drawn</span>
</header>
<div id="clips"></div>
<div style="padding:14px"><b>Result</b><textarea id="json" readonly></textarea></div>
<script>
const DATA = __DATA__;
const boxes = {};
let mode = "t1";
const $ = s => document.querySelector(s);

function dump(){
  const o = {};
  for (const k in boxes) if (Object.keys(boxes[k]).length) o[k] = boxes[k];
  $("#json").value = JSON.stringify(o, null, 1);
}

function pane(clip, kind, src, w, h, t, scale){
  const wrap = document.createElement("div"); wrap.className = "pane";
  const cap = document.createElement("div"); cap.className = "cap";
  cap.textContent = `${kind === "s1" ? "early" : "mid"}  t=${t}s   ${w}x${h}`;
  const img = document.createElement("img");
  img.src = "data:image/jpeg;base64," + src;
  img.style.width = (w*scale) + "px"; img.style.height = (h*scale) + "px";
  img.draggable = false;
  const cv = document.createElement("canvas");
  cv.width = w*scale; cv.height = h*scale;
  cv.style.width = (w*scale)+"px"; cv.style.height = (h*scale)+"px";
  const out = document.createElement("div"); out.className = "out";
  const holder = document.createElement("div");
  holder.style.position="relative"; holder.style.width=(w*scale)+"px";
  holder.style.height=(h*scale)+"px";
  holder.appendChild(img); holder.appendChild(cv);
  wrap.appendChild(cap); wrap.appendChild(holder); wrap.appendChild(out);

  const ctx = cv.getContext("2d");
  function redraw(){
    ctx.clearRect(0,0,cv.width,cv.height);
    const flat = [];
    for (const [k,b] of Object.entries(boxes[clip]||{})){
      if (k.endsWith("_end")) continue;
      if (k === "x") b.forEach((bb,i)=>flat.push(["x"+(i+1), bb]));
      else flat.push([k, b]);
    }
    for (const [k,b] of flat){
      ctx.strokeStyle = k==="t1" ? "#ff3b3b" : (k==="s1" ? "#3bff6a" : "#ffd23b");
      ctx.lineWidth = 2;
      ctx.strokeRect(b[0]*scale, b[1]*scale, b[2]*scale, b[3]*scale);
      ctx.fillStyle = ctx.strokeStyle; ctx.font="11px monospace";
      ctx.fillText(k.toUpperCase()+" "+b.join(","), b[0]*scale+2, b[1]*scale-3);
    }
    out.textContent = flat.map(([k,b])=>`${k}: ${b.join(", ")}`).join("\\n")
      || "(none)";
  }
  let sx=0, sy=0, drag=false;
  cv.addEventListener("mousedown", e=>{
    const r=cv.getBoundingClientRect(); sx=e.clientX-r.left; sy=e.clientY-r.top; drag=true;
  });
  cv.addEventListener("mousemove", e=>{
    if(!drag) return;
    const r=cv.getBoundingClientRect(), x=e.clientX-r.left, y=e.clientY-r.top;
    redraw();
    ctx.strokeStyle = mode==="t1" ? "#ff3b3b" : (mode==="s1" ? "#3bff6a" : "#ffd23b");
    ctx.lineWidth=2;
    ctx.strokeRect(Math.min(sx,x),Math.min(sy,y),Math.abs(x-sx),Math.abs(y-sy));
  });
  cv.addEventListener("mouseup", e=>{
    if(!drag) return; drag=false;
    const r=cv.getBoundingClientRect(), x=e.clientX-r.left, y=e.clientY-r.top;
    const bx=Math.round(Math.min(sx,x)/scale), by=Math.round(Math.min(sy,y)/scale);
    const bw=Math.round(Math.abs(x-sx)/scale), bh=Math.round(Math.abs(y-sy)/scale);
    if (bw<5||bh<5){ redraw(); return; }
    boxes[clip] = boxes[clip] || {};
    if (mode === "x"){
      boxes[clip].x = boxes[clip].x || [];
      boxes[clip].x.push([bx,by,bw,bh]);
    } else {
      boxes[clip][mode] = [bx,by,bw,bh];
    }
    redraw(); dump();
  });
  wrap._redraw = redraw;
  return wrap;
}

const host = $("#clips");
for (const c of DATA){
  boxes[c.id] = {};
  if (c.pre_t1) boxes[c.id].t1 = c.pre_t1;
  if (c.pre_s1) boxes[c.id].s1 = c.pre_s1;
  const d = document.createElement("div"); d.className="clip";
  const h3 = document.createElement("h3");
  h3.textContent = `#${c.id}   ${c.w}x${c.h}   keeps ${c.keep}s`
                 + (c.pre_t1 ? "   (T1 pre-filled - adjust or redraw)" : "");
  d.appendChild(h3);
  const panes = document.createElement("div"); panes.className="panes";
  const scale = Math.min(2.0, __MAXW__ / c.w);
  const p1 = pane(c.id,"s1",c.early,c.w,c.h,c.t_early,scale);
  const p2 = pane(c.id,"t1",c.mid,c.w,c.h,c.t_mid,scale);
  panes.appendChild(p1); panes.appendChild(p2);
  d.appendChild(panes); host.appendChild(d);
  p1._redraw(); p2._redraw();
}
dump();
$("#mT1").onclick=()=>setMode("t1","#mT1");
function setMode(m,btn){mode=m;
  for (const b of ["#mT1","#mS1","#mXX"]) $(b).classList.remove("on");
  $(btn).classList.add("on");}
$("#mS1").onclick=()=>setMode("s1","#mS1");
$("#mXX").onclick=()=>setMode("x","#mXX");
$("#clr").onclick=()=>{for(const k in boxes) boxes[k]={};
  document.querySelectorAll(".pane").forEach(p=>p._redraw&&p._redraw()); dump();};
$("#cp").onclick=()=>{$("#json").select();document.execCommand("copy");
  $("#cp").textContent="Copied";setTimeout(()=>$("#cp").textContent="Copy JSON",1200)};
</script>
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src", required=True)
    ap.add_argument("--out", default=None)
    ap.add_argument("--only", default="", help="comma-separated clip ids")
    ap.add_argument("--times", default="",
                    help="explicit frame times per clip, e.g. '1:5,26;13:23,40'. "
                         "Needed when the thing to cover only appears mid-clip -- "
                         "the default early/mid pair cannot show it.")
    args = ap.parse_args()

    src = Path(args.src)
    batch = src / "_ytbatch"
    cls = json.loads((batch / "classification.json").read_text(encoding="utf-8"))
    cuts = json.loads((batch / "endcard_cuts.json").read_text())["cuts"]
    pre_t1, pre_s1 = {}, {}
    for name, target in (("icon_match.json", pre_t1), ("static_cards.json", pre_s1)):
        p = batch / name
        if p.exists():
            for k, v in json.loads(p.read_text()).items():
                if isinstance(v, dict) and v.get("box"):
                    target[k] = v["box"]

    only = {s.strip() for s in args.only.split(",") if s.strip()}
    times: dict[str, list[float]] = {}
    for part in args.times.split(";"):
        if ":" not in part:
            continue
        cid, ts = part.split(":", 1)
        times[cid.strip()] = [float(t) for t in ts.split(",") if t.strip()]
    data = []
    for v in cls["videos"]:
        if not v.get("ship"):
            continue
        cid = v["id"]
        if only and cid not in only:
            continue
        mp4 = src / v["file"]
        keep = float(cuts.get(cid, v["duration"]))
        t_a, t_b = times.get(cid, [min(1.6, keep * 0.1), keep * 0.45])[:2]
        early, w, h = _frame_b64(mp4, t_a)
        mid, _, _ = _frame_b64(mp4, t_b)
        data.append({"id": cid, "w": w, "h": h, "keep": round(keep, 2),
                     "t_early": round(t_a, 2), "t_mid": round(t_b, 2),
                     "early": early, "mid": mid,
                     "pre_t1": pre_t1.get(cid), "pre_s1": pre_s1.get(cid)})
        print(f"  #{cid} {w}x{h}")

    out = Path(args.out) if args.out else batch / "box_picker.html"
    html = PAGE.replace("__DATA__", json.dumps(data)).replace("__MAXW__", str(MAX_W))
    out.write_text(html, encoding="utf-8")
    mb = out.stat().st_size / 1e6
    print(f"\nwrote {out}  ({mb:.1f} MB, {len(data)} clips)")
    print("Open it in a browser, drag boxes, press Copy JSON.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
