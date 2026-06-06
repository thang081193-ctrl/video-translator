#!/usr/bin/env python3
"""Dub video(s) into a target language, keeping the ORIGINAL BGM, output 1080x1920.

Composable so a batch can share the expensive per-source steps across langs:
  prepare_source()  -> extract HQ audio, Demucs separate, Whisper transcribe  [once/source]
  dub_lang()        -> translate, Edge-TTS over original BGM, mux+downscale     [per lang]
  mux_downscale()   -> scale to 1080x1920 + h264 CRF, mux dubbed audio
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _log(t0, msg):
    print(f"[{time.time()-t0:6.1f}s] {msg}", flush=True)


def prepare_source(src, work, whisper_model="small", source_lang="en"):
    """Heavy per-source steps (shared across target langs)."""
    from pipeline.audio import extract_audio_hq
    from pipeline.transcribe import transcribe
    from pipeline.dub import separate_audio
    wav = extract_audio_hq(src, work)
    demucs_dir = os.path.join(work, "demucs")
    os.makedirs(demucs_dir, exist_ok=True)
    stems = separate_audio(wav, demucs_dir)
    segs, det = transcribe(stems["vocals"], model_name=whisper_model,
                           source_lang=source_lang, cache_dir=work, use_cache=False)
    return stems["no_vocals"], segs, det


def mux_downscale(src, audio, out, crf=23, scale="1080:1920", preset="fast"):
    """Mux dubbed audio over src, downscale video to `scale`, re-encode h264 CRF."""
    os.makedirs(os.path.dirname(out), exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", src, "-i", audio,
         "-vf", f"scale={scale}", "-c:v", "libx264", "-crf", str(crf),
         "-preset", preset, "-pix_fmt", "yuv420p",
         "-map", "0:v:0", "-map", "1:a:0", "-c:a", "aac", "-b:a", "192k", out],
        check=True,
    )
    return out


def dub_lang(src, segs, no_vocals, lang, out, work, crf=23, scale="1080:1920"):
    """Per-lang: translate -> TTS over original BGM -> mux+downscale."""
    from pipeline.translate import translate_segments
    from pipeline.dub import build_dubbed_audio
    trans = translate_segments(segs, source_lang="en", target_lang=lang, batch_size=20,
                               cache_path=os.path.join(work, f"tr_{lang}.json"), use_cache=False)
    dubbed = os.path.join(work, f"dubbed_{lang}.m4a")
    build_dubbed_audio(trans, lang=lang, output_path=dubbed, output_dir=work,
                       custom_voice=None, audio_mode="keep_original_bgm",
                       pre_separated_no_vocals_path=no_vocals)
    mux_downscale(src, dubbed, out, crf=crf, scale=scale)
    return trans


def dub_one(src, target_lang, out_path, source_lang="en", whisper_model="small",
            crf=23, scale="1080:1920"):
    t0 = time.time()
    work = tempfile.mkdtemp(prefix="dub_")
    try:
        _log(t0, f"prepare {os.path.basename(src)} (extract+Demucs+Whisper)")
        no_vocals, segs, det = prepare_source(src, work, whisper_model, source_lang)
        if not segs:
            _log(t0, "NO SPEECH — skip")
            return None
        _log(t0, f"  {len(segs)} seg ({det}); -> {target_lang} (translate+TTS+downscale)")
        trans = dub_lang(src, segs, no_vocals, target_lang, out_path, work, crf, scale)
        _log(t0, f"DONE -> {out_path} ({time.time()-t0:.0f}s, {os.path.getsize(out_path)//1024}KB)")
        for s in trans[:6]:
            print(f"   {str(s.get('text',''))[:42]!r} -> "
                  f"{str(s.get('translated_text',''))[:42]!r}", flush=True)
        return out_path
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    from pipeline.logger import setup_logging
    setup_logging(mode="cli")

    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("-t", "--target", required=True)
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("-s", "--source", default="en")
    ap.add_argument("--whisper", default="small")
    ap.add_argument("--crf", type=int, default=23)
    ap.add_argument("--scale", default="1080:1920")
    a = ap.parse_args()
    dub_one(a.src, a.target, a.out, source_lang=a.source, whisper_model=a.whisper,
            crf=a.crf, scale=a.scale)
