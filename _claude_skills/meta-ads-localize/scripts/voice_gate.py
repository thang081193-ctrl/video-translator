#!/usr/bin/env python3
"""Classify each video as real-VO vs BGM-only using the meta-ads-prepare voice gate
(avg_logprob > -0.5). Writes <root>/_vo_gate.json. VO videos keep their spoken
language; music-only / no-audio videos should be moved to a `none` language folder.

A song/music hallucination sits at avg_logprob -0.7..-1.0; no_speech_prob is NOT a
reliable discriminator. Walks all *.mp4 under --root (skips _-prefixed dirs).
Usage: python voice_gate.py --root "<folder>" [--model tiny] [--sample 45]
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from collections import Counter

GATE = -0.5


def gate_one(model, src, sample_s):
    wav = os.path.join(tempfile.gettempdir(), "vg_" + os.path.basename(src) + ".wav")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", src, "-t", str(sample_s),
                    "-ac", "1", "-ar", "16000", "-vn", wav], stderr=subprocess.DEVNULL)
    try:
        segs, info = model.transcribe(wav, language=None, beam_size=1, vad_filter=True)
        segs = list(segs)
        good = [s for s in segs if s.text.strip() and getattr(s, "avg_logprob", -9) > GATE]
        speech = sum((s.end - s.start) for s in good)
        real = speech >= max(1.5, 0.05 * sample_s)
        return {"has_vo": bool(real), "vo_lang": info.language if real else "none",
                "speech_s": round(speech, 1), "n_seg": len(segs)}
    finally:
        try:
            os.remove(wav)
        except OSError:
            pass


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--model", default="tiny")
    ap.add_argument("--sample", type=int, default=45)
    a = ap.parse_args()

    from faster_whisper import WhisperModel
    model = WhisperModel(a.model, device="cpu", compute_type="int8")

    vids = []
    for dp, _, fs in os.walk(a.root):
        rel = os.path.relpath(dp, a.root)
        if any(s.startswith(("_", ".")) for s in rel.split(os.sep)):
            continue
        for f in sorted(fs):
            if f.lower().endswith(".mp4") and not f.endswith(".temp.mp4") and ".part" not in f:
                vids.append(os.path.join(dp, f))

    print(f"gating {len(vids)} videos (model={a.model})...", flush=True)
    results, t0 = [], time.time()
    for i, p in enumerate(vids, 1):
        try:
            r = gate_one(model, p, a.sample)
        except Exception as e:
            r = {"has_vo": False, "vo_lang": "none", "error": str(e)[:60]}
        r["path"] = os.path.relpath(p, a.root)
        results.append(r)
        tag = f"VO:{r['vo_lang']}" if r["has_vo"] else "BGM-only/none"
        print(f"[{i}/{len(vids)}] {r['path'][:46]:<46} {tag}", flush=True)

    out = os.path.join(a.root, "_vo_gate.json")
    json.dump(results, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    c = Counter(("VO:" + r["vo_lang"]) if r["has_vo"] else "none" for r in results)
    print(f"\n=== {dict(c)} ({time.time()-t0:.0f}s) -> {out} ===", flush=True)


if __name__ == "__main__":
    main()
