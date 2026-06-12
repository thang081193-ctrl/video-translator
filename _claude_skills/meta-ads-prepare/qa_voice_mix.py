"""Post-hoc Voice/BGM audibility audit for delivered ad packs.

Self-contained (ffmpeg + optionally faster-whisper) — no repo imports, runs on
any box including a Vast instance. For every .mp4 it reports integrated
loudness + true peak, and with --whisper proves the narration is actually
intelligible (tiny model transcript word count).

Usage:
    python qa_voice_mix.py "<pack folder>" [--sample 10] [--whisper]
        [--expect-voice] [--csv report.csv] [--seed 7]

Verdicts per file:
    LUFS    PASS  -20 .. -11 LUFS integrated (voiced target is -16±3)
            WARN  outside that range (too quiet → platforms boost BGM+noise;
                  too hot → crushed)
    TP      WARN  > -0.3 dBTP (clipping risk; pipeline limiter targets ≤ -1)
    SPEECH  n words from whisper-tiny; NO-SPEECH + --expect-voice → FAIL

Exit code 1 only when --expect-voice and at least one file has no detectable
speech — that is the "voice drowned or missing" red flag.
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import re
import subprocess
import sys
from pathlib import Path

LUFS_RANGE = (-20.0, -11.0)
TP_WARN = -0.3


def measure_loudness(path: str) -> dict | None:
    try:
        r = subprocess.run(
            ["ffmpeg", "-hide_banner", "-nostats", "-i", path, "-map", "0:a:0",
             "-af", "loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json",
             "-f", "null", "-"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=300,
        )
        if r.returncode != 0:
            return None
        m = None
        for m in re.finditer(r"\{[^{}]*\"input_i\"[^{}]*\}", r.stderr, re.S):
            pass
        if m is None:
            return None
        d = json.loads(m.group(0))
        return {k: max(min(float(d[k]), 99.0), -99.0)
                for k in ("input_i", "input_tp")}
    except Exception:
        return None


def whisper_words(path: str, model_cache: list) -> int:
    if not model_cache:
        from faster_whisper import WhisperModel
        model_cache.append(WhisperModel("tiny", device="cpu", compute_type="int8"))
    segs, _ = model_cache[0].transcribe(path, beam_size=1, vad_filter=True)
    text = " ".join(s.text.strip() for s in segs)
    return len(re.findall(r"[\w']+", text))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("folder")
    ap.add_argument("--sample", type=int, default=0, help="audit N random files (0=all)")
    ap.add_argument("--whisper", action="store_true", help="speech-presence check (slower)")
    ap.add_argument("--expect-voice", action="store_true",
                    help="files are voiced creatives: NO-SPEECH becomes a FAIL")
    ap.add_argument("--csv", default=None)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    files = sorted(Path(args.folder).rglob("*.mp4"))
    if not files:
        print(f"no .mp4 under {args.folder}")
        return 1
    if args.sample and args.sample < len(files):
        files = sorted(random.Random(args.seed).sample(files, args.sample))

    model_cache: list = []
    rows, fails = [], 0
    print(f"{'file':<58} {'LUFS':>7} {'TP':>6} {'speech':>7}  verdict")
    for f in files:
        meas = measure_loudness(str(f))
        if meas is None:
            print(f"{str(f.name)[:58]:<58} {'--':>7} {'--':>6} {'--':>7}  NO-AUDIO")
            fails += 1 if args.expect_voice else 0
            rows.append([str(f), None, None, None, "NO-AUDIO"])
            continue
        i, tp = meas["input_i"], meas["input_tp"]
        notes = []
        if not (LUFS_RANGE[0] <= i <= LUFS_RANGE[1]):
            notes.append("LUFS-WARN")
        if tp > TP_WARN:
            notes.append("CLIP-WARN")
        wcount = ""
        if args.whisper:
            n = whisper_words(str(f), model_cache)
            wcount = str(n)
            if n == 0:
                notes.append("NO-SPEECH")
                if args.expect_voice:
                    fails += 1
        verdict = ",".join(notes) if notes else "PASS"
        print(f"{str(f.name)[:58]:<58} {i:>7.2f} {tp:>6.2f} {wcount:>7}  {verdict}")
        rows.append([str(f), i, tp, wcount, verdict])

    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["file", "lufs_i", "tp", "speech_words", "verdict"])
            w.writerows(rows)
        print(f"csv → {args.csv}")

    n_pass = sum(1 for r in rows if r[4] == "PASS")
    print(f"\n{len(rows)} files: {n_pass} PASS, {len(rows)-n_pass} flagged"
          + (f", {fails} voice-FAIL" if args.expect_voice else ""))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
