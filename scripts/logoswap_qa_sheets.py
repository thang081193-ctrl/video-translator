"""Build the visual QA gate for a logo-swap batch: source vs output, side by side.

Every numeric check in pipeline.logo_swap is a tripwire for BREAKAGE -- frame
counts, duration drift, flicker, swim. None of them can see the two failures
that actually matter:

  1. the competitor mark still visible (box too small, t range too short)
  2. our logo drawn where theirs never was (a false ON run)

Only eyes catch those, so this script exists to make looking cheap. It crops
tightly around each target's box and tiles source-above-output across the clip,
which is far more legible than watching 23 files.

    python scripts/logoswap_qa_sheets.py --src <dir> --dst <dir> [--per-sheet 5]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.logo_swap import load_spec, probe_video, spec_path  # noqa: E402

TILE = 190
SAMPLES = 8


def _row(src_mp4: Path, out_mp4: Path, spec, label: str) -> np.ndarray | None:
    """One clip -> a 2-row strip: source crops above output crops."""
    if not out_mp4.exists():
        return None
    info = probe_video(src_mp4)
    got = probe_video(out_mp4)

    target = (spec.tracked[0] if spec.tracked
              else (spec.statics[0] if spec.statics else None))
    if target is None:
        return None
    box = getattr(target, "cover_box", None) or getattr(target, "seed_box", None) \
        or target.box
    bx, by, bw, bh = box.to_px(info.width, info.height)
    pad = int(max(bw, bh) * 0.55) + 8

    # sample the region the swap actually covers, in each file's own timebase
    kept = spec.kept_duration()
    strips = []
    for path, dur, tag in ((src_mp4, kept, "SRC"), (out_mp4, got.duration, "OUT")):
        cap = cv2.VideoCapture(str(path))
        fps = cap.get(cv2.CAP_PROP_FPS) or float(info.fps)
        tiles = []
        for i in range(SAMPLES):
            t = dur * (0.03 + 0.94 * i / (SAMPLES - 1))
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(t * fps))
            ok, f = cap.read()
            if not ok:
                tiles.append(np.zeros((TILE, TILE, 3), np.uint8))
                continue
            y0, y1 = max(0, by - pad), min(f.shape[0], by + bh + pad)
            x0, x1 = max(0, bx - pad), min(f.shape[1], bx + bw + pad)
            crop = f[y0:y1, x0:x1]
            if crop.size == 0:
                crop = f
            tile = cv2.resize(crop, (TILE, TILE), interpolation=cv2.INTER_NEAREST)
            cv2.rectangle(tile, (0, 0), (TILE, 15), (0, 0, 0), -1)
            cv2.putText(tile, f"{tag} {t:5.1f}s", (3, 11),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.36, (0, 255, 255), 1)
            tiles.append(tile)
        cap.release()
        strips.append(np.hstack(tiles))

    head = np.zeros((22, strips[0].shape[1], 3), np.uint8)
    cv2.putText(head, label, (4, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
    return np.vstack([head, *strips])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src", required=True, help="folder holding the source mp4s")
    ap.add_argument("--dst", required=True, help="folder holding the rendered mp4s")
    ap.add_argument("--qa", default=None, help="where to write sheets (default <dst>/_qa)")
    ap.add_argument("--per-sheet", type=int, default=5)
    args = ap.parse_args()

    src, dst = Path(args.src), Path(args.dst)
    qa = Path(args.qa) if args.qa else dst / "_qa"
    qa.mkdir(parents=True, exist_ok=True)

    specs = sorted((src / "_logoswap").glob("*.json"))
    if not specs:
        print(f"no specs under {src / '_logoswap'}")
        return 1

    rows, skipped = [], []
    for sp in specs:
        vid = sp.stem
        src_mp4, out_mp4 = src / f"{vid}.mp4", dst / f"{vid}.mp4"
        if not src_mp4.exists() or not out_mp4.exists():
            skipped.append(vid)
            continue
        spec = load_spec(sp)
        label = f"{vid}   cut->{spec.kept_duration():.2f}s   " \
                f"targets: {len(spec.tracked)}T/{len(spec.statics)}S"
        r = _row(src_mp4, out_mp4, spec, label)
        if r is not None:
            rows.append((vid, r))

    width = max(r.shape[1] for _, r in rows)
    for i in range(0, len(rows), args.per_sheet):
        chunk = rows[i:i + args.per_sheet]
        padded = [np.pad(r, ((0, 0), (0, width - r.shape[1]), (0, 0)),
                         constant_values=255) for _, r in chunk]
        sheet = np.vstack([np.vstack([p, np.full((6, width, 3), 255, np.uint8)])
                           for p in padded])
        fp = qa / f"LOGOSWAP_QA_{i // args.per_sheet}.png"
        cv2.imwrite(str(fp), sheet)
        print(f"wrote {fp.name}  ({', '.join(v for v, _ in chunk)})")

    if skipped:
        print(f"\nskipped (no render): {', '.join(skipped)}")
    print(f"\n{len(rows)} clips on {(len(rows) + args.per_sheet - 1) // args.per_sheet} sheets")
    print("Gate: competitor mark gone in every OUT tile, our logo never on a tile "
          "where theirs was absent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
