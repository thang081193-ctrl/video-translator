#!/usr/bin/env python
"""One-pass QA sweep for an ad-video pack - the slow parts of a brandpass batch, batched.

Two phases, matching the natural break in the workflow:

    qa_sweep.py scan   --src <sources>            # BEFORE render: probe, twins, padding, tail sheets
    qa_sweep.py fine   --src <sources> --windows  # zoom on one end-card window to pick the exact cut
    qa_sweep.py verify --out <renders> ...        # AFTER render: geometry, padding, audio, output sheets

Why it exists (measured on the Mars VD 0809 pack: 59 min wall clock, 6.5 min of it render):

  * Every contact sheet is emitted as a MONTAGE of N clips, so reviewing a 41-clip batch is
    ~7 image reads instead of ~41. That was the single biggest time sink.
  * `scan` looks for baked letterbox in the SOURCES, so a clip whose picture sits inside black
    bars is caught before it is rendered, not after (brand_pass upscales the bars - its own
    padding detector is opt-in and off by default, deliberately).
  * Audio is measured on decoded PCM, never `volumedetect`, which reported -19 dB on a segment
    that was digital silence and cost ~6 min of chasing a bug that did not exist.
  * `verify` names the few clips that actually need a bed-muted control render to prove the
    source audio was untouched, instead of re-rendering the whole batch for it.
  * Duplicates are REPORTED, never dropped - one output per source file is the operator's rule.

All ffmpeg work is threaded (ffmpeg is a subprocess, so threads are the right tool).
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

FF = ["ffmpeg", "-nostdin", "-v", "error"]
SHEET_H = 250          # per-frame height inside a strip
STRIP_W = 1400         # every strip is scaled to this before stacking, so vstack lines up


# ---------------------------------------------------------------- helpers

def _run(cmd: list[str]) -> bytes:
    return subprocess.run(cmd, capture_output=True).stdout


def probe(p: Path) -> dict:
    """Coded dims, SAR, rotation-corrected DISPLAY dims, fps, duration, audio."""
    j = json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(p)],
        capture_output=True, text=True, encoding="utf-8", errors="replace").stdout or "{}")
    if not j:
        return {"name": p.name, "error": "probe failed"}
    v = next(s for s in j["streams"] if s["codec_type"] == "video")
    a = next((s for s in j["streams"] if s["codec_type"] == "audio"), None)
    w, h = int(v["width"]), int(v["height"])
    sar = v.get("sample_aspect_ratio") or "1:1"
    if sar in ("N/A", "0:1", "0:0"):
        sar = "1:1"
    sn, sd = (int(x) for x in sar.split(":"))
    if sn == 0 or sd == 0:
        sn = sd = 1
    rot = 0
    for item in v.get("side_data_list", []) or []:
        if "rotation" in item:
            rot = int(item["rotation"]) % 360
    if rot in (90, 270):
        w, h = h, w
        sn, sd = sd, sn
    dw, dh = w * sn // sd, h
    num, den = (v.get("r_frame_rate") or "30/1").split("/")
    fps = round(int(num) / max(int(den), 1), 3)
    return {
        "name": p.name, "path": str(p), "size_mb": round(p.stat().st_size / 1e6, 2),
        "coded": f'{v["width"]}x{v["height"]}', "sar": sar, "rot": rot,
        "display": f"{dw}x{dh}", "disp_ratio": round(dw / dh, 4),
        "is_9_16": abs(dw / dh - 9 / 16) <= 0.05 * (9 / 16),
        "pix_fmt": v.get("pix_fmt"), "fps": fps,
        "dur": round(float(j["format"].get("duration") or 0), 3),
        "audio": (a or {}).get("codec_name"),
        "vbitrate": int(v.get("bit_rate") or 0) // 1000,
    }


def pcm(p: Path, ss: float | None = None, dur: float | None = None, sr: int = 16000) -> np.ndarray:
    """Decoded mono float32. The ONLY way this tool measures level — never volumedetect."""
    cmd = list(FF)
    if ss is not None:
        cmd += ["-ss", f"{ss:.3f}"]
    if dur is not None:
        cmd += ["-t", f"{dur:.3f}"]
    cmd += ["-i", str(p), "-map", "a:0", "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"]
    return np.frombuffer(_run(cmd), np.float32).astype(np.float64)


def db(a: np.ndarray) -> float:
    return 20 * np.log10(np.sqrt((a ** 2).mean()) + 1e-12) if a.size else -240.0


def md5(p: Path) -> str:
    h = hashlib.md5()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def gray(p: Path, t: float, w: int, h: int) -> np.ndarray | None:
    raw = _run(FF + ["-ss", f"{t:.3f}", "-i", str(p), "-frames:v", "1",
                     "-vf", f"scale={w}:{h}:flags=area,format=gray", "-f", "rawvideo", "-"])
    return np.frombuffer(raw, np.uint8).astype(np.float32) if len(raw) == w * h else None


# ---------------------------------------------------------------- sheets

def _strip(src: Path, times: list[float], dst: Path, label: bool = True) -> bool:
    """One clip -> one horizontal strip of labelled frames."""
    tmp = dst.parent / f"_f_{dst.stem}"
    tmp.mkdir(parents=True, exist_ok=True)
    frames = []
    for i, t in enumerate(times):
        f = tmp / f"{i:02d}.png"
        vf = f"scale=-2:{SHEET_H}"
        if label:
            vf += (f",drawtext=text='{t:.2f}':x=5:y=4:fontsize=22:fontcolor=yellow"
                   f":box=1:boxcolor=black@0.75")
        subprocess.run(FF + ["-y", "-ss", f"{t:.3f}", "-i", str(src), "-frames:v", "1",
                             "-vf", vf, str(f)], capture_output=True)
        if f.is_file():
            frames.append(f)
    ok = False
    if frames:
        cmd = list(FF) + ["-y"]
        for f in frames:
            cmd += ["-i", str(f)]
        cmd += ["-filter_complex", f"hstack=inputs={len(frames)}", str(dst)]
        subprocess.run(cmd, capture_output=True)
        ok = dst.is_file()
    for f in frames:
        f.unlink(missing_ok=True)
    tmp.rmdir()
    return ok


def montage(strips: list[Path], dst: Path) -> bool:
    """Stack per-clip strips into ONE sheet. This is the whole point of the tool."""
    strips = [s for s in strips if s.is_file()]
    if not strips:
        return False
    cmd = list(FF) + ["-y"]
    for s in strips:
        cmd += ["-i", str(s)]
    fc = "".join(f"[{i}:v]scale={STRIP_W}:-2[s{i}];" for i in range(len(strips)))
    fc += "".join(f"[s{i}]" for i in range(len(strips))) + f"vstack=inputs={len(strips)}"
    cmd += ["-filter_complex", fc, str(dst)]
    subprocess.run(cmd, capture_output=True)
    return dst.is_file()


def build_sheets(items: list[tuple[Path, list[float]]], outdir: Path, prefix: str,
                 per_sheet: int, workers: int) -> list[Path]:
    outdir.mkdir(parents=True, exist_ok=True)
    tmpdir = outdir / "_strips"
    tmpdir.mkdir(exist_ok=True)
    strips: list[Path] = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = []
        for src, times in items:
            dst = tmpdir / f"{src.stem}.png"
            strips.append(dst)
            futs.append(ex.submit(_strip, src, times, dst))
        for _ in futs:
            _.result()
    sheets = []
    for i in range(0, len(strips), per_sheet):
        chunk = strips[i:i + per_sheet]
        dst = outdir / f"{prefix}_{i // per_sheet + 1:02d}.png"
        if montage(chunk, dst):
            sheets.append(dst)
            names = ", ".join(c.stem for c in chunk)
            print(f"  {dst.name}: {names}")
    for s in strips:
        s.unlink(missing_ok=True)
    tmpdir.rmdir()
    return sheets


# ---------------------------------------------------------------- padding

def padding_report(p: Path, n: int = 7, skip_tail: float = 0.0) -> dict:
    """Edge bands that are near-uniform DARK or near-zero-detail (blur pad).

    Both directions matter: a competitor's black letterbox and a blurred pillarbox both
    waste canvas, and neither shows up in a dims check. Known false positive: a dark
    intro/outro card reads as padding, so always confirm a hit by eye.
    """
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                "-of", "csv=p=0", str(p)],
                               capture_output=True, text=True).stdout or 0)
    body = max(dur - skip_tail, 1.0)
    W, H = 120, 212
    acc = None
    for i in range(n):
        t = body * (i + 0.5) / n
        g = gray(p, t, W, H)
        if g is None:
            continue
        acc = g.reshape(H, W) if acc is None else acc + g.reshape(H, W)
    if acc is None:
        return {"error": "no frames"}
    m = acc / n
    # dark bands
    rows, cols = m.mean(axis=1), m.mean(axis=0)
    rstd, cstd = m.std(axis=1), m.std(axis=0)
    # detail bands (cheap Laplacian-ish: second difference magnitude)
    d_rows = np.zeros(H)
    d_rows[1:-1] = np.abs(m[2:, :] - 2 * m[1:-1, :] + m[:-2, :]).mean(axis=1)
    d_cols = np.zeros(W)
    d_cols[1:-1] = np.abs(m[:, 2:] - 2 * m[:, 1:-1] + m[:, :-2]).mean(axis=0)
    d_rows /= d_rows.max() + 1e-9
    d_cols /= d_cols.max() + 1e-9

    def run_len(vals, stds, rev=False, dark=True, detail=None):
        idx = range(len(vals) - 1, -1, -1) if rev else range(len(vals))
        k = 0
        for i in idx:
            hit = (vals[i] < 26 and stds[i] < 12) if dark else (detail[i] < 0.08)
            if hit:
                k += 1
            else:
                break
        return k / len(vals) * 100

    return {
        "dark_top": round(run_len(rows, rstd), 1), "dark_bot": round(run_len(rows, rstd, True), 1),
        "dark_left": round(run_len(cols, cstd), 1), "dark_right": round(run_len(cols, cstd, True), 1),
        "flat_top": round(run_len(rows, rstd, False, False, d_rows), 1),
        "flat_bot": round(run_len(rows, rstd, True, False, d_rows), 1),
        "flat_left": round(run_len(cols, cstd, False, False, d_cols), 1),
        "flat_right": round(run_len(cols, cstd, True, False, d_cols), 1),
    }


def padding_verdict(r: dict, thresh: float = 8.0) -> str:
    if "error" in r:
        return "PROBE-FAIL"
    v = []
    if r["dark_top"] + r["dark_bot"] > thresh:
        v.append("LETTERBOX")
    if r["dark_left"] + r["dark_right"] > thresh:
        v.append("PILLARBOX")
    if r["flat_top"] + r["flat_bot"] > thresh and not v:
        v.append("flat-bands?")
    if r["flat_left"] + r["flat_right"] > thresh and "PILLARBOX" not in v:
        v.append("blur-sides?")
    return " ".join(v)


# ---------------------------------------------------------------- twins

def _sig(p: Path, dur: float, n: int = 8) -> list[int]:
    hs = []
    for i in range(n):
        g = gray(p, dur * (i + 0.5) / n, 9, 8)
        if g is None:
            hs.append(0)
            continue
        r = g.reshape(8, 9)
        bits = 0
        for row in range(8):
            for col in range(8):
                bits = (bits << 1) | (1 if r[row, col] < r[row, col + 1] else 0)
        hs.append(bits)
    return hs


def _mad_aligned(a: Path, b: Path, common: float, n: int = 10) -> float:
    """Mean abs difference at IDENTICAL absolute timestamps.

    Proportional sampling is what makes two copies of one ad that differ by 0.1 s in length
    look like different ads; anchoring both to the same wall-clock offsets removes that.
    """
    diffs = []
    for i in range(n):
        t = common * (i + 0.5) / n
        fa, fb = gray(a, t, 64, 114), gray(b, t, 64, 114)
        if fa is None or fb is None:
            diffs.append(255.0)
            continue
        diffs.append(float(np.abs(fa - fb).mean()))
    return sum(diffs) / len(diffs)


def find_twins(rows: list[dict], workers: int, mad_max: float = 3.0) -> list[dict]:
    paths = {r["name"]: Path(r["path"]) for r in rows}
    by_name = {r["name"]: r for r in rows}
    hashes = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for name, h in zip(paths, ex.map(md5, paths.values())):
            hashes[name] = h
    twins = []
    seen = set()
    rev: dict[str, list[str]] = {}
    for n, h in hashes.items():
        rev.setdefault(h, []).append(n)
    for group in rev.values():
        for a, b in itertools.combinations(sorted(group), 2):
            twins.append({"a": a, "b": b, "kind": "md5", "mad": 0.0})
            seen.add((a, b))
    # perceptual prefilter, then confirm survivors at aligned timestamps
    sigs = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {n: ex.submit(_sig, paths[n], by_name[n]["dur"]) for n in paths}
        for n, f in futs.items():
            sigs[n] = f.result()

    def dist(x, y):
        return sum(bin(i ^ j).count("1") for i, j in zip(x, y))

    cands = [(a, b) for a, b in itertools.combinations(sorted(sigs), 2)
             if (a, b) not in seen and dist(sigs[a], sigs[b]) <= 60]
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {(a, b): ex.submit(_mad_aligned, paths[a], paths[b],
                                  min(by_name[a]["dur"], by_name[b]["dur"])) for a, b in cands}
        for (a, b), f in futs.items():
            mad = f.result()
            if mad <= mad_max:
                twins.append({"a": a, "b": b, "kind": "frame", "mad": round(mad, 2)})
    return sorted(twins, key=lambda t: t["mad"])


# ---------------------------------------------------------------- commands

def cmd_scan(args):
    src = Path(args.src)
    work = Path(args.work or src.parent / "_qa")
    work.mkdir(parents=True, exist_ok=True)
    files = sorted(src.glob("*.mp4"))
    if not files:
        sys.exit(f"no .mp4 under {src}")
    print(f"[scan] {len(files)} sources in {src}\n")

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        rows = list(ex.map(probe, files))
    (work / "source_probe.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")

    odd = [r for r in rows if not r["is_9_16"]]
    noaud = [r["name"] for r in rows if not r.get("audio")]
    print(f"  9:16 {len(rows) - len(odd)}/{len(rows)}   fps {sorted({r['fps'] for r in rows})}   "
          f"dur {min(r['dur'] for r in rows):.1f}-{max(r['dur'] for r in rows):.1f}s")
    if noaud:
        print(f"  !! no audio track: {noaud}")
    if odd:
        print("\n  -- NOT 9:16 (needs a staging crop) --")
        for r in odd:
            print(f"     {r['name']:34s} display={r['display']:10s} ratio={r['disp_ratio']:.3f} "
                  f"sar={r['sar']}")

    print("\n  -- baked padding in the SOURCES (caught before render) --")
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        pads = list(ex.map(padding_report, [Path(r["path"]) for r in rows]))
    flagged = []
    for r, pad in zip(rows, pads):
        verdict = padding_verdict(pad)
        if verdict:
            flagged.append({"name": r["name"], **pad, "verdict": verdict})
            print(f"     {r['name']:34s} dark T/B {pad['dark_top']:.0f}/{pad['dark_bot']:.0f}%  "
                  f"L/R {pad['dark_left']:.0f}/{pad['dark_right']:.0f}%   {verdict}")
    if not flagged:
        print("     none")
    (work / "source_padding.json").write_text(json.dumps(flagged, indent=1), encoding="utf-8")

    print(f"\n  -- duplicate pairs (REPORTED, never dropped) --")
    twins = find_twins(rows, args.workers)
    (work / "twins.json").write_text(json.dumps(twins, indent=1), encoding="utf-8")
    if twins:
        for t in twins:
            print(f"     {t['kind']:5s} mad={t['mad']:5.2f}  {t['a']:30s} <-> {t['b']}")
        print(f"     {len(twins)} pair(s). Ship every file; give each twin a different BGM "
              f"cluster and keep the pair out of one ad set.")
    else:
        print("     none")

    print(f"\n  -- tail sheets ({args.per_sheet} clips per montage) --")
    items = []
    for r in rows:
        d = r["dur"]
        start = max(0.0, d - args.tail_secs)
        n = args.frames
        items.append((Path(r["path"]),
                      [start + (d - 0.06 - start) * i / (n - 1) for i in range(n)]))
    sheets = build_sheets(items, work / "sheets", "tail", args.per_sheet, args.workers)
    print(f"\n[scan] done. {len(sheets)} tail sheet(s) in {work / 'sheets'}")
    print("       Read those, list the clips carrying a competitor end-card, then:")
    print("       qa_sweep.py fine --src <sources> --windows 'NAME:9.2-10.4,NAME2:6.2-7.4'")


def cmd_fine(args):
    src = Path(args.src)
    work = Path(args.work or src.parent / "_qa")
    out = work / "fine"
    out.mkdir(parents=True, exist_ok=True)
    items = []
    for spec in args.windows.split(","):
        spec = spec.strip()
        if not spec:
            continue
        name, rng = spec.rsplit(":", 1)
        a, b = (float(x) for x in rng.split("-"))
        p = src / (name if name.endswith(".mp4") else f"{name}.mp4")
        if not p.is_file():
            sys.exit(f"no such clip: {p}")
        times = []
        t = a
        while t <= b + 1e-6:
            times.append(round(t, 2))
            t += args.step
        items.append((p, times))
    print(f"[fine] {len(items)} window(s) at {args.step}s")
    sheets = build_sheets(items, out, "fine", args.per_sheet, args.workers)
    print(f"[fine] {len(sheets)} sheet(s) in {out}")
    print("       Cut ONE sample after the last clean frame - losing a beat of demo is free,")
    print("       keeping a frame of someone else's 'Download Now' is not.")


def cmd_verify(args):
    outdir = Path(args.out)
    work = Path(args.work or outdir.parent / "_qa")
    work.mkdir(parents=True, exist_ok=True)
    files = sorted(outdir.rglob("*.mp4"))
    if not files:
        sys.exit(f"no .mp4 under {outdir}")

    cuts, srcmap = {}, {}
    if args.manifest:
        man = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
        for v in man.get("videos", []):
            cuts[v["id"]] = v.get("endcard_cut")
            srcmap[v["id"]] = v.get("src_path")

    ctrl = Path(args.ctrl) if args.ctrl else None
    print(f"[verify] {len(files)} outputs in {outdir}\n")

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        probes = list(ex.map(probe, files))
        pads = list(ex.map(lambda p: padding_report(p, skip_tail=args.outro_secs + 0.2), files))
        hashes = list(ex.map(md5, files))

    if args.expect:
        ew, eh = (int(x) for x in args.expect.lower().split("x"))
    else:
        mode = max({p["coded"] for p in probes}, key=[p["coded"] for p in probes].count)
        ew, eh = (int(x) for x in mode.split("x"))
        print(f"  (no --expect given; using the majority canvas {ew}x{eh})")

    rows, fails, need_ctrl = [], [], []
    print(f"  {'clip':16s} {'dur':>7s} {'cut':>6s} {'src dB':>7s} {'mix dB':>7s} "
          f"{'bed':>6s} {'drift':>6s} {'tail dB':>8s}  status")
    for p, pr, pad, h in zip(files, probes, pads, hashes):
        vid = p.stem
        bad = []
        if pr["coded"] != f"{ew}x{eh}":
            bad.append(f"dims {pr['coded']}")
        if pr["sar"] not in ("1:1",):
            bad.append(f"sar {pr['sar']}")
        if pr["pix_fmt"] != "yuv420p":
            bad.append(f"pix {pr['pix_fmt']}")
        if not pr.get("audio"):
            bad.append("NO AUDIO")
        pv = padding_verdict(pad)
        if pv and "?" not in pv:
            bad.append(pv)

        cut = cuts.get(vid)
        srcp = Path(srcmap[vid]) if srcmap.get(vid) and Path(srcmap[vid]).is_file() else None
        body = float(cut) if cut else (pr["dur"] - args.outro_secs)
        if cut and abs(pr["dur"] - (body + args.outro_secs)) > 0.35:
            bad.append(f"dur {pr['dur']:.2f} != cut {body:.2f}+{args.outro_secs:g}")

        src_db = db(pcm(srcp, 0, body)) if srcp else float("nan")
        mix_db = db(pcm(p, 0, body))
        tail_db = db(pcm(p, max(pr["dur"] - args.outro_secs + 0.4, 0), args.outro_secs - 0.6))
        bed = mix_db - src_db if srcp else float("nan")

        drift = float("nan")
        if ctrl:
            c = ctrl / p.relative_to(outdir)
            if c.is_file() and srcp:
                drift = db(pcm(c, 0, body)) - src_db
                if abs(drift) > 1.0:
                    bad.append(f"source re-levelled {drift:+.1f}dB")
        elif srcp and abs(bed) > args.bed_tol:
            need_ctrl.append(vid)

        if tail_db < -60:
            bad.append(f"SILENT tail {tail_db:.0f}dB")
        if mix_db < -45:
            bad.append(f"body near-silent {mix_db:.0f}dB")

        status = "OK" if not bad else "FAIL: " + "; ".join(bad)
        if bad:
            fails.append((vid, bad))
        print(f"  {vid:16s} {pr['dur']:7.2f} {(f'{cut:.2f}' if cut else '-'):>6s} "
              f"{src_db:7.1f} {mix_db:7.1f} {bed:+6.2f} "
              f"{(f'{drift:+.2f}' if drift == drift else '   -'):>6s} {tail_db:8.1f}  {status}")
        rows.append({"id": vid, "dur": pr["dur"], "cut": cut, "src_db": round(src_db, 1),
                     "mix_db": round(mix_db, 1), "bed_prominence_db": round(bed, 2),
                     "source_drift_db": (round(drift, 2) if drift == drift else None),
                     "tail_db": round(tail_db, 1), "padding": pad, "md5": h,
                     "ok": not bad, "issues": bad})

    (work / "qa_report.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
    print(f"\n[verify] {len(files)} outputs | FAIL={len(fails)}")
    print(f"  unique md5: {len(set(hashes))}/{len(hashes)}   "
          f"end-card cuts applied: {sum(1 for r in rows if r['cut'])}")
    soft = [r["id"] for r in rows if padding_verdict(r["padding"]) .endswith("?")]
    if soft:
        print(f"  soft padding hits (confirm by eye, a dark card reads as padding): {soft}")
    if fails:
        print("\n  FAILURES:")
        for n, b in fails:
            print(f"    {n}: {'; '.join(b)}")
    if need_ctrl:
        print(f"\n  {len(need_ctrl)} clip(s) sit >{args.bed_tol} dB above their source, which is bed")
        print(f"  prominence, NOT proof the source changed. Prove it on just those with a")
        print(f"  bed-muted control render, then re-run verify with --ctrl <dir>:")
        print(f"    run.py brandpass ... --bgm-under-gain 0.001 --dst <ctrl-dir>")
        print(f"    clips: {', '.join(need_ctrl)}")
    elif ctrl:
        d = [abs(r["source_drift_db"]) for r in rows if r["source_drift_db"] is not None]
        if d:
            print(f"  max source drift vs control: {max(d):.2f} dB "
                  f"({'source untouched' if max(d) < 1.0 else 'CHECK'})")

    if not args.no_sheets:
        print(f"\n  -- output sheets ({args.per_sheet} clips per montage) --")
        items = []
        for p, pr in zip(files, probes):
            b = pr["dur"] - args.outro_secs
            items.append((p, [b * f for f in (0.03, 0.3, 0.6, 0.95)]
                          + [b + 0.3, b + args.outro_secs - 0.3]))
        sheets = build_sheets(items, work / "sheets", "out", args.per_sheet, args.workers)
        print(f"\n[verify] {len(sheets)} output sheet(s) in {work / 'sheets'} - review by eye;"
              f" mechanical checks are blind to branding and framing taste.")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workers", type=int, default=6, help="parallel ffmpeg calls (default 6)")
    ap.add_argument("--per-sheet", type=int, default=6, help="clips stacked per montage (default 6)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan", help="BEFORE render: probe, twins, source padding, tail sheets")
    s.add_argument("--src", required=True)
    s.add_argument("--work", default=None, help="QA dir (default <src>/../_qa)")
    s.add_argument("--tail-secs", type=float, default=7.0)
    s.add_argument("--frames", type=int, default=9)
    s.set_defaults(func=cmd_scan)

    f = sub.add_parser("fine", help="zoom one end-card window to pick the exact cut")
    f.add_argument("--src", required=True)
    f.add_argument("--work", default=None)
    f.add_argument("--windows", required=True,
                   help="comma list of CLIP:start-end, e.g. 'VD_01:9.2-10.4,VD_07:6.2-7.4'")
    f.add_argument("--step", type=float, default=0.15)
    f.set_defaults(func=cmd_fine)

    v = sub.add_parser("verify", help="AFTER render: geometry, padding, audio, output sheets")
    v.add_argument("--out", required=True, help="render output dir (searched recursively)")
    v.add_argument("--work", default=None)
    v.add_argument("--manifest", default=None, help="manifest.json for endcard_cut + src_path")
    v.add_argument("--ctrl", default=None, help="bed-muted control render dir, if one exists")
    v.add_argument("--expect", default=None, help="expected canvas, e.g. 954x1696")
    v.add_argument("--outro-secs", type=float, default=3.0)
    v.add_argument("--bed-tol", type=float, default=1.5,
                   help="mix-vs-source dB above which a control render is asked for")
    v.add_argument("--no-sheets", action="store_true")
    v.set_defaults(func=cmd_verify)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
