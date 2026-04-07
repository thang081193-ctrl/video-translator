"""Audio mixing, concatenation, and video dubbing."""

import asyncio
import os
import shutil
import subprocess

from pipeline.audio import check_ffmpeg
from pipeline.config import cfg
from pipeline.logger import get_logger

from .separator import get_audio_duration, separate_audio
from .tts import (
    _batch_tts,
    _generate_silence,
    adjust_speed,
    get_voice_for_lang,
)

log = get_logger("Dub")


def build_dubbed_audio(
    segments: list[dict],
    lang: str,
    output_path: str,
    output_dir: str,
    custom_voice: str | None = None,
    audio_mode: str = "custom_bgm",
    bgm_path: str | None = None,
    bgm_volume: float = 0.25,
    original_audio_path: str | None = None,
    video_duration: float | None = None,
) -> str:
    """
    Build complete dubbed audio track:
    1. Generate TTS for each segment
    2. Speed-adjust to fit subtitle timing
    3. Concat with silence gaps
    4. Mix with background music

    audio_mode: "keep_original_bgm" or "custom_bgm"
    Returns path to final audio file.
    """
    check_ffmpeg()
    voice = get_voice_for_lang(lang, custom_voice)

    if audio_mode == "custom_bgm":
        if not bgm_path or not os.path.isfile(bgm_path):
            raise FileNotFoundError(f"Background music file not found: {bgm_path}")
    elif audio_mode == "keep_original_bgm":
        if not original_audio_path or not os.path.isfile(original_audio_path):
            raise FileNotFoundError("Original audio file required for keep_original_bgm mode")

    tts_dir = os.path.join(output_dir, "_tts_temp")
    os.makedirs(tts_dir, exist_ok=True)

    # Step 1: Generate TTS + speed adjust for each segment (concurrent)
    audio_pieces = []  # list of (start_time, end_time, audio_path)

    # Prepare segment tasks
    tts_tasks = []
    for i, seg in enumerate(segments):
        text = seg.get("translated_text", seg.get("text", ""))
        if not text.strip():
            continue
        seg_start = seg["start"]
        seg_end = seg["end"]
        target_duration = seg_end - seg_start
        if target_duration <= 0:
            continue
        tts_raw = os.path.join(tts_dir, f"seg_{i:04d}_raw.mp3")
        tts_adjusted = os.path.join(tts_dir, f"seg_{i:04d}.wav")
        tts_tasks.append((i, seg_start, seg_end, target_duration, text, tts_raw, tts_adjusted))

    # Generate all TTS concurrently using async event loop + gather
    if tts_tasks:
        log.info(f"Generating TTS for {len(tts_tasks)} segments concurrently...")

        asyncio.run(_batch_tts(tts_tasks, voice))
        log.info("TTS done — speed adjusting...")

        # Speed adjust sequentially (fast ffmpeg calls)
        for i, seg_start, seg_end, target_duration, _, tts_raw, tts_adjusted in tts_tasks:
            adjust_speed(tts_raw, target_duration, tts_adjusted)
            audio_pieces.append((seg_start, seg_end, tts_adjusted))

    if not audio_pieces:
        raise RuntimeError("No TTS segments generated")

    # Step 2: Build full audio track with silence gaps
    log.info("Concatenating TTS segments...")
    concat_list_path = os.path.join(tts_dir, "concat_list.txt")
    concat_pieces = []

    # Silence before first segment
    if audio_pieces[0][0] > 0.01:
        silence_path = os.path.join(tts_dir, "silence_start.wav")
        _generate_silence(audio_pieces[0][0], silence_path)
        concat_pieces.append(silence_path)

    for idx, (start, end, audio_path) in enumerate(audio_pieces):
        concat_pieces.append(audio_path)

        # Silence gap to next segment
        if idx < len(audio_pieces) - 1:
            next_start = audio_pieces[idx + 1][0]
            gap = next_start - end
            if gap > 0.01:
                silence_path = os.path.join(tts_dir, f"silence_{idx:04d}.wav")
                _generate_silence(gap, silence_path)
                concat_pieces.append(silence_path)

    # Add trailing silence to match full video duration
    last_end = audio_pieces[-1][1]
    total_duration = video_duration or last_end
    if total_duration > last_end + 0.01:
        silence_path = os.path.join(tts_dir, "silence_end.wav")
        _generate_silence(total_duration - last_end, silence_path)
        concat_pieces.append(silence_path)

    # Write concat file
    with open(concat_list_path, "w", encoding="utf-8") as f:
        for piece in concat_pieces:
            escaped = piece.replace("\\", "/").replace("'", "'\\''")
            f.write(f"file '{escaped}'\n")

    # Concat all pieces
    dubbed_raw = os.path.join(tts_dir, "dubbed_raw.wav")
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", concat_list_path,
            "-c:a", "pcm_s16le",
            "-ar", str(cfg.tts.output_sample_rate),
            "-ac", str(cfg.tts.output_channels),
            dubbed_raw,
        ],
        capture_output=True, text=True, check=True, timeout=cfg.ffmpeg.timeout_default,
    )

    # Step 3: Mix with background music
    if audio_mode == "keep_original_bgm":
        log.info("Separating original audio (Demucs)...")
        stems = separate_audio(original_audio_path, output_dir)
        bgm_source = stems["no_vocals"]
        loop_bgm = False
    else:
        bgm_source = bgm_path
        loop_bgm = True

    log.info(f"Mixing with background music (volume={bgm_volume:.0%})...")
    bgm_input_args = ["-stream_loop", "-1", "-i", bgm_source] if loop_bgm else ["-i", bgm_source]

    # bgm_volume is 0.05–0.50 from the slider.
    # For keep_original_bgm mode we want BGM close to its original loudness,
    # so we scale it higher (the slider acts as a relative mix ratio).
    # Voice is kept at a comfortable boost above BGM.
    if audio_mode == "keep_original_bgm":
        bgm_vol = max(bgm_volume * cfg.dub.original_bgm_multiplier, cfg.dub.original_bgm_min)
        voice_vol = cfg.dub.original_voice_vol
    else:
        bgm_vol = bgm_volume * cfg.dub.custom_bgm_multiplier
        voice_vol = cfg.dub.custom_voice_vol

    subprocess.run(
        [
            "ffmpeg", "-y",
            "-i", dubbed_raw,
            *bgm_input_args,
            "-filter_complex",
            f"[0]volume={voice_vol:.2f}[voice];"
            f"[1]volume={bgm_vol:.2f}[bg];"
            f"[voice][bg]amix=inputs=2:duration=longest:dropout_transition=0:normalize=0,alimiter=limit={cfg.dub.limiter_threshold}:level=false",
            "-c:a", "aac", "-b:a", cfg.dub.audio_bitrate,
            output_path,
        ],
        capture_output=True, text=True, check=True, timeout=cfg.ffmpeg.timeout_default,
    )

    # Cleanup demucs temp
    demucs_temp = os.path.join(output_dir, "_demucs_temp")
    if os.path.isdir(demucs_temp):
        shutil.rmtree(demucs_temp, ignore_errors=True)

    log.info(f"Dubbed audio: {output_path}")
    return output_path


def dub_video(video_path: str, audio_path: str, output_path: str) -> str:
    """
    Replace video audio with dubbed audio.
    Keeps full video duration — pads dubbed audio with silence if shorter.
    """
    check_ffmpeg()

    # Get video duration to pad audio if needed
    video_duration = get_audio_duration(video_path)
    audio_duration = get_audio_duration(audio_path)

    if audio_duration < video_duration - 0.1:
        # Pad dubbed audio to match video length
        padded_path = output_path + ".padded.m4a"
        pad_duration = video_duration - audio_duration
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-i", audio_path,
                "-f", "lavfi", "-t", f"{pad_duration:.3f}",
                "-i", "anullsrc=r=44100:cl=stereo",
                "-filter_complex", "[0][1]concat=n=2:v=0:a=1",
                "-c:a", "aac", "-b:a", cfg.dub.audio_bitrate,
                padded_path,
            ],
            capture_output=True, text=True, check=True, timeout=cfg.ffmpeg.timeout_default,
        )
        audio_path = padded_path

    subprocess.run(
        [
            "ffmpeg", "-y",
            "-i", video_path,
            "-i", audio_path,
            "-c:v", "copy",
            "-map", "0:v:0",
            "-map", "1:a:0",
            output_path,
        ],
        capture_output=True, text=True, check=True, timeout=cfg.ffmpeg.timeout_default,
    )

    # Cleanup padded file
    padded = output_path + ".padded.m4a"
    if os.path.isfile(padded):
        os.remove(padded)

    log.info(f"Output video: {output_path}")
    return output_path
