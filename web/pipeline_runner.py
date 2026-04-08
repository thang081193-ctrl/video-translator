"""Shared pipeline execution logic — used by both CLI and Web.

Extracts the common pipeline steps from web_app.py's _run_pipeline
into a reusable class.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from typing import Callable

from pipeline.audio import extract_audio, extract_audio_hq, get_video_info, check_ffmpeg
from pipeline.transcribe import transcribe
from pipeline.translate import translate_segments
from pipeline.subtitle import generate_srt
from pipeline.burn import burn_subtitles, burn_ocr_overlay, burn_with_overlays
from pipeline.dub import build_dubbed_audio, dub_video, get_audio_duration, DEFAULT_VOICES
from pipeline.config import cfg
from pipeline.gpu_state import empty_cuda_cache
from pipeline.logger import get_logger

log = get_logger("Pipeline")


@dataclass
class PipelineParams:
    """All parameters needed to run the translation pipeline."""
    video_path: str
    target_lang: str
    source_lang: str | None = None
    whisper_model: str = "medium"
    burn: bool = False
    dub: bool = False
    bgm_path: str | None = None
    tts_voice: str | None = None
    batch_size: int = 20
    audio_mode: str = "keep_original_bgm"
    bgm_volume: float = 0.25
    translate_ocr: bool = False
    ocr_quality: str = "fast"
    use_cache: bool = True
    output_dir: str | None = None


@dataclass
class PipelineResult:
    """Result of a pipeline run."""
    srt_path: str | None = None
    dubbed_video: str | None = None
    burned_video: str | None = None
    files: list[dict] = field(default_factory=list)
    segments_count: int = 0
    detected_lang: str = ""


ProgressCallback = Callable[[int, int, str, int], None]
"""(step, total_steps, label, progress_pct) -> None"""


def count_steps(params: PipelineParams) -> int:
    """Calculate total pipeline steps based on params."""
    total = 4  # audio, transcribe, translate, subtitles
    if params.translate_ocr:
        total += 1
    if params.dub:
        total += 2  # TTS + merge
    total += 1  # finalize/burn step
    return total


def run_pipeline(
    params: PipelineParams,
    on_progress: ProgressCallback | None = None,
) -> PipelineResult:
    """
    Execute the full translation pipeline.

    This is the shared implementation used by both CLI and Web.
    """
    check_ffmpeg()
    result = PipelineResult()

    job_dir = params.output_dir or os.path.dirname(os.path.abspath(params.video_path))
    video_base = os.path.splitext(os.path.basename(params.video_path))[0]
    total_steps = count_steps(params)
    current_step = 0

    def progress(label: str, pct: int):
        nonlocal current_step
        current_step += 1
        if on_progress:
            on_progress(current_step, total_steps, label, pct)

    def progress_update(label: str, pct: int):
        """Update progress without incrementing step."""
        if on_progress:
            on_progress(current_step, total_steps, label, pct)

    # Step 1: Extract audio
    progress("Extracting audio", 5)
    wav_path = extract_audio(params.video_path, job_dir)
    progress_update("Extracting audio", 15)

    # Step 2: Transcribe
    progress(f"Transcribing ({params.whisper_model})", 20)
    segments, detected_lang = transcribe(
        wav_path, model_name=params.whisper_model, source_lang=params.source_lang,
        cache_dir=job_dir, use_cache=params.use_cache,
    )
    src_lang = params.source_lang or detected_lang
    result.detected_lang = src_lang
    result.segments_count = len(segments)
    progress_update(f"Transcribing ({params.whisper_model})", 45)

    # Step 3: Translate
    cache_path = os.path.join(job_dir, f"{video_base}.{src_lang}_{params.target_lang}.translated.json")
    progress(f"Translating {src_lang} -> {params.target_lang}", 50)
    translated_segments = translate_segments(
        segments, source_lang=src_lang, target_lang=params.target_lang,
        batch_size=params.batch_size, cache_path=cache_path, use_cache=params.use_cache,
    )
    progress_update(f"Translating {src_lang} -> {params.target_lang}", 65)

    # Step 3.5 (optional): OCR
    ocr_filter = None
    ocr_overlays = None
    if params.translate_ocr:
        progress("Detecting on-screen text (OCR)", 67)
        try:
            from pipeline.ocr import (
                extract_key_frames, detect_text_regions,
                group_persistent_texts, group_persistent_texts_tracked,
                translate_ocr_texts, generate_drawtext_filter,
                generate_overlay_images, inpaint_all_regions,
            )

            video_info = get_video_info(params.video_path)
            frames_dir = os.path.join(job_dir, "_ocr_frames")
            key_frames = extract_key_frames(params.video_path, frames_dir, interval=cfg.ocr.frame_interval)

            progress_update("Running OCR...", 69)
            frame_results = detect_text_regions(key_frames, lang=src_lang)

            ocr_quality = params.ocr_quality
            if ocr_quality == "premium":
                text_groups = group_persistent_texts_tracked(frame_results)
            else:
                text_groups = group_persistent_texts(frame_results)

            if text_groups:
                progress_update("Translating on-screen text...", 72)
                text_groups = translate_ocr_texts(text_groups, src_lang, params.target_lang)

                if ocr_quality == "fast" and params.target_lang in cfg.ocr.drawtext_unsafe_langs:
                    log.info(f"Auto-upgrading OCR quality fast->quality for lang={params.target_lang}")
                    ocr_quality = "quality"

                if ocr_quality == "fast":
                    ocr_filter = generate_drawtext_filter(
                        text_groups, video_info["width"], video_info["height"], params.target_lang
                    )
                elif ocr_quality == "quality":
                    progress_update("Rendering text overlays...", 73)
                    ocr_overlays = generate_overlay_images(
                        text_groups, video_info["width"], video_info["height"],
                        job_dir, params.target_lang
                    )
                elif ocr_quality == "premium":
                    progress_update("Inpainting text regions...", 73)
                    inpaint_patches = inpaint_all_regions(params.video_path, text_groups, frames_dir)
                    progress_update("Rendering text overlays...", 74)
                    ocr_overlays = generate_overlay_images(
                        text_groups, video_info["width"], video_info["height"],
                        job_dir, params.target_lang, inpaint_patches=inpaint_patches,
                    )

            if ocr_quality != "premium":
                shutil.rmtree(frames_dir, ignore_errors=True)
        except ImportError as e:
            log.warning(f"OCR module not available ({e}), skipping...")
        progress_update("OCR complete", 75)

    # Step 4: Generate SRT
    srt_path = os.path.join(job_dir, f"{video_base}.{params.target_lang}.srt")
    progress("Generating subtitles", 76)
    generate_srt(translated_segments, srt_path, text_key="translated_text")
    result.srt_path = srt_path

    has_segments = len(translated_segments) > 0
    if has_segments:
        result.files.append({"name": os.path.basename(srt_path), "type": "srt", "label": "Subtitles (.srt)"})

    # Determine if dubbing is possible
    can_dub = params.dub and has_segments
    video_ext = os.path.splitext(params.video_path)[1]
    base_video = params.video_path

    if can_dub:
        vid_duration = get_audio_duration(params.video_path)

        # Extract HQ audio for Demucs if needed
        original_audio_hq = None
        if params.audio_mode == "keep_original_bgm":
            original_audio_hq = extract_audio_hq(params.video_path, job_dir)

        # Step: TTS + mix
        dubbed_audio_path = os.path.join(job_dir, f"{video_base}.{params.target_lang}.dubbed.m4a")
        label = "Generating TTS + separating audio" if params.audio_mode == "keep_original_bgm" else "Generating TTS audio"
        progress(label, 78)
        build_dubbed_audio(
            translated_segments, lang=params.target_lang,
            output_path=dubbed_audio_path, output_dir=job_dir,
            custom_voice=params.tts_voice, audio_mode=params.audio_mode,
            bgm_path=params.bgm_path, bgm_volume=params.bgm_volume,
            original_audio_path=original_audio_hq, video_duration=vid_duration,
        )
        progress_update(label, 88)

        # Cleanup HQ audio
        if original_audio_hq and os.path.isfile(original_audio_hq):
            os.remove(original_audio_hq)

        # Step: Merge
        output_video = os.path.join(job_dir, f"{video_base}.{params.target_lang}.dubbed{video_ext}")
        progress("Merging dubbed audio", 90)
        dub_video(params.video_path, dubbed_audio_path, output_video)
        result.dubbed_video = output_video
        result.files.append({"name": os.path.basename(output_video), "type": "video", "label": "Dubbed video"})
        base_video = output_video

    elif params.dub and not has_segments:
        current_step += 2  # skip TTS + Merge steps
        progress_update("No speech detected, skipping dubbing", 88)

    # Step: Burn subtitles / OCR overlay
    has_burn_work = (params.burn and has_segments) or ocr_filter or ocr_overlays
    if has_burn_work:
        final_video = os.path.join(job_dir, f"{video_base}.{params.target_lang}.final{video_ext}")
        progress("Burning subtitles/text overlay", 93)

        if ocr_overlays:
            burn_with_overlays(
                base_video, ocr_overlays, final_video,
                srt_path=srt_path if (params.burn and has_segments) else None,
            )
        elif params.burn and has_segments:
            burn_subtitles(base_video, srt_path, final_video, ocr_filter=ocr_filter)
        elif ocr_filter:
            burn_ocr_overlay(base_video, ocr_filter, final_video)

        label = "Dubbed + Subtitles" if can_dub else "Video with text overlay"
        result.burned_video = final_video
        result.files.append({"name": os.path.basename(final_video), "type": "video", "label": label})
    else:
        progress("Finalizing", 95)

    # If no output files were generated, include the original video
    if not result.files:
        result.files.append({
            "name": os.path.basename(params.video_path), "type": "video",
            "label": "Original video (no speech detected)",
        })

    # Cleanup temp files
    try:
        os.remove(wav_path)
    except OSError:
        pass
    for temp_dir in ["_tts_temp", "_demucs_temp", "_ocr_frames", "_ocr_overlays"]:
        temp_path = os.path.join(job_dir, temp_dir)
        if os.path.isdir(temp_path):
            shutil.rmtree(temp_path, ignore_errors=True)

    # Release fragmented VRAM between jobs (P6.A). Best-effort: never fail
    # a completed job because cleanup couldn't run.
    try:
        empty_cuda_cache()
    except Exception:
        pass

    return result
