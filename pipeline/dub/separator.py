"""Demucs source separation and audio duration utilities."""

import json
import os
import subprocess

from pipeline.config import cfg
from pipeline.errors import DegradedError
from pipeline.logger import get_logger

log = get_logger("Dub")


def separate_audio(audio_path: str, demucs_dir: str, model: str = "htdemucs") -> dict[str, str]:
    """
    Separate audio into vocals and accompaniment using Demucs.

    `demucs_dir` must be an already-created directory owned by the caller
    (typically from a `temp_dir("demucs", ...)` context manager). Demucs
    writes its output tree under this path.

    Returns dict with paths: {"vocals": ..., "no_vocals": ...}. Note: these
    paths live inside `demucs_dir`; read them BEFORE the caller exits its
    context manager or the files will be cleaned up.
    """
    log.info(f"Separating audio with Demucs ({model})")

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
            try:
                demucs_main([
                    "--two-stems", "vocals",
                    "-n", model,
                    "-o", demucs_dir,
                    audio_path,
                ])
            except RuntimeError:
                log.warning("GPU OOM during Demucs, retrying on CPU...")
                import torch
                torch.cuda.empty_cache()
                demucs_main([
                    "--two-stems", "vocals",
                    "-n", model,
                    "-d", "cpu",
                    "-o", demucs_dir,
                    audio_path,
                ])
        except Exception as e:
            # Both GPU and CPU paths failed — surface as DegradedError so
            # callers can skip source separation and fall back (e.g. to
            # custom_bgm mode) instead of aborting the whole job.
            raise DegradedError(
                f"Demucs source separation failed: {e}", feature="demucs"
            ) from e
    finally:
        torchaudio.save = _original_save

    base_name = os.path.splitext(os.path.basename(audio_path))[0]
    stem_dir = os.path.join(demucs_dir, model, base_name)

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
        capture_output=True, text=True, check=True, timeout=cfg.ffmpeg.timeout_default,
    )
    data = json.loads(result.stdout)
    return float(data["format"]["duration"])
