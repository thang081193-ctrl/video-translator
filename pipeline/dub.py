import asyncio
import json
import os
import subprocess
import tempfile

import edge_tts

from pipeline.audio import check_ffmpeg


# Default voice per language
DEFAULT_VOICES = {
    "vi": "vi-VN-HoaiMyNeural",
    "en": "en-US-JennyNeural",
    "ja": "ja-JP-NanamiNeural",
    "ko": "ko-KR-SunHiNeural",
    "fr": "fr-FR-DeniseNeural",
    "es": "es-ES-ElviraNeural",
    "zh": "zh-CN-XiaoxiaoNeural",
    "de": "de-DE-KatjaNeural",
    "pt": "pt-BR-FranciscaNeural",
    "ru": "ru-RU-SvetlanaNeural",
    "th": "th-TH-PremwadeeNeural",
    "id": "id-ID-GadisNeural",
    "hi": "hi-IN-SwaraNeural",
    "ar": "ar-SA-ZariyahNeural",
    "it": "it-IT-ElsaNeural",
}


def get_voice_for_lang(lang: str, custom_voice: str | None = None) -> str:
    """Get edge-tts voice ID for a language code."""
    if custom_voice:
        return custom_voice
    voice = DEFAULT_VOICES.get(lang)
    if not voice:
        raise ValueError(
            f"No default voice for language '{lang}'. "
            f"Use --tts-voice to specify one. Supported: {', '.join(sorted(DEFAULT_VOICES.keys()))}"
        )
    return voice


def get_audio_duration(path: str) -> float:
    """Get audio duration in seconds using ffprobe."""
    result = subprocess.run(
        [
            "ffprobe", "-v", "quiet",
            "-print_format", "json",
            "-show_entries", "format=duration",
            path,
        ],
        capture_output=True, text=True, check=True,
    )
    data = json.loads(result.stdout)
    return float(data["format"]["duration"])


async def _generate_tts(text: str, voice: str, output_path: str):
    """Generate TTS audio for a single text using edge-tts."""
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(output_path)


def generate_tts_segment(text: str, voice: str, output_path: str):
    """Sync wrapper for edge-tts async generation."""
    asyncio.run(_generate_tts(text, voice, output_path))


def adjust_speed(input_path: str, target_duration: float, output_path: str) -> str:
    """
    Speed up audio to fit within target_duration and normalize to consistent format.
    Converts to WAV 24kHz mono, applies atempo if needed, adds fade in/out.
    Returns output_path.
    """
    actual_duration = get_audio_duration(input_path)

    # Build filter chain: always normalize format + add fade
    filters = []

    # Speed adjustment if TTS is longer than target
    if actual_duration > target_duration:
        ratio = actual_duration / target_duration
        r = ratio
        while r > 2.0:
            filters.append("atempo=2.0")
            r /= 2.0
        filters.append(f"atempo={r:.4f}")

    # Fade in/out to avoid clicks at segment boundaries
    fade_dur = 0.015  # 15ms fade
    filters.append(f"afade=t=in:d={fade_dur}")
    filters.append(f"afade=t=out:st={max(0, target_duration - fade_dur):.3f}:d={fade_dur}")

    filter_str = ",".join(filters)

    subprocess.run(
        [
            "ffmpeg", "-y",
            "-i", input_path,
            "-filter:a", filter_str,
            "-ar", "24000", "-ac", "1",
            "-c:a", "pcm_s16le",
            output_path,
        ],
        capture_output=True, text=True, check=True,
    )
    return output_path


def _generate_silence(duration: float, output_path: str):
    """Generate a silent audio file of given duration."""
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi",
            "-i", f"anullsrc=r=24000:cl=mono",
            "-t", f"{duration:.3f}",
            "-c:a", "pcm_s16le",
            output_path,
        ],
        capture_output=True, text=True, check=True,
    )


def build_dubbed_audio(
    segments: list[dict],
    lang: str,
    bgm_path: str,
    output_path: str,
    output_dir: str,
    custom_voice: str | None = None,
) -> str:
    """
    Build complete dubbed audio track:
    1. Generate TTS for each segment
    2. Speed-adjust to fit subtitle timing
    3. Concat with silence gaps
    4. Mix with background music

    Returns path to final audio file.
    """
    check_ffmpeg()
    voice = get_voice_for_lang(lang, custom_voice)

    if not os.path.isfile(bgm_path):
        raise FileNotFoundError(f"Background music file not found: {bgm_path}")

    tts_dir = os.path.join(output_dir, "_tts_temp")
    os.makedirs(tts_dir, exist_ok=True)

    # Step 1: Generate TTS + speed adjust for each segment
    audio_pieces = []  # list of (start_time, audio_path)

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

        # Generate TTS
        print(f"    Segment {i+1}/{len(segments)}: TTS generating...", end="", flush=True)
        generate_tts_segment(text, voice, tts_raw)

        # Speed adjust to fit timing
        adjust_speed(tts_raw, target_duration, tts_adjusted)
        print(" done")

        audio_pieces.append((seg_start, seg_end, tts_adjusted))

    if not audio_pieces:
        raise RuntimeError("No TTS segments generated")

    # Step 2: Build full audio track with silence gaps
    print("  Concatenating TTS segments...")
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
            "-ar", "24000",
            "-ac", "1",
            dubbed_raw,
        ],
        capture_output=True, text=True, check=True,
    )

    # Step 3: Mix with background music
    print("  Mixing with background music...")
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-i", dubbed_raw,
            "-stream_loop", "-1", "-i", bgm_path,
            "-filter_complex",
            "[0]volume=1.8[voice];"
            "[1]volume=0.10[bg];"
            "[voice][bg]amix=inputs=2:duration=first:dropout_transition=0,alimiter=limit=0.95:level=false",
            "-c:a", "aac", "-b:a", "192k",
            output_path,
        ],
        capture_output=True, text=True, check=True,
    )

    print(f"  Dubbed audio: {output_path}")
    return output_path


def dub_video(video_path: str, audio_path: str, output_path: str) -> str:
    """
    Replace video audio with dubbed audio.
    Copies video stream (no re-encode), replaces audio.
    """
    check_ffmpeg()
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-i", video_path,
            "-i", audio_path,
            "-c:v", "copy",
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-shortest",
            output_path,
        ],
        capture_output=True, text=True, check=True,
    )
    print(f"  Output video: {output_path}")
    return output_path
