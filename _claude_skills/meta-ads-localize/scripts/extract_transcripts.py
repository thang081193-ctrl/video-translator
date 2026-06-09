#!/usr/bin/env python3
"""Dub phase 1: Whisper transcripts + 1080x1920 downscale + Demucs cache + EN-final-with-outro.

For the first --limit videos in <root>/<src-angle>/EN/:
  - downscale -> _dub_cache/<stem>/main.mp4 (1080x1920, original audio, NO outro)
  - Demucs    -> _dub_cache/<stem>/no_vocals.wav
  - Whisper   -> segments
  - EN final  = main + outro -> overwrites <src-angle>/EN/<file>  (English-market ready)
Writes _dub_cache/transcripts.json [{stem,file,segments:[{id,start,end,text}]}].
Claude then authors _dub_cache/translations.json; apply_dub.py builds the dubs.

Idempotent: a stem with an existing main.mp4 is reused (no double-outro / re-Demucs).
NO end-card trim (the detector false-positives on continuous-VO videos).
Usage: python extract_transcripts.py --root <folder> [--src-angle STORAGE_VO]
       [--outro <outro.mp4>] [--limit 12] [--whisper small]
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time

VT = os.environ.get("VIDEO_TRANSLATOR_ROOT", r"D:/Dev/Tools/Video Translator")
sys.path.insert(0, VT)


def downscale(src, out, crf=23):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", src, "-vf", "scale=1080:1920",
                    "-c:v", "libx264", "-crf", str(crf), "-preset", "fast", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-ar", "44100", "-b:a", "192k", out], check=True)


def append_outro(main, outro, out, crf=23):
    """Concat main (1080x1920) + outro, normalized to 30fps. If no outro, just copy main."""
    if not outro or not os.path.isfile(outro):
        shutil.copy(main, out)
        return
    fc = ("[0:v]scale=1080:1920,fps=30,setsar=1,format=yuv420p[v0];"
          "[0:a]aformat=sample_rates=44100:channel_layouts=stereo[a0];"
          "[1:v]scale=1080:1920,fps=30,setsar=1,format=yuv420p[v1];"
          "[1:a]aformat=sample_rates=44100:channel_layouts=stereo[a1];"
          "[v0][a0][v1][a1]concat=n=2:v=1:a=1[v][a]")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", main, "-i", outro,
                    "-filter_complex", fc, "-map", "[v]", "-map", "[a]",
                    "-c:v", "libx264", "-crf", str(crf), "-preset", "fast",
                    "-c:a", "aac", "-b:a", "192k", out], check=True)


def _cached_segments(sdir):
    for f in os.listdir(sdir):
        if f.startswith("vocals.") and f.endswith(".transcript.json"):
            return json.load(open(os.path.join(sdir, f), encoding="utf-8")).get("segments")
    return None


def main():
    from dotenv import load_dotenv
    load_dotenv(os.path.join(VT, ".env"))
    from pipeline.logger import setup_logging
    setup_logging(mode="cli")
    from pipeline.audio import extract_audio_hq
    from pipeline.transcribe import transcribe
    from pipeline.dub import separate_audio

    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--src-angle", default="STORAGE_VO")
    ap.add_argument("--outro", default=None)
    ap.add_argument("--limit", type=int, default=12)
    ap.add_argument("--whisper", default="small")
    a = ap.parse_args()

    en_dir = os.path.join(a.root, a.src_angle, "EN")
    cache = os.path.join(a.root, "_dub_cache")
    os.makedirs(cache, exist_ok=True)
    name_re = re.compile(r"_EN_\d+\.mp4$")
    srcs = sorted(f for f in os.listdir(en_dir) if name_re.search(f))[:a.limit]
    print(f"processing {len(srcs)} {a.src_angle} sources", flush=True)

    recs, t0 = [], time.time()
    for i, fn in enumerate(srcs, 1):
        en_src = os.path.join(en_dir, fn)
        stem = os.path.splitext(fn)[0]
        sdir = os.path.join(cache, stem)
        os.makedirs(sdir, exist_ok=True)
        main_mp4 = os.path.join(sdir, "main.mp4")
        no_vocals = os.path.join(sdir, "no_vocals.wav")
        reused = os.path.exists(main_mp4)
        st = time.time()

        if not reused:
            downscale(en_src, main_mp4)
        segs = _cached_segments(sdir) if reused else None
        if segs is None or not os.path.exists(no_vocals):
            wav = extract_audio_hq(main_mp4, sdir)
            stems = separate_audio(wav, os.path.join(sdir, "demucs"))
            shutil.copy(stems["no_vocals"], no_vocals)
            segs, _ = transcribe(stems["vocals"], model_name=a.whisper, source_lang="en",
                                 cache_dir=sdir, use_cache=True)
        append_outro(main_mp4, a.outro, en_src)

        recs.append({"stem": stem, "file": fn,
                     "segments": [{"id": j, "start": round(s["start"], 2),
                                   "end": round(s["end"], 2), "text": s["text"].strip()}
                                  for j, s in enumerate(segs)]})
        print(f"[{i}/{len(srcs)}] {stem}: {len(segs)} seg "
              f"({'reused' if reused else 'processed'}, {time.time()-st:.0f}s)", flush=True)

    rest = sorted(f for f in os.listdir(en_dir) if name_re.search(f))[a.limit:]
    if rest:
        spare = os.path.join(a.root, a.src_angle, "_unused_en")
        os.makedirs(spare, exist_ok=True)
        for f in rest:
            shutil.move(os.path.join(en_dir, f), os.path.join(spare, f))
        print(f"moved {len(rest)} unused EN sources -> _unused_en/", flush=True)

    out = os.path.join(cache, "transcripts.json")
    json.dump(recs, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    total = sum(len(r["segments"]) for r in recs)
    print(f"\n{len(recs)} transcripts, {total} segments -> {out} ({(time.time()-t0)/60:.1f} min)"
          f"\nNow author _dub_cache/translations.json (Claude), then run apply_dub.py", flush=True)


if __name__ == "__main__":
    main()
