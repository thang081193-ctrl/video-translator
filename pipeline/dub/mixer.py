"""Audio mixing, concatenation, and video dubbing."""

import asyncio
import os
import subprocess

from pipeline.audio import check_ffmpeg
from pipeline.config import cfg
from pipeline.errors import FatalError
from pipeline.logger import get_logger
from pipeline.utils import temp_dir

from .separator import get_audio_duration, separate_audio
from .tts import (
    _batch_tts,
    _generate_silence,
    adjust_speed,
    get_voice_for_lang,
)

log = get_logger("Dub")


def _generate_tts_pieces(
    segments: list[dict],
    voice: str,
    tts_dir: str,
) -> list[tuple[float, float, str]]:
    """Generate TTS audio for each segment and speed-adjust to fit timing.

    Runs edge-tts concurrently, then applies atempo sequentially. All files
    are written inside `tts_dir`. Returns a list of (start, end, path) tuples
    for segments that produced audio; empty segments and zero-duration
    segments are skipped.

    Raises RuntimeError if zero TTS pieces were produced (will become
    FatalError in P5.1).
    """
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

    audio_pieces: list[tuple[float, float, str]] = []
    if tts_tasks:
        log.info(f"Generating TTS for {len(tts_tasks)} segments concurrently...")
        asyncio.run(_batch_tts(tts_tasks, voice))
        log.info("TTS done — speed adjusting...")

        for i, seg_start, seg_end, target_duration, _, tts_raw, tts_adjusted in tts_tasks:
            adjust_speed(tts_raw, target_duration, tts_adjusted)
            audio_pieces.append((seg_start, seg_end, tts_adjusted))

    if not audio_pieces:
        raise FatalError("No TTS segments generated")

    return audio_pieces


def _collect_concat_pieces(
    audio_pieces: list[tuple[float, float, str]],
    video_duration: float | None,
    tts_dir: str,
) -> list[str]:
    """Interleave TTS segments with silence gaps to match video timing.

    Returns an ordered list of audio file paths (TTS + silence fillers) ready
    for ffmpeg concat. All silence files are written inside `tts_dir`.
    """
    concat_pieces: list[str] = []

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

    return concat_pieces


def _ffmpeg_concat(concat_pieces: list[str], tts_dir: str) -> str:
    """Write an ffmpeg concat list and produce a single WAV track.

    Returns the path to `dubbed_raw.wav` inside `tts_dir`.
    """
    log.info("Concatenating TTS segments...")
    concat_list_path = os.path.join(tts_dir, "concat_list.txt")
    with open(concat_list_path, "w", encoding="utf-8") as f:
        for piece in concat_pieces:
            escaped = piece.replace("\\", "/").replace("'", "'\\''")
            f.write(f"file '{escaped}'\n")

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
    return dubbed_raw


def _mix_voice_and_bgm(
    dubbed_raw: str,
    bgm_source: str,
    loop_bgm: bool,
    audio_mode: str,
    bgm_volume: float,
    output_path: str,
) -> None:
    """Mix dubbed voice track with background music into final output file.

    bgm_volume is 0.05–0.50 from the slider. For keep_original_bgm mode we
    want BGM close to its original loudness, so we scale it higher (the
    slider acts as a relative mix ratio). Voice is kept at a comfortable
    boost above BGM.
    """
    log.info(f"Mixing with background music (volume={bgm_volume:.0%})...")
    bgm_input_args = ["-stream_loop", "-1", "-i", bgm_source] if loop_bgm else ["-i", bgm_source]

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

    Scratch directories (_tts_temp, _demucs_temp) are managed by temp_dir()
    context managers — guaranteed cleanup on both success and exception paths.
    """
    check_ffmpeg()
    voice = get_voice_for_lang(lang, custom_voice)

    if audio_mode == "custom_bgm":
        if not bgm_path or not os.path.isfile(bgm_path):
            raise FatalError(f"Background music file not found: {bgm_path}")
    elif audio_mode == "keep_original_bgm":
        if not original_audio_path or not os.path.isfile(original_audio_path):
            raise FatalError("Original audio file required for keep_original_bgm mode")

    with temp_dir("tts", base_dir=output_dir) as tts_dir:
        audio_pieces = _generate_tts_pieces(segments, voice, tts_dir)
        concat_pieces = _collect_concat_pieces(audio_pieces, video_duration, tts_dir)
        dubbed_raw = _ffmpeg_concat(concat_pieces, tts_dir)

        if audio_mode == "keep_original_bgm":
            log.info("Separating original audio (Demucs)...")
            with temp_dir("demucs", base_dir=output_dir) as demucs_dir:
                stems = separate_audio(original_audio_path, demucs_dir)
                _mix_voice_and_bgm(
                    dubbed_raw, stems["no_vocals"], loop_bgm=False,
                    audio_mode=audio_mode, bgm_volume=bgm_volume, output_path=output_path,
                )
        else:
            _mix_voice_and_bgm(
                dubbed_raw, bgm_path, loop_bgm=True,
                audio_mode=audio_mode, bgm_volume=bgm_volume, output_path=output_path,
            )

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
