"""TTS generation, speed adjustment, and silence generation."""

import asyncio
import os
import subprocess
import sys

import edge_tts

from pipeline.config import cfg
from pipeline.errors import FatalError
from pipeline.languages import DEFAULT_VOICES
from pipeline.logger import get_logger

from .separator import get_audio_duration

log = get_logger("Dub")

# DEFAULT_VOICES is re-exported from pipeline.languages (single source of truth).
# Do not define a parallel dict here. To add a voice, edit pipeline/languages.py.


def get_voice_for_lang(lang: str, custom_voice: str | None = None) -> str:
    """Get edge-tts voice ID for a language code."""
    if custom_voice:
        return custom_voice
    voice = DEFAULT_VOICES.get(lang)
    if not voice:
        raise FatalError(
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
    fade_dur = cfg.dub.fade_duration
    filters.append(f"afade=t=in:d={fade_dur}")
    filters.append(f"afade=t=out:st={max(0, target_duration - fade_dur):.3f}:d={fade_dur}")

    filter_str = ",".join(filters)

    subprocess.run(
        [
            "ffmpeg", "-y",
            "-i", input_path,
            "-filter:a", filter_str,
            "-ar", str(cfg.tts.output_sample_rate), "-ac", str(cfg.tts.output_channels),
            "-c:a", "pcm_s16le",
            output_path,
        ],
        capture_output=True, text=True, check=True, timeout=cfg.ffmpeg.timeout_default,
    )
    return output_path


def _generate_silence(duration: float, output_path: str):
    """Generate a silent audio file of given duration."""
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi",
            "-i", f"anullsrc=r={cfg.tts.output_sample_rate}:cl=mono",
            "-t", f"{duration:.3f}",
            "-c:a", "pcm_s16le",
            output_path,
        ],
        capture_output=True, text=True, check=True, timeout=cfg.ffmpeg.timeout_default,
    )


async def _batch_tts(tts_tasks: list[tuple], voice: str):
    """Generate TTS for all segments concurrently with retry logic."""
    sem = asyncio.Semaphore(cfg.tts.concurrency)

    async def _one(text, voice, path):
        async with sem:
            for attempt in range(cfg.tts.retry_attempts):
                try:
                    c = edge_tts.Communicate(text, voice)
                    await asyncio.wait_for(c.save(path), timeout=cfg.tts.timeout)
                    return
                except (asyncio.TimeoutError, Exception) as e:
                    if attempt == cfg.tts.retry_attempts - 1:
                        # Retries exhausted — honest FatalError (don't re-raise as
                        # Transient, that would invite outer retry loops to try again).
                        raise FatalError(f"TTS failed after {cfg.tts.retry_attempts} retries: {e}") from e
                    await asyncio.sleep(1 * (attempt + 1))

    await asyncio.gather(*[
        _one(t[4], voice, t[5]) for t in tts_tasks
    ])
