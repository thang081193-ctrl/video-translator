#!/usr/bin/env python3
"""Phase 1 of Claude-translated dubbing (super-saiyan style, EN->FR/TR).

For the first N STORAGE_VO/EN videos:
  - downscale to 1080x1920 -> _dub_cache/<stem>/main.mp4  (cached; no outro; original EN audio)
  - Demucs separate -> _dub_cache/<stem>/no_vocals.wav    (cached BGM for dub)
  - Whisper-transcribe the EN VO -> segments
  - EN final = main.mp4 + DecoAI outro -> STORAGE_VO/EN/<file>  (ZA-ready)
Writes _dub_cache/transcripts.json with empty fr/tr per segment for Claude to fill.

Idempotent: a stem whose _dub_cache/<stem>/main.mp4 already exists is reused
(no re-downscale/Demucs/Whisper, no double outro). main.mp4 is the canonical
no-outro source — the EN final is ALWAYS main + outro, so re-runs are safe.
(No end-card trim: STORAGE_VO has no competitor outro — VO runs to the end.)
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = r"D:/Dev/App Details/Home Decor/Video/home decor 2805"
EN_DIR = os.path.join(ROOT, "STORAGE_VO", "EN")
CACHE = os.path.join(ROOT, "_dub_cache")
OUTRO = r"D:/Dev/App Details/Home Decor/Video/DecoAI_app_outro_animation_202605281741.mp4"
LIMIT = 12
NAME_RE = re.compile(r"^STORAGE_VO_EN_\d+\.mp4$")   # exclude .1080.mp4 / temp junk


def downscale(src, out, crf=23):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", src, "-vf", "scale=1080:1920",
                    "-c:v", "libx264", "-crf", str(crf), "-preset", "fast", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-ar", "44100", "-b:a", "192k", out], check=True)


def append_outro(main, out, crf=23):
    fc = ("[0:v]scale=1080:1920,fps=30,setsar=1,format=yuv420p[v0];"
          "[0:a]aformat=sample_rates=44100:channel_layouts=stereo[a0];"
          "[1:v]scale=1080:1920,fps=30,setsar=1,format=yuv420p[v1];"
          "[1:a]aformat=sample_rates=44100:channel_layouts=stereo[a1];"
          "[v0][a0][v1][a1]concat=n=2:v=1:a=1[v][a]")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", main, "-i", OUTRO,
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
    load_dotenv()
    from pipeline.logger import setup_logging
    setup_logging(mode="cli")
    from pipeline.audio import extract_audio_hq
    from pipeline.transcribe import transcribe
    from pipeline.dub import separate_audio

    os.makedirs(CACHE, exist_ok=True)
    srcs = sorted(f for f in os.listdir(EN_DIR) if NAME_RE.match(f))[:LIMIT]
    print(f"processing {len(srcs)} STORAGE_VO sources", flush=True)

    recs = []
    t0 = time.time()
    for i, fn in enumerate(srcs, 1):
        en_src = os.path.join(EN_DIR, fn)
        stem = os.path.splitext(fn)[0]
        sdir = os.path.join(CACHE, stem)
        os.makedirs(sdir, exist_ok=True)
        main_mp4 = os.path.join(sdir, "main.mp4")
        no_vocals = os.path.join(sdir, "no_vocals.wav")
        st = time.time()
        reused = os.path.exists(main_mp4)

        if not reused:
            downscale(en_src, main_mp4)                  # original 2.5K -> 1080 no-outro

        segs = _cached_segments(sdir) if reused else None
        if segs is None or not os.path.exists(no_vocals):
            wav = extract_audio_hq(main_mp4, sdir)
            stems = separate_audio(wav, os.path.join(sdir, "demucs"))
            shutil.copy(stems["no_vocals"], no_vocals)
            segs, _ = transcribe(stems["vocals"], model_name="small", source_lang="en",
                                 cache_dir=sdir, use_cache=True)

        append_outro(main_mp4, en_src)                   # EN final = main + outro (idempotent)

        recs.append({"stem": stem, "file": fn,
                     "segments": [{"id": j, "start": round(s["start"], 2),
                                   "end": round(s["end"], 2), "text": s["text"].strip(),
                                   "fr": "", "tr": ""} for j, s in enumerate(segs)]})
        print(f"[{i}/{len(srcs)}] {stem}: {len(segs)} seg "
              f"({'reused cache' if reused else 'processed'}, {time.time()-st:.0f}s)", flush=True)

    rest = sorted(f for f in os.listdir(EN_DIR) if NAME_RE.match(f))[LIMIT:]
    if rest:
        spare = os.path.join(ROOT, "STORAGE_VO", "_unused_en")
        os.makedirs(spare, exist_ok=True)
        for f in rest:
            shutil.move(os.path.join(EN_DIR, f), os.path.join(spare, f))
        print(f"moved {len(rest)} unused EN sources -> _unused_en/", flush=True)

    out = os.path.join(CACHE, "transcripts.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(recs, f, ensure_ascii=False, indent=1)
    total = sum(len(r["segments"]) for r in recs)
    print(f"\n{len(recs)} transcripts, {total} segments -> {out} ({(time.time()-t0)/60:.1f} min)", flush=True)


if __name__ == "__main__":
    main()
