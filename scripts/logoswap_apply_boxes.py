"""Turn picker output into specs, then render and QA the whole batch.

Consumes the JSON emitted by scripts/logoswap_pick.py:

    {"100": {"t1": [306,391,53,52], "s1": [12,470,110,110]}, ...}

`t1` is the app icon on the phone the actor holds -- tracked. `s1` is the
start-of-clip competitor logo card -- fixed position, so it only needs a time
range. The picker cannot capture that range, but with an exact box it is
trivial to measure: watch that rectangle and note when it stops matching
itself. That is a far easier signal than finding the card in the first place,
which is why it is done here rather than guessed in the picker.

    python scripts/logoswap_apply_boxes.py --src <dir> --boxes boxes.json \
        [--logo <png>] [--render] [--only 1,13]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.logger import get_logger, setup_logging          # noqa: E402
from pipeline.logo_swap import (                                # noqa: E402
    FILL_CLEAN_PLATE, FILL_LOGO_ROUNDED, Box, ClipSpec, CutSpec, SearchCfg,
    StaticSpec,
    TrackedSpec, probe_video, render_clip, save_spec, spec_path, validate_spec,
)

log = get_logger("LogoSwap")

DEFAULT_LOGO = ""      # pass --logo; no default path baked in
WIDEN = 2.5        # seconds of slack either side of a detected window


def card_windows(mp4: Path, box: tuple[int, int, int, int], fps: float,
                 limit: float, ref_t: float = 0.5,
                 step: float = 0.2) -> list[tuple[float, float]]:
    """Find WHEN a fixed-position card is on screen, given its exact box.

    Not all of these cards open the clip: corner logo cards do, but a lower-third
    name pill often appears mid-clip. So both ends are measured rather than
    assuming a start of 0.

    Method: an opaque card is FROZEN -- consecutive frames inside its box are
    pixel-identical -- AND visually distinct from whatever is behind it. Both
    halves are needed. Frozen alone picks the wrong run whenever the shot is
    locked off: clip 77's card sits over a table that never moves, and the
    table held still for longer than the card, so the window came back as
    20.1-28.5s instead of ~0-5s. Distinct alone picks the majority appearance,
    which after the card leaves is the background.

    So: take every frozen run of at least a second, and keep the one whose
    content differs most from the frames outside it.
    """
    x, y, w, h = box
    cap = cv2.VideoCapture(str(mp4))
    try:
        ts, diffs, sigs = [], [], []
        prev = None
        t = 0.1
        while t < limit:
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(t * fps))
            ok, f = cap.read()
            if not ok:
                break
            patch = f[y:y + h, x:x + w]
            if patch.size:
                g = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY).astype(np.float32)
                if prev is not None and prev.shape == g.shape:
                    diffs.append(float(np.abs(g - prev).mean()))
                    sigs.append(cv2.resize(g, (16, 16)).ravel())
                    ts.append(t)
                prev = g
            t += step
        if len(diffs) < 4:
            return 0.0, min(5.0, limit)

        frozen = np.array(diffs) < 2.0
        S = np.stack(sigs)
        runs = []
        i = 0
        while i < len(frozen):
            if not frozen[i]:
                i += 1
                continue
            j = i
            while j < len(frozen) and frozen[j]:
                j += 1
            if (j - i) * step >= 1.0:
                runs.append((i, j))
            i = j
        if not runs:
            return [(0.0, min(5.0, limit))]

        # Match against the card's ACTUAL appearance, sampled at ref_t -- a time
        # a human confirmed it is on screen. "Frozen and unlike the rest" is not
        # enough on its own: when the card leaves, clip 1 shows a static table
        # behind it, which is equally frozen, so that test lit up six windows
        # covering almost the whole clip and would have pasted our logo onto the
        # table. Comparing to the real thing has no such ambiguity.
        # Measured separation is wide: on clips 1 and 81 the runs holding the
        # card sit 16-19 away from the reference and every other frozen run sits
        # 75-111 away. Anything in that gap works; 35 is the midpoint. A tight
        # threshold like 10 rejects the card's own runs, because the reference
        # frame is a single sample and the card is re-encoded slightly
        # differently each time it fades in.
        ref_i = int(np.argmin([abs(t - ref_t) for t in ts]))
        ref_sig = S[ref_i]
        out: list[tuple[float, float]] = []
        for a, b in runs:
            if float(np.abs(S[a:b].mean(axis=0) - ref_sig).mean()) <= 35.0:
                out.append((round(max(0.0, ts[a] - step), 2),
                            round(min(limit, ts[b - 1] + step), 2)))
        if not out:
            return [(0.0, min(5.0, limit))]
        # merge intervals separated by less than a beat
        out.sort()
        merged = [list(out[0])]
        for lo, hi in out[1:]:
            if lo - merged[-1][1] <= 0.6:
                merged[-1][1] = hi
            else:
                merged.append([lo, hi])
        return [(a, b) for a, b in merged]
    finally:
        cap.release()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src", required=True)
    ap.add_argument("--boxes", required=True, help="picker JSON file")
    ap.add_argument("--logo", default=DEFAULT_LOGO, required=not DEFAULT_LOGO)
    ap.add_argument("--dst", default=None, help="default <src>/_logoswap_out")
    ap.add_argument("--only", default="")
    ap.add_argument("--render", action="store_true")
    ap.add_argument("--corner-radius", type=float, default=0.24)
    ap.add_argument("--pad", type=float, default=0.08)
    args = ap.parse_args()
    setup_logging("cli")

    src = Path(args.src)
    dst = Path(args.dst) if args.dst else src / "_logoswap_out"
    batch = src / "_ytbatch"
    cls = json.loads((batch / "classification.json").read_text(encoding="utf-8"))
    cuts = json.loads((batch / "endcard_cuts.json").read_text())["cuts"]
    boxes = json.loads(Path(args.boxes).read_text(encoding="utf-8"))
    ship = {v["id"]: v for v in cls["videos"] if v.get("ship")}
    only = {s.strip() for s in args.only.split(",") if s.strip()}

    missing = [c for c in ship if c not in boxes or not boxes[c].get("t1")]
    if missing:
        print(f"note: no t1 box for {len(missing)} clip(s): {', '.join(sorted(missing))}")

    rows = []
    started = time.time()
    for cid, spec_boxes in sorted(boxes.items()):
        if cid not in ship or (only and cid not in only):
            continue
        v = ship[cid]
        mp4 = src / v["file"]
        info = probe_video(mp4)
        end = float(cuts.get(cid, info.duration))

        tracked, statics = [], []
        if spec_boxes.get("t1"):
            tracked.append(TrackedSpec(
                # MUST match the frame logoswap_pick.py rendered: the box was
                # drawn against that phone pose, and seeding anywhere else cuts
                # a patch the box no longer frames. `t1_t` carries the time when
                # the picker was run with --times.
                id="t1_phone_icon",
                seed_t=float(spec_boxes.get("t1_t") or round(end * 0.45, 2)),
                seed_box=Box.from_px(*spec_boxes["t1"], info.width, info.height),
                t=(0.0, end), search=SearchCfg(), fill=FILL_LOGO_ROUNDED,
                corner_radius=args.corner_radius, pad=args.pad,
                feather_px=1.6, grade=True))
        if spec_boxes.get("s1"):
            bx = tuple(int(n) for n in spec_boxes["s1"])          # type: ignore[arg-type]
            ref_t = float(spec_boxes.get('s1_ref') or 0.5)
            wins = card_windows(mp4, bx, info.fps_f, end, ref_t)
            if spec_boxes.get("s1_t"):
                wins = [tuple(float(v) for v in spec_boxes["s1_t"])]
            for j, (a, b) in enumerate(wins, 1):
                # The freeze test cannot see the card slide in or out, so the
                # detected window is always short at both ends. Widen it and let
                # the per-frame gate decide -- clip 1's card was still on screen
                # at 15.0s and 39.0s, outside a 16-19 / 40-44 detection.
                statics.append(StaticSpec(
                    id="s1_logo_card" if len(wins) == 1 else f"s1_logo_card_{j}",
                    box=Box.from_px(*bx, info.width, info.height),
                    t=(max(0.0, a - WIDEN), min(end, b + WIDEN)),
                    ref_t=(a + b) / 2,
                    fill=FILL_LOGO_ROUNDED, kind="logo",
                    corner_radius=args.corner_radius, pad=args.pad,
                    feather_px=1.6, grade=True))

        # Wipe boxes: competitor TEXT, painted over with a clean plate and no
        # mark drawn back. Deliberately not re-lettered -- on a 360px source
        # those glyphs are 5-7px tall and no font substitution survives the
        # comparison; an unlabelled icon reads as a crop, a wrong-font name
        # reads as an edit.
        wipes = spec_boxes.get("x") or []
        for i, raw in enumerate(wipes, 1):
            wb = tuple(int(n) for n in raw)
            # Ride the phone ONLY if the text is ON the phone. Clip 78's
            # "ID Caller" caption sits directly above the icon and moves with
            # it; clips 1/13/81/86 carry a fixed lower-third name pill that has
            # nothing to do with the handset. Attaching those to the phone
            # tracker dragged the wipe around the frame and painted bars over
            # the table.
            on_phone = False
            if tracked:
                t1b = spec_boxes["t1"]
                reach = 2.5 * max(t1b[2], t1b[3])
                dx = abs((wb[0] + wb[2] / 2) - (t1b[0] + t1b[2] / 2))
                dy = abs((wb[1] + wb[3] / 2) - (t1b[1] + t1b[3] / 2))
                on_phone = dx <= reach and dy <= reach
            if on_phone:
                # ride the phone: same template as t1, different cover
                tracked.append(TrackedSpec(
                    id=f"x{i}_wipe", seed_t=tracked[0].seed_t,
                    seed_box=tracked[0].seed_box,
                    cover_box=Box.from_px(*wb, info.width, info.height),
                    t=(0.0, end), search=SearchCfg(),
                    fill=FILL_CLEAN_PLATE, pad=args.pad, feather_px=1.6,
                    grade=False))
            else:
                wref = float(spec_boxes.get('s1_ref') or 0.5)
                for j, (a, b) in enumerate(
                        card_windows(mp4, wb, info.fps_f, end, wref), 1):
                    statics.append(StaticSpec(
                        id=f"x{i}_{j}_wipe",
                        box=Box.from_px(*wb, info.width, info.height),
                        t=(max(0.0, a - WIDEN), min(end, b + WIDEN)),
                        ref_t=(a + b) / 2,
                        fill=FILL_CLEAN_PLATE, kind="text",
                        pad=args.pad, feather_px=1.6, grade=False))

        spec = ClipSpec(
            video_id=mp4.stem, authored_dims=(info.width, info.height),
            fps=info.fps, duration=info.duration, creative_group=f"cg_{cid}",
            cuts=[CutSpec("endcard_and_storepage", end, None)] if cid in cuts else [],
            tracked=tracked, statics=statics,
            reviewed_by="picker", reviewed_at=time.strftime("%Y-%m-%d"))
        errs = validate_spec(spec, info.width, info.height, info.duration)
        if errs:
            print(f"#{cid:<5} SPEC INVALID: {errs}")
            continue
        save_spec(spec, spec_path(src, spec.video_id))
        wins = [s.t for s in statics if s.id.startswith("s1_")]
        print(f"#{cid:<5} T1={'yes' if tracked else ' - '}  "
              f"S1={'/'.join('%.0f-%.0f' % w for w in wins) or '-':<22} "
              f"wipe={len([s for s in statics if 'wipe' in s.id]) + len([t for t in tracked if 'wipe' in t.id])}  "
              f"cut->{end:.2f}s")

        if args.render:
            try:
                qa = render_clip(spec, mp4, dst / v["file"], logo=args.logo)
                tr = (qa["tracks"].get("t1_phone_icon") or {})
                rows.append({"id": cid, **{k: qa[k] for k in
                                           ("verdict", "out_duration", "fails")},
                             "on_pct": tr.get("on_pct"), "score": tr.get("score_med"),
                             "runs": tr.get("on_runs"), "dpos": tr.get("max_dpos")})
                print(f"        -> {qa['verdict']} on={tr.get('on_pct')}% "
                      f"score={tr.get('score_med')} runs={tr.get('on_runs')} "
                      f"dpos={tr.get('max_dpos')}")
            except Exception as e:
                print(f"        -> RENDER FAILED: {type(e).__name__}: {e}")

    if rows:
        (batch / "logoswap_qa.json").write_text(json.dumps(rows, indent=2))
        bad = [r for r in rows
               if r["verdict"] != "PASS" or (r.get("on_pct") or 0) < 85
               or (r.get("runs") or 0) > 2 or (r.get("dpos") or 0) > 8]
        print(f"\n{len(rows)} rendered in {time.time() - started:.0f}s   "
              f"clean {len(rows) - len(bad)}   flagged {len(bad)}"
              + (f": {[r['id'] for r in bad]}" if bad else ""))
        print("Now run scripts/logoswap_qa_sheets.py and LOOK -- the numeric gates "
              "cannot see a mark left visible or a logo drawn where none belonged.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
