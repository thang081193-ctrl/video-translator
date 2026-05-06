"""Shared pipeline execution logic — used by both CLI and Web.

Multi-target architecture: extract_audio + transcribe (and optionally Demucs
source separation) run ONCE per source video, then translate/TTS/mix/burn
fan out per target language. Reusing the Demucs output across langs is the
single biggest free-tier speedup — ~12s/video × (N_langs - 1) × N_videos.
"""

from __future__ import annotations

import os
import shutil
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Callable

from pipeline.audio import extract_audio, extract_audio_hq, get_video_info, check_ffmpeg
from pipeline.transcribe import transcribe
from pipeline.translate import translate_segments
from pipeline.subtitle import generate_srt
from pipeline.burn import burn_subtitles, burn_ocr_overlay, burn_with_overlays
from pipeline.dub import build_dubbed_audio, dub_video, get_audio_duration, DEFAULT_VOICES
from pipeline.dub.separator import separate_audio
from pipeline.config import cfg
from pipeline.errors import FatalError
from pipeline.gpu_state import empty_cuda_cache, should_use_gpu
from pipeline.logger import get_logger

log = get_logger("Pipeline")


@dataclass
class PipelineParams:
    """All parameters needed to run the translation pipeline.

    `target_langs` is the source of truth for fan-out. A single-lang job
    is just `target_langs=["vi"]`; multi-lang jobs reuse extract_audio,
    transcribe, and Demucs across the langs.
    """
    video_path: str
    target_langs: list[str] = field(default_factory=list)
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

    def __post_init__(self):
        if not self.target_langs:
            raise ValueError("PipelineParams.target_langs must contain at least one lang")


@dataclass
class PipelineResult:
    """Result of a pipeline run.

    Per-lang outputs live in dicts keyed by lang code. `files` is the
    aggregated download manifest across all langs (each entry's label
    includes the lang). `stage_timings` keys shared stages plain
    (`extract_audio`, `transcribe`, `demucs`); per-lang stages are
    suffixed with the lang code (`translate:vi`, `dub:en`, ...).
    """
    srt_paths: dict[str, str] = field(default_factory=dict)
    dubbed_videos: dict[str, str] = field(default_factory=dict)
    burned_videos: dict[str, str] = field(default_factory=dict)
    files: list[dict] = field(default_factory=list)
    segments_count: int = 0
    detected_lang: str = ""
    stage_timings: dict[str, float] = field(default_factory=dict)


@contextmanager
def _stage(result: "PipelineResult", name: str):
    """Record wall-clock time for a pipeline stage into result.stage_timings."""
    t0 = time.monotonic()
    try:
        yield
    finally:
        result.stage_timings[name] = round(time.monotonic() - t0, 3)


ProgressCallback = Callable[[int, int, str, int], None]
"""(step, total_steps, label, progress_pct) -> None"""


def _per_lang_step_count(params: PipelineParams) -> int:
    """How many sub-steps each target lang adds: translate + srt baseline,
    plus OCR / dub-mix / merge / burn-finalize as configured."""
    n = 2  # translate, srt
    if params.translate_ocr:
        n += 1
    if params.dub:
        n += 2  # TTS+mix, merge
    n += 1  # finalize / burn
    return n


def count_steps(params: PipelineParams) -> int:
    """Total UI steps for the whole job (shared + per-lang × N langs)."""
    shared = 2  # extract_audio, transcribe
    if params.dub and params.audio_mode == "keep_original_bgm":
        shared += 1  # Demucs separation, run once and reused across langs
    return shared + len(params.target_langs) * _per_lang_step_count(params)


def _vram_guard_check(params: PipelineParams) -> None:
    """Fail-loud guard: raise FatalError if VRAM is too low for Demucs."""
    if not (params.dub and params.audio_mode == "keep_original_bgm"):
        return

    try:
        from pipeline.gpu_stats import collect_gpu_stats
        stats = collect_gpu_stats()
    except Exception as e:
        log.debug(f"VRAM guard: stats collection failed ({e}), letting pipeline try")
        return

    if not stats.get("available"):
        return

    free_mb = stats.get("vram_free_mb")
    if free_mb is None:
        return

    if free_mb < cfg.gpu.vram_min_free_mb:
        raise FatalError(
            f"Insufficient VRAM for source separation: {free_mb}MB free, "
            f"need {cfg.gpu.vram_min_free_mb}MB. "
            f"Disable --keep-original-bgm (use --audio-mode custom_bgm) "
            f"or restart the server to release cached models."
        )


def estimate_eta_seconds(
    params: PipelineParams,
    video_duration_sec: float,
    gpu_name: str | None,
) -> int:
    """Rough ETA heuristic for the whole job across all target langs.

    Shared work (transcribe, Demucs) is constant per video; per-lang work
    (translate, TTS, mix, merge, burn) scales linearly with len(target_langs).
    """
    vd = max(video_duration_sec, 1.0)

    # Shared: Whisper transcribe + (optional Demucs) — independent of N langs
    shared = vd * 0.35
    if params.whisper_model == "large-v3":
        shared *= 3.0
    if params.dub and params.audio_mode == "keep_original_bgm":
        shared += vd * 0.6  # Demucs ≈ 0.6× real-time on 3060

    # Per-lang: TTS + mix + merge + burn — each runs once per lang
    per_lang = vd * 0.25  # baseline translate+srt+light I/O
    if params.dub:
        per_lang += vd * 0.4  # TTS-heavy
    if params.translate_ocr:
        per_lang *= 1.3

    total = shared + per_lang * len(params.target_langs)

    # CPU fallback: ~6× across the board
    if gpu_name is None or not should_use_gpu():
        total *= 6.0

    return max(30, int(total))


def run_pipeline(
    params: PipelineParams,
    on_progress: ProgressCallback | None = None,
) -> PipelineResult:
    """Execute the full translation pipeline for all target langs.

    Stage ordering:
      1. Shared: extract_audio → transcribe → (optional) Demucs separation
      2. Per lang: translate → srt → (optional) OCR → (optional) dub+merge → burn

    Demucs runs once per video regardless of N langs because its output
    only depends on the source audio.
    """
    check_ffmpeg()
    _vram_guard_check(params)
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
        if on_progress:
            on_progress(current_step, total_steps, label, pct)

    n_langs = len(params.target_langs)

    # ─── Shared phase ────────────────────────────────────────────────────────

    progress("Extracting audio", 5)
    with _stage(result, "extract_audio"):
        wav_path = extract_audio(params.video_path, job_dir)
    progress_update("Extracting audio", 10)

    progress(f"Transcribing ({params.whisper_model})", 12)
    with _stage(result, "transcribe"):
        segments, detected_lang = transcribe(
            wav_path, model_name=params.whisper_model, source_lang=params.source_lang,
            cache_dir=job_dir, use_cache=params.use_cache,
        )
    src_lang = params.source_lang or detected_lang
    result.detected_lang = src_lang
    result.segments_count = len(segments)
    progress_update(f"Transcribing ({params.whisper_model})", 25)

    has_segments = len(segments) > 0

    # Demucs separation: run once and share the no_vocals.wav across all langs
    shared_no_vocals_path: str | None = None
    shared_demucs_dir = None  # kept alive until pipeline end so file isn't cleaned
    original_audio_hq: str | None = None
    if has_segments and params.dub and params.audio_mode == "keep_original_bgm":
        progress("Separating original audio (Demucs)", 28)
        original_audio_hq = extract_audio_hq(params.video_path, job_dir)
        shared_demucs_dir = os.path.join(job_dir, "_demucs_shared")
        os.makedirs(shared_demucs_dir, exist_ok=True)
        with _stage(result, "demucs"):
            stems = separate_audio(original_audio_hq, shared_demucs_dir)
        shared_no_vocals_path = stems["no_vocals"]
        progress_update("Source separation complete", 30)

    # ─── Per-lang fan-out ────────────────────────────────────────────────────

    # Each lang gets a slice of [30%, 95%] for its progress reporting
    lang_pct_start = 30
    lang_pct_end = 95
    pct_per_lang = max(1, (lang_pct_end - lang_pct_start) // max(n_langs, 1))

    for lang_idx, target_lang in enumerate(params.target_langs):
        lang_pct = lang_pct_start + lang_idx * pct_per_lang
        lang_tag = f"({lang_idx + 1}/{n_langs}) {target_lang}"
        translated_segments = _process_one_lang(
            params=params,
            target_lang=target_lang,
            segments=segments,
            src_lang=src_lang,
            video_base=video_base,
            job_dir=job_dir,
            shared_no_vocals_path=shared_no_vocals_path,
            result=result,
            progress=progress,
            progress_update=progress_update,
            lang_tag=lang_tag,
            lang_pct=lang_pct,
            pct_per_lang=pct_per_lang,
        )

    # If nothing was produced (no speech, no langs handled it), include the original
    if not result.files:
        result.files.append({
            "name": os.path.basename(params.video_path), "type": "video",
            "label": "Original video (no speech detected)",
        })

    # ─── Cleanup ─────────────────────────────────────────────────────────────
    try:
        os.remove(wav_path)
    except OSError:
        pass
    if original_audio_hq and os.path.isfile(original_audio_hq):
        try:
            os.remove(original_audio_hq)
        except OSError:
            pass
    if shared_demucs_dir and os.path.isdir(shared_demucs_dir):
        shutil.rmtree(shared_demucs_dir, ignore_errors=True)
    for temp_subdir in ["_tts_temp", "_demucs_temp", "_ocr_frames", "_ocr_overlays"]:
        temp_path = os.path.join(job_dir, temp_subdir)
        if os.path.isdir(temp_path):
            shutil.rmtree(temp_path, ignore_errors=True)

    try:
        empty_cuda_cache()
    except Exception:
        pass

    return result


def _process_one_lang(
    params: PipelineParams,
    target_lang: str,
    segments: list[dict],
    src_lang: str,
    video_base: str,
    job_dir: str,
    shared_no_vocals_path: str | None,
    result: PipelineResult,
    progress: Callable[[str, int], None],
    progress_update: Callable[[str, int], None],
    lang_tag: str,
    lang_pct: int,
    pct_per_lang: int,
) -> list[dict]:
    """Run translate → srt → (ocr) → (dub+merge) → (burn) for one lang.

    Each lang's stage_timings keys are suffixed with `:{lang}` so a multi-
    lang run still records all timings without collisions.
    """
    has_segments = len(segments) > 0

    # Step: Translate
    cache_path = os.path.join(job_dir, f"{video_base}.{src_lang}_{target_lang}.translated.json")
    progress(f"Translating {src_lang} -> {target_lang} {lang_tag}", lang_pct)
    with _stage(result, f"translate:{target_lang}"):
        translated_segments = translate_segments(
            segments, source_lang=src_lang, target_lang=target_lang,
            batch_size=params.batch_size, cache_path=cache_path, use_cache=params.use_cache,
        )

    # Step: OCR (optional, per-lang because text is translated to target)
    ocr_filter = None
    ocr_overlays = None
    if params.translate_ocr:
        progress(f"OCR {lang_tag}", lang_pct + 1)
        ocr_filter, ocr_overlays = _run_ocr(params, target_lang, src_lang, job_dir, result)

    # Step: Generate SRT
    srt_path = os.path.join(job_dir, f"{video_base}.{target_lang}.srt")
    progress(f"Generating subtitles {lang_tag}", lang_pct + 2)
    with _stage(result, f"srt:{target_lang}"):
        generate_srt(translated_segments, srt_path, text_key="translated_text")

    if has_segments:
        result.srt_paths[target_lang] = srt_path
        result.files.append({
            "name": os.path.basename(srt_path), "type": "srt",
            "label": f"Subtitles ({target_lang})",
        })

    # Step: Dub + Merge (optional)
    can_dub = params.dub and has_segments
    video_ext = os.path.splitext(params.video_path)[1]
    base_video = params.video_path

    if can_dub:
        vid_duration = get_audio_duration(params.video_path)
        dubbed_audio_path = os.path.join(job_dir, f"{video_base}.{target_lang}.dubbed.m4a")
        label = "Generating TTS + mix" if params.audio_mode == "keep_original_bgm" else "Generating TTS audio"
        progress(f"{label} {lang_tag}", lang_pct + 3)
        with _stage(result, f"dub:{target_lang}"):
            build_dubbed_audio(
                translated_segments, lang=target_lang,
                output_path=dubbed_audio_path, output_dir=job_dir,
                custom_voice=params.tts_voice, audio_mode=params.audio_mode,
                bgm_path=params.bgm_path, bgm_volume=params.bgm_volume,
                video_duration=vid_duration,
                pre_separated_no_vocals_path=shared_no_vocals_path,
            )

        output_video = os.path.join(job_dir, f"{video_base}.{target_lang}.dubbed{video_ext}")
        progress(f"Merging dubbed audio {lang_tag}", lang_pct + 4)
        with _stage(result, f"merge:{target_lang}"):
            dub_video(params.video_path, dubbed_audio_path, output_video)
        result.dubbed_videos[target_lang] = output_video
        result.files.append({
            "name": os.path.basename(output_video), "type": "video",
            "label": f"Dubbed video ({target_lang})",
        })
        base_video = output_video

    elif params.dub and not has_segments:
        progress_update(f"No speech detected, skipping dubbing {lang_tag}", lang_pct + 4)

    # Step: Burn (subtitles or OCR overlay)
    has_burn_work = (params.burn and has_segments) or ocr_filter or ocr_overlays
    if has_burn_work:
        final_video = os.path.join(job_dir, f"{video_base}.{target_lang}.final{video_ext}")
        progress(f"Burning subtitles/text {lang_tag}", lang_pct + pct_per_lang - 1)

        with _stage(result, f"burn:{target_lang}"):
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
        result.burned_videos[target_lang] = final_video
        result.files.append({
            "name": os.path.basename(final_video), "type": "video",
            "label": f"{label} ({target_lang})",
        })
    else:
        progress(f"Finalizing {lang_tag}", lang_pct + pct_per_lang - 1)

    return translated_segments


def _run_ocr(
    params: PipelineParams,
    target_lang: str,
    src_lang: str,
    job_dir: str,
    result: PipelineResult,
):
    """Run OCR detection + translation + render. Returns (drawtext_filter, overlays)."""
    ocr_filter = None
    ocr_overlays = None
    _ocr_stage = _stage(result, f"ocr:{target_lang}")
    _ocr_stage.__enter__()
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

        frame_results = detect_text_regions(key_frames, lang=src_lang)

        ocr_quality = params.ocr_quality
        if ocr_quality == "premium":
            text_groups = group_persistent_texts_tracked(frame_results)
        else:
            text_groups = group_persistent_texts(frame_results)

        if text_groups:
            text_groups = translate_ocr_texts(text_groups, src_lang, target_lang)

            if ocr_quality == "fast" and target_lang in cfg.ocr.drawtext_unsafe_langs:
                log.info(f"Auto-upgrading OCR quality fast->quality for lang={target_lang}")
                ocr_quality = "quality"

            if ocr_quality == "fast":
                ocr_filter = generate_drawtext_filter(
                    text_groups, video_info["width"], video_info["height"], target_lang
                )
            elif ocr_quality == "quality":
                ocr_overlays = generate_overlay_images(
                    text_groups, video_info["width"], video_info["height"],
                    job_dir, target_lang
                )
            elif ocr_quality == "premium":
                inpaint_patches = inpaint_all_regions(params.video_path, text_groups, frames_dir)
                ocr_overlays = generate_overlay_images(
                    text_groups, video_info["width"], video_info["height"],
                    job_dir, target_lang, inpaint_patches=inpaint_patches,
                )

        if ocr_quality != "premium":
            shutil.rmtree(frames_dir, ignore_errors=True)
    except ImportError as e:
        log.warning(f"OCR module not available ({e}), skipping...")
    finally:
        _ocr_stage.__exit__(None, None, None)

    return ocr_filter, ocr_overlays
