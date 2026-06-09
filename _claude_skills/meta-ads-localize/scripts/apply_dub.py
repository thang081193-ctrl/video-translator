#!/usr/bin/env python3
"""Dub phase 2: build target-language dubs from Claude's translations.

Reads _dub_cache/transcripts.json (segment timing/text) + _dub_cache/translations.json
(Claude's positional translation arrays per stem per lang). For each (stem, lang):
Edge-TTS in the target voice over the cached Demucs no_vocals (original BGM), mux over
main.mp4, append the brand outro -> <src-angle>/<LANG>/<ANGLE>_<LANG>_<MMDD><NN>.mp4.

translations.json schema: {"<stem>": {"fr": ["seg0",...], "tr": [...]}} — array index ==
segment id, length == segment count. Incomplete (stem,lang) is skipped.

Usage: python apply_dub.py --root <folder> --langs fr,tr [--src-angle STORAGE_VO] [--outro <mp4>]
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

VT = os.environ.get("VIDEO_TRANSLATOR_ROOT", r"D:/Dev/Tools/Video Translator")
sys.path.insert(0, VT)
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from extract_transcripts import append_outro  # noqa: E402


def main():
    from dotenv import load_dotenv
    load_dotenv(os.path.join(VT, ".env"))
    from pipeline.logger import setup_logging
    setup_logging(mode="cli")
    from pipeline.dub import build_dubbed_audio

    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--src-angle", default="STORAGE_VO")
    ap.add_argument("--langs", default="fr,tr")
    ap.add_argument("--outro", default=None)
    a = ap.parse_args()
    langs = [x.strip() for x in a.langs.split(",") if x.strip()]

    cache = os.path.join(a.root, "_dub_cache")
    recs = json.load(open(os.path.join(cache, "transcripts.json"), encoding="utf-8"))
    trans = json.load(open(os.path.join(cache, "translations.json"), encoding="utf-8"))

    done = skip = fail = 0
    t0 = time.time()
    for rec in recs:
        stem = rec["stem"]
        angle, _, dateseq = stem.rsplit("_", 2)
        sdir = os.path.join(cache, stem)
        no_vocals = os.path.join(sdir, "no_vocals.wav")
        main = os.path.join(sdir, "main.mp4")
        t = trans.get(stem, {})
        for lang in langs:
            arr = t.get(lang, [])
            if len(arr) != len(rec["segments"]) or any(not str(x).strip() for x in arr):
                print(f"{stem}/{lang}: SKIP (translations {len(arr)}/{len(rec['segments'])})", flush=True)
                skip += 1
                continue
            out = os.path.join(a.root, a.src_angle, lang.upper(),
                               f"{angle}_{lang.upper()}_{dateseq}.mp4")
            os.makedirs(os.path.dirname(out), exist_ok=True)
            work = tempfile.mkdtemp(prefix="apply_")
            try:
                segs = [{"start": s["start"], "end": s["end"],
                         "translated_text": arr[i], "text": arr[i]}
                        for i, s in enumerate(rec["segments"])]
                dub = os.path.join(work, "dub.m4a")
                build_dubbed_audio(segs, lang=lang, output_path=dub, output_dir=work,
                                   custom_voice=None, audio_mode="keep_original_bgm",
                                   pre_separated_no_vocals_path=no_vocals)
                dubmain = os.path.join(work, "dubmain.mp4")
                subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", main, "-i", dub,
                                "-c:v", "copy", "-map", "0:v:0", "-map", "1:a:0", dubmain], check=True)
                append_outro(dubmain, a.outro, out)
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
