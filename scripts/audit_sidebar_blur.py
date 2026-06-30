#!/usr/bin/env python3
"""Audit brand-pass outputs for the "bóp ảnh" side-blur bug.

Usage:  python scripts/audit_sidebar_blur.py <out_dir>

A clip is flagged when its content was wrongly cropped to a narrow center band
then blur-padded. Verified rule (PlantSmart 0630): the blurred side bars have
near-zero high-freq energy -> edge_min < 4.0, and the sharp center band occupies
only 45-90% of the width. Clean full-frame 9:16 clips score edge_min >= 6.
See docs/PLANTSMART_0630_BLURBUG.md for the root cause + fix.
"""
import cv2, numpy as np, glob, os, sys

EDGE_MIN_MAX = 4.0      # below this on the blurred side => bar
BAND_LO, BAND_HI = 45.0, 90.0

def metrics(path, n=7):
    cap = cv2.VideoCapture(path)
    nf = int(cap.get(7)); W = int(cap.get(3))
    if nf <= 0:
        cap.release(); return None
    Ls, Rs, lap_l, lap_r = [], [], [], []
    s = int(W * 0.12)
    for i in range(n):
        cap.set(1, int(nf * (0.12 + 0.76 * i / (n - 1))))
        ok, f = cap.read()
        if not ok:
            continue
        g = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)
        sx = np.abs(cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3)).mean(axis=0)
        win = max(5, W // 100)
        sm = np.convolve(sx, np.ones(win) / win, mode="same")
        ab = sm > sm.max() * 0.35
        if ab.any():
            Ls.append(int(np.argmax(ab)))
            Rs.append(int(len(sm) - np.argmax(ab[::-1])))
        lap_l.append(cv2.Laplacian(g[:, :s], cv2.CV_64F).var())
        lap_r.append(cv2.Laplacian(g[:, W - s:], cv2.CV_64F).var())
    cap.release()
    if not lap_l:
        return None
    Lm = int(np.median(Ls)) if Ls else 0
    Rm = int(np.median(Rs)) if Rs else W
    return (float(min(np.median(lap_l), np.median(lap_r))),  # edge_min
            100.0 * (Rm - Lm) / W)                            # band%

def main():
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(1)
    out_dir = sys.argv[1]
    files = sorted(glob.glob(os.path.join(out_dir, "**", "*.mp4"), recursive=True))
    bad = []
    for i, f in enumerate(files):
        m = metrics(f)
        if m is None:
            continue
        em, bw = m
        if em < EDGE_MIN_MAX and BAND_LO < bw < BAND_HI:
            bad.append((os.path.relpath(f, out_dir).replace("\\", "/"), em, bw))
        if (i + 1) % 50 == 0:
            print(f"...{i+1}/{len(files)}", flush=True)
    print(f"\n==== SIDE-BLUR AUDIT ====  total={len(files)}  AFFECTED={len(bad)}")
    for rel, em, bw in sorted(bad):
        print(f"  edge_min={em:5.2f}  band%={bw:5.1f}  {rel}")

if __name__ == "__main__":
    main()
