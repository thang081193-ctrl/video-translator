import asyncio
import json
import os
import shutil
import subprocess
import sys

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


def _get_python_exe() -> str:
    """Get python.exe (not pythonw.exe) for subprocess calls that need stdout."""
    exe = sys.executable
    if exe.endswith("pythonw.exe"):
        candidate = exe.replace("pythonw.exe", "python.exe")
        if os.path.isfile(candidate):
            return candidate
    return exe


def separate_audio(audio_path: str, output_dir: str, model: str = "htdemucs") -> dict[str, str]:
    """
    Separate audio into vocals and accompaniment using Demucs.
    Returns dict with paths: {"vocals": ..., "no_vocals": ...}
    """
    demucs_out = os.path.join(output_dir, "_demucs_temp")
    os.makedirs(demucs_out, exist_ok=True)

    # Monkey-patch torchaudio.save to use soundfile (torchcodec broken on Windows)
    import torchaudio
    _original_save = torchaudio.save
    try:
        import soundfile as sf

        def _sf_save(filepath, src, sample_rate, **kwargs):
            sf.write(str(filepath), src.cpu().numpy().T, sample_rate)

        torchaudio.save = _sf_save
    except ImportError:
        pass  # Fall through to default save

    try:
        from demucs.separate import main as demucs_main
        try:
            demucs_main([
                "--two-stems", "vocals",
                "-n", model,
                "-o", demucs_out,
                audio_path,
            ])
        except RuntimeError:
            print("  GPU OOM during Demucs, retrying on CPU...")
            import torch
            torch.cuda.empty_cache()
            demucs_main([
                "--two-stems", "vocals",
                "-n", model,
                "-d", "cpu",
                "-o", demucs_out,
                audio_path,
            ])
    finally:
        torchaudio.save = _original_save

    base_name = os.path.splitext(os.path.basename(audio_path))[0]
    stem_dir = os.path.join(demucs_out, model, base_name)

    return {
        "vocals": os.path.join(stem_dir, "vocals.wav"),
        "no_vocals": os.path.join(stem_dir, "no_vocals.wav"),
    }


def get_audio_duration(path: str) -> float:
    """Get audio duration in seconds using ffprobe."""
    result = subprocess.run(
        [
            "ffprobe", "-v", "quiet",
            "-print_format", "json",
            "-show_entries", "format=duration",
            path,
        ],
        capture_output=True, text=True, check=True, timeout=300,
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
        capture_output=True, text=True, check=True, timeout=300,
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
        capture_output=True, text=True, check=True, timeout=300,
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
        print(f"  Generating TTS for {len(tts_tasks)} segments concurrently...")

        async def _batch_tts():
            sem = asyncio.Semaphore(5)  # limit concurrent requests
            async def _one(text, voice, path):
                async with sem:
                    for attempt in range(3):
                        try:
                            c = edge_tts.Communicate(text, voice)
                            await asyncio.wait_for(c.save(path), timeout=30)
                            return
                        except (asyncio.TimeoutError, Exception) as e:
                            if attempt == 2:
                                raise RuntimeError(f"TTS failed after 3 retries: {e}")
                            await asyncio.sleep(1 * (attempt + 1))
            await asyncio.gather(*[
                _one(t[4], voice, t[5]) for t in tts_tasks
            ])

        asyncio.run(_batch_tts())
        print(f"  TTS done — speed adjusting...")

        # Speed adjust sequentially (fast ffmpeg calls)
        for i, seg_start, seg_end, target_duration, _, tts_raw, tts_adjusted in tts_tasks:
            adjust_speed(tts_raw, target_duration, tts_adjusted)
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
            "-ar", "24000",
            "-ac", "1",
            dubbed_raw,
        ],
        capture_output=True, text=True, check=True, timeout=300,
    )

    # Step 3: Mix with background music
    if audio_mode == "keep_original_bgm":
        print("  Separating original audio (Demucs)...")
        stems = separate_audio(original_audio_path, output_dir)
        bgm_source = stems["no_vocals"]
        loop_bgm = False
    else:
        bgm_source = bgm_path
        loop_bgm = True

    print(f"  Mixing with background music (volume={bgm_volume:.0%})...")
    bgm_input_args = ["-stream_loop", "-1", "-i", bgm_source] if loop_bgm else ["-i", bgm_source]

    # bgm_volume is 0.05–0.50 from the slider.
    # For keep_original_bgm mode we want BGM close to its original loudness,
    # so we scale it higher (the slider acts as a relative mix ratio).
    # Voice is kept at a comfortable boost above BGM.
    if audio_mode == "keep_original_bgm":
        bgm_vol = max(bgm_volume * 4.0, 0.5)   # slider 25% → 1.0x (original level)
        voice_vol = 2.5
    else:
        bgm_vol = bgm_volume * 2.0              # custom BGM stays quieter
        voice_vol = 1.8

    subprocess.run(
        [
            "ffmpeg", "-y",
            "-i", dubbed_raw,
            *bgm_input_args,
            "-filter_complex",
            f"[0]volume={voice_vol:.2f}[voice];"
            f"[1]volume={bgm_vol:.2f}[bg];"
            f"[voice][bg]amix=inputs=2:duration=longest:dropout_transition=0:normalize=0,alimiter=limit=0.95:level=false",
            "-c:a", "aac", "-b:a", "192k",
            output_path,
        ],
        capture_output=True, text=True, check=True, timeout=300,
    )

    # Cleanup demucs temp
    demucs_temp = os.path.join(output_dir, "_demucs_temp")
    if os.path.isdir(demucs_temp):
        shutil.rmtree(demucs_temp, ignore_errors=True)

    print(f"  Dubbed audio: {output_path}")
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
                "-c:a", "aac", "-b:a", "192k",
                padded_path,
            ],
            capture_output=True, text=True, check=True,
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
        capture_output=True, text=True, check=True, timeout=300,
    )

    # Cleanup padded file
    padded = output_path + ".padded.m4a"
    if os.path.isfile(padded):
        os.remove(padded)

    print(f"  Output video: {output_path}")
    return output_path
