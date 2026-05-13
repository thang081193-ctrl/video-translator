"""Brand-pass video transformer — Reels upscale + Andromeda dedup evasion.

Applies V4c-equivalent transforms to any source video, producing a Reels-ready
1080x1920 mp4 with:
- TTS re-dub (Edge TTS, configurable voice — default en-AU-WilliamNeural)
- Demucs htdemucs BGM separation, mixed under TTS (-8dB / 0.4 gain)
- Zoom 1.04x + crop (alters spatial fingerprint)
- Color LUT: saturation +15%, contrast +10%, gamma 0.95, hue +8°
- Corner watermark in Reels safe zone (top-right, x=876, y=288 on 1080x1920)
- Outro card 1.5s at end (dark grey + brand text)

Expected Andromeda dedup score vs source: ~0.40 (passes 0.5 threshold).

Usage:
    from pipeline.brand_pass import brand_pass_video
    brand_pass_video(
        input_path="EN_1205001.mp4",
        output_path="EN_1205001_branded.mp4",
        transcript="Just upload a 2D floor plan...",  # optional — auto via Whisper if None
    )
"""
from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
import tempfile

from pipeline.audio import extract_audio
from pipeline.dub.separator import separate_audio
from pipeline.logger import get_logger
from pipeline.transcribe import transcribe

log = get_logger("BrandPass")

# Reels safe-zone constants
W, H = 1080, 1920
TOP_SAFE = int(H * 0.14)   # 268
SIDE_SAFE = int(W * 0.06)  # 64

# Default brand-pass parameters
DEFAULT_VOICE = "en-AU-WilliamNeural"
DEFAULT_WATERMARK = "DecoAI"
DEFAULT_OUTRO_TITLE = "DecoAI"
DEFAULT_OUTRO_SUB = "Free AI Home Design"
DEFAULT_OUTRO_DUR = 1.5
DEFAULT_BGM_VOL = 0.4

# Find system font
_FONT = None
for _f in [r"C:\Windows\Fonts\seguisb.ttf", r"C:\Windows\Fonts\segoeui.ttf",
           r"C:\Windows\Fonts\arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]:
    if os.path.exists(_f):
        _FONT = _f
        break
if not _FONT:
    raise RuntimeError("No system font found for drawtext filter")
FONT_FF = _FONT.replace("\\", "/").replace(":", "\\:")


def _ffprobe_duration(path: str) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True, check=True,
    )
    return float(r.stdout.strip())


async def _generate_tts(text: str, voice: str, output_path: str) -> None:
    import edge_tts
    comm = edge_tts.Communicate(text, voice, rate="+0%")
    await comm.save(output_path)


def _transcribe_video(video_path: str, work_root: str | None = None) -> str:
    """Extract audio + Whisper transcribe → return joined transcript text."""
    work = tempfile.mkdtemp(prefix="brandpass_tx_", dir=work_root) if work_root else tempfile.mkdtemp(prefix="brandpass_tx_")
    try:
        audio = extract_audio(video_path, work)
        segments, _ = transcribe(audio, model_name="small", source_lang="en", use_cache=False)
        text = " ".join(s["text"].strip() for s in segments).strip()
        return text
    finally:
        shutil.rmtree(work, ignore_errors=True)


def brand_pass_video(
    input_path: str,
    output_path: str,
    *,
    transcript: str | None = None,
    voice: str = DEFAULT_VOICE,
    watermark_text: str = DEFAULT_WATERMARK,
    outro_title: str = DEFAULT_OUTRO_TITLE,
    outro_subtitle: str = DEFAULT_OUTRO_SUB,
    outro_duration: float = DEFAULT_OUTRO_DUR,
    bgm_volume: float = DEFAULT_BGM_VOL,
    work_root: str | None = None,
) -> str:
    """Apply V4c brand-pass to input video, write Reels-ready output.

    Steps:
      1. Transcribe (Whisper) if transcript not provided.
      2. Extract source audio.
      3. Demucs separate → BGM (no_vocals).
      4. Generate TTS for re-dub.
      5. Mix TTS + BGM, pad to (src_dur + outro_dur).
      6. ffmpeg: source [zoom+color+watermark] → concat outro card → mux mixed audio.

    Returns output_path on success.
    """
    if not os.path.isfile(input_path):
        raise FileNotFoundError(input_path)
    os.makedirs(os.path.dirname(os.path.abspath(output_path)) or ".", exist_ok=True)

    if work_root:
        os.makedirs(work_root, exist_ok=True)
        work = tempfile.mkdtemp(prefix="brandpass_", dir=work_root)
    else:
        work = tempfile.mkdtemp(prefix="brandpass_")
    log.info(f"Brand-pass: {os.path.basename(input_path)} → {os.path.basename(output_path)}")
    try:
        # 1. Transcript
        if transcript is None:
            log.info("Transcribing (Whisper small) ...")
            transcript = _transcribe_video(input_path, work_root=work_root)
        log.info(f"Transcript: {transcript[:80]}{'...' if len(transcript) > 80 else ''}")

        # 2. Extract source audio (44.1k stereo for Demucs)
        src_audio_dir = os.path.join(work, "src_audio")
        os.makedirs(src_audio_dir, exist_ok=True)
        log.info("Extracting source audio ...")
        from pipeline.audio import extract_audio_hq
        src_audio = extract_audio_hq(input_path, src_audio_dir)

        # 3. Demucs separate
        log.info("Demucs htdemucs separating BGM ...")
        demucs_dir = os.path.join(work, "demucs")
        os.makedirs(demucs_dir, exist_ok=True)
        stems = separate_audio(src_audio, demucs_dir, model="htdemucs")
        bgm = stems["no_vocals"]

        # 4. TTS
        log.info(f"TTS Edge ({voice}) ...")
        tts_audio = os.path.join(work, "tts.mp3")
        asyncio.run(_generate_tts(transcript, voice, tts_audio))

        # 5. Mix TTS + BGM
        src_dur = _ffprobe_duration(input_path)
        total_dur = src_dur + outro_duration
        mixed_audio = os.path.join(work, "mixed.m4a")
        log.info(f"Mixing TTS over BGM (BGM vol={bgm_volume}) → {total_dur:.2f}s ...")
        r = subprocess.run(
            ["ffmpeg", "-y", "-i", tts_audio, "-i", bgm,
             "-filter_complex",
             f"[0:a]volume=1.0,apad[v0];"
             f"[1:a]volume={bgm_volume},apad[v1];"
             f"[v0][v1]amix=inputs=2:duration=first:dropout_transition=0,atrim=duration={total_dur}[aout]",
             "-map", "[aout]", "-c:a", "aac", "-b:a", "192k", mixed_audio],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            raise RuntimeError(f"ffmpeg mix failed (exit {r.returncode}): {r.stderr[-800:]}")

        # 6. Video transforms (3 steps for reliability)
        wm_x = W - SIDE_SAFE - 140
        wm_y = TOP_SAFE + 20
        drawtext_wm = (
            f"drawtext=fontfile='{FONT_FF}':text='{watermark_text}':"
            f"fontsize=36:fontcolor=white@0.6:borderw=2:bordercolor=black@0.4:"
            f"x={wm_x}:y={wm_y}"
        )
        color = "eq=saturation=1.15:contrast=1.10:gamma=0.95,hue=h=8"
        zoom_crop = f"scale={int(W * 1.04)}:{int(H * 1.04)},crop={W}:{H}"

        body = os.path.join(work, "body.mp4")
        log.info("Encoding transformed body (zoom + color + watermark) ...")
        subprocess.run(
            ["ffmpeg", "-y", "-i", input_path,
             "-vf", f"{zoom_crop},{color},{drawtext_wm}",
             "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-an", body],
            capture_output=True, check=True, text=True,
        )

        outro = os.path.join(work, "outro.mp4")
        log.info("Generating outro card ...")
        subprocess.run(
            ["ffmpeg", "-y",
             "-f", "lavfi", "-i", f"color=c=0x1a1a1a:s={W}x{H}:d={outro_duration}:r=30",
             "-vf",
             f"drawtext=fontfile='{FONT_FF}':text='{outro_title}':"
             f"fontsize=110:fontcolor=white:x=(w-text_w)/2:y=(h-text_h)/2-60,"
             f"drawtext=fontfile='{FONT_FF}':text='{outro_subtitle}':"
             f"fontsize=48:fontcolor=0xcfcfcf:x=(w-text_w)/2:y=(h-text_h)/2+60",
             "-c:v", "libx264", "-preset", "fast", "-crf", "20", outro],
            capture_output=True, check=True, text=True,
        )

        concat_list = os.path.join(work, "list.txt")
        with open(concat_list, "w", encoding="utf-8") as f:
            f.write(f"file '{body}'\nfile '{outro}'\n")

        log.info("Concat + mux mixed audio → final ...")
        subprocess.run(
            ["ffmpeg", "-y",
             "-f", "concat", "-safe", "0", "-i", concat_list,
             "-i", mixed_audio,
             "-map", "0:v", "-map", "1:a",
             "-c:v", "libx264", "-preset", "fast", "-crf", "20",
             "-c:a", "copy",
             "-movflags", "+faststart",
             output_path],
            capture_output=True, check=True, text=True,
        )

        log.info(f"Brand-pass output: {output_path} ({os.path.getsize(output_path) // 1024} KB)")
        return output_path
    finally:
        shutil.rmtree(work, ignore_errors=True)
