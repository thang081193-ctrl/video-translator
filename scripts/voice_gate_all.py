#!/usr/bin/env python3
"""Classify every video as VO (real speech) vs BGM-only, using the brand-pass
voice gate (avg_logprob > -0.5). Writes a JSON map for reorganization.

VO videos keep their spoken language; BGM-only videos get language "none".
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

GATE_LOGPROB = -0.5   # brand-pass voice gate: real speech vs music hallucination


def gate_one(model, src, sample_s=45):
    work = tempfile.gettempdir()
    wav = os.path.join(work, "vg_" + os.path.basename(src) + ".wav")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", src, "-t", str(sample_s),
                    "-ac", "1", "-ar", "16000", "-vn", wav], stderr=subprocess.DEVNULL)
    try:
        segs, info = model.transcribe(wav, language=None, beam_size=1, vad_filter=True)
        segs = list(segs)
        good = [s for s in segs if s.text.strip() and getattr(s, "avg_logprob", -9) > GATE_LOGPROB]
        speech = sum((s.end - s.start) for s in good)
        real = speech >= max(1.5, 0.05 * sample_s)
        return {
            "has_vo": bool(real),
            "vo_lang": info.language if real else "none",
            "lang_prob": round(info.language_probability, 2),
            "speech_s": round(speech, 1),
            "n_seg": len(segs),
            "text": " ".join(s.text.strip() for s in segs)[:80],
        }
    finally:
        try:
            os.remove(wav)
        except OSError:
            pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True, help="folder holding <ANGLE>/<LANG>/*.mp4")
    ap.add_argument("--model", default="tiny")
    a = ap.parse_args()

    from faster_whisper import WhisperModel
    model = WhisperModel(a.model, device="cpu", compute_type="int8")

    vids = []
    for ang in sorted(os.listdir(a.root)):
        angp = os.path.join(a.root, ang)
        if not os.path.isdir(angp) or ang.startswith("_"):
            continue
        for lang in sorted(os.listdir(angp)):
            langp = os.path.join(angp, lang)
            if not os.path.isdir(langp):
                continue
            for f in sorted(os.listdir(langp)):
                if f.endswith(".mp4"):
                    vids.append((ang, lang, os.path.join(langp, f)))

    print(f"gating {len(vids)} videos (model={a.model})...", flush=True)
    results = []
    t0 = time.time()
    for i, (ang, lang, path) in enumerate(vids, 1):
        try:
            r = gate_one(model, path)
        except Exception as e:
            r = {"has_vo": False, "vo_lang": "none", "error": str(e)[:60]}
        r.update({"angle": ang, "cur_lang": lang, "path": path,
                  "file": os.path.basename(path)})
        results.append(r)
        tag = f"VO:{r['vo_lang']}" if r["has_vo"] else "BGM-only"
        print(f"[{i}/{len(vids)}] {ang}/{os.path.basename(path)[:30]:<30} {tag:<10} "
              f"speech={r.get('speech_s','?')}s", flush=True)

    out = os.path.join(a.root, "_vo_gate.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(results, fh, ensure_ascii=False, indent=1)

    # summary
    from collections import Counter
    by_angle = {}
    for r in results:
        by_angle.setdefault(r["angle"], Counter())[("VO:" + r["vo_lang"]) if r["has_vo"] else "BGM-only"] += 1
    print(f"\n=== SUMMARY ({time.time()-t0:.0f}s) ===", flush=True)
    for ang, c in sorted(by_angle.items()):
        print(f"  {ang}: {dict(c)}", flush=True)
    print(f"map -> {out}", flush=True)


if __name__ == "__main__":
    main()
