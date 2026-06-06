#!/usr/bin/env python3
"""Phase 2 of Claude-translated dubbing: build FR/TR dubs from the Claude-filled
_dub_cache/transcripts.json + cached BGM, then append the DecoAI outro.

Run AFTER Claude has filled every segment's "fr" and "tr" fields.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
from extract_transcripts import ROOT, CACHE, append_outro  # noqa: E402


def main():
    from dotenv import load_dotenv
    load_dotenv()
    from pipeline.logger import setup_logging
    setup_logging(mode="cli")
    from pipeline.dub import build_dubbed_audio

    ap = argparse.ArgumentParser()
    ap.add_argument("--langs", default="fr,tr")
    a = ap.parse_args()
    langs = [x.strip() for x in a.langs.split(",") if x.strip()]

    recs = json.loads(open(os.path.join(CACHE, "transcripts.json"), encoding="utf-8").read())
    # Merge Claude's translations (positional arrays per stem) into the segments.
    trans = json.loads(open(os.path.join(CACHE, "translations.json"), encoding="utf-8").read())
    for rec in recs:
        t = trans.get(rec["stem"], {})
        for i, s in enumerate(rec["segments"]):
            for lang in ("fr", "tr"):
                arr = t.get(lang, [])
                s[lang] = arr[i] if i < len(arr) else ""
        for lang in ("fr", "tr"):
            n = len(t.get(lang, []))
            if n != len(rec["segments"]):
                print(f"WARN {rec['stem']}/{lang}: {n} translations vs "
                      f"{len(rec['segments'])} segments", flush=True)
    done = skip = fail = 0
    t0 = time.time()
    for rec in recs:
        stem = rec["stem"]
        angle, _, dateseq = stem.rsplit("_", 2)
        sdir = os.path.join(CACHE, stem)
        no_vocals = os.path.join(sdir, "no_vocals.wav")
        main = os.path.join(sdir, "main.mp4")
        for lang in langs:
            if any(not str(s.get(lang, "")).strip() for s in rec["segments"]):
                print(f"{stem}/{lang}: SKIP-INCOMPLETE (translations missing)", flush=True)
                skip += 1
                continue
            out = os.path.join(ROOT, "STORAGE_VO", lang.upper(),
                               f"{angle}_{lang.upper()}_{dateseq}.mp4")
            os.makedirs(os.path.dirname(out), exist_ok=True)
            work = tempfile.mkdtemp(prefix="apply_")
            try:
                segs = [{"start": s["start"], "end": s["end"],
                         "translated_text": s[lang], "text": s[lang]}
                        for s in rec["segments"]]
                dub = os.path.join(work, "dub.m4a")
                build_dubbed_audio(segs, lang=lang, output_path=dub, output_dir=work,
                                   custom_voice=None, audio_mode="keep_original_bgm",
                                   pre_separated_no_vocals_path=no_vocals)
                dubmain = os.path.join(work, "dubmain.mp4")
                subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", main, "-i", dub,
                                "-c:v", "copy", "-map", "0:v:0", "-map", "1:a:0", dubmain],
                               check=True)
                append_outro(dubmain, out)
                print(f"{stem}/{lang}: DONE -> {os.path.basename(out)} "
                      f"({os.path.getsize(out)//1024}KB)", flush=True)
                done += 1
            except Exception as e:
                print(f"{stem}/{lang}: FAIL {e}", flush=True)
                fail += 1
            finally:
                shutil.rmtree(work, ignore_errors=True)
    print(f"\n=== APPLY: {done} dubbed, {skip} skipped, {fail} failed "
          f"in {(time.time()-t0)/60:.1f} min ===", flush=True)


if __name__ == "__main__":
    main()
