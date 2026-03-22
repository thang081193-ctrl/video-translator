#!/usr/bin/env python3
"""Video Translator — Web UI powered by FastAPI."""

import io
import logging
import os
import shutil
import subprocess
import sys
import threading
import traceback
import time
import uuid

# Fix Windows encoding for Unicode characters (e.g. EasyOCR progress bars)
if sys.platform == "win32":
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name)
        if stream is None or not hasattr(stream, "reconfigure"):
            setattr(sys, stream_name, io.TextIOWrapper(
                open(os.devnull, "wb"), encoding="utf-8", errors="replace"))
        else:
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                setattr(sys, stream_name, io.TextIOWrapper(
                    open(os.devnull, "wb"), encoding="utf-8", errors="replace"))

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

load_dotenv()

from pipeline.audio import extract_audio, extract_audio_hq, get_video_info, check_ffmpeg
from pipeline.transcribe import transcribe
from pipeline.translate import translate_segments
from pipeline.subtitle import generate_srt
from pipeline.burn import burn_subtitles, burn_ocr_overlay, burn_with_overlays
from pipeline.dub import build_dubbed_audio, dub_video, DEFAULT_VOICES

import queue as _queue

app = FastAPI(title="Video Translator")

UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

# In-memory job store
jobs: dict[str, dict] = {}

# Sequential job queue — process one video at a time to avoid OOM
_job_queue: _queue.Queue = _queue.Queue()
_worker_thread: threading.Thread | None = None
_worker_lock = threading.Lock()

# File-based logging for worker thread (stdout may be devnull on pythonw)
_log_path = os.path.join(os.path.dirname(__file__), "worker.log")
_logger = logging.getLogger("worker")
_logger.setLevel(logging.DEBUG)
_fh = logging.FileHandler(_log_path, encoding="utf-8")
_fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
_logger.addHandler(_fh)

# Upload size limit (100MB — matches Cloudflare Free tunnel limit)
MAX_UPLOAD_SIZE = 100 * 1024 * 1024

# Track last activity for auto-stop
_last_activity = time.time()

# Auto-stop idle timeout (seconds) — only active when CONTAINER_ID is set
_IDLE_TIMEOUT = 90 * 60  # 90 minutes


def _cleanup_old_jobs():
    """Delete job files and records older than 24 hours."""
    cutoff = time.time() - 86400
    for job_id in list(jobs.keys()):
        job = jobs[job_id]
        if job["status"] in ("queued", "running"):
            continue
        job_dir = os.path.join(UPLOAD_DIR, job_id)
        if os.path.isdir(job_dir):
            try:
                if os.path.getmtime(job_dir) < cutoff:
                    shutil.rmtree(job_dir, ignore_errors=True)
                    del jobs[job_id]
                    _logger.info(f"Cleaned up old job {job_id}")
            except OSError:
                pass


def _auto_stop_checker():
    """Background thread: stop Vast.ai instance after idle timeout."""
    global _last_activity
    while True:
        time.sleep(300)  # check every 5 minutes
        # Run cleanup every cycle
        try:
            _cleanup_old_jobs()
        except Exception as e:
            _logger.error(f"Cleanup error: {e}")
        # Check idle timeout (only on Vast.ai)
        cid = os.environ.get("CONTAINER_ID")
        api_key = os.environ.get("CONTAINER_API_KEY")
        if cid and api_key and (time.time() - _last_activity) > _IDLE_TIMEOUT:
            _logger.info(f"Idle for >{_IDLE_TIMEOUT // 60}min, stopping instance {cid}")
            try:
                subprocess.run(
                    ["vastai", "stop", "instance", cid, "--api-key", api_key],
                    capture_output=True, timeout=30,
                )
            except Exception as e:
                _logger.error(f"Auto-stop failed: {e}")


# Start background maintenance thread
_maintenance_thread = threading.Thread(target=_auto_stop_checker, daemon=True)
_maintenance_thread.start()


# ─── API routes ────────────────────────────────────────────────────────────────

@app.get("/api/languages")
async def get_languages():
    """Return supported languages for the frontend dropdown."""
    languages = [
        {"code": "vi", "name": "Tiếng Việt"},
        {"code": "en", "name": "English"},
        {"code": "ja", "name": "日本語"},
        {"code": "ko", "name": "한국어"},
        {"code": "zh", "name": "中文"},
        {"code": "fr", "name": "Français"},
        {"code": "es", "name": "Español"},
        {"code": "de", "name": "Deutsch"},
        {"code": "pt", "name": "Português"},
        {"code": "ru", "name": "Русский"},
        {"code": "th", "name": "ไทย"},
        {"code": "id", "name": "Bahasa Indonesia"},
        {"code": "hi", "name": "हिन्दी"},
        {"code": "ar", "name": "العربية"},
        {"code": "it", "name": "Italiano"},
    ]
    return {"languages": languages, "voices": DEFAULT_VOICES}


def _parse_bool(val: str) -> bool:
    """Parse string boolean from form data (JS sends 'true'/'false' strings)."""
    if isinstance(val, bool):
        return val
    return str(val).lower() in ("true", "1", "yes", "on")


@app.post("/api/translate")
async def start_translation(
    video: UploadFile = File(...),
    target_lang: str = Form(...),
    source_lang: str = Form(default=""),
    whisper_model: str = Form(default="medium"),
    burn: str = Form(default="false"),
    dub: str = Form(default="false"),
    bgm: UploadFile | None = File(default=None),
    tts_voice: str = Form(default=""),
    batch_size: int = Form(default=20),
    audio_mode: str = Form(default="keep_original_bgm"),
    bgm_volume: float = Form(default=0.25),
    translate_ocr: str = Form(default="false"),
    ocr_quality: str = Form(default="fast"),
):
    """Upload video and start a translation job."""
    # Parse booleans from form strings
    burn = _parse_bool(burn)
    dub = _parse_bool(dub)
    translate_ocr = _parse_bool(translate_ocr)
    # Validate inputs
    if ocr_quality not in ("fast", "quality", "premium"):
        ocr_quality = "fast"
    batch_size = max(1, batch_size)
    bgm_volume = max(0.05, min(1.0, bgm_volume))
    job_id = str(uuid.uuid4())[:8]
    job_dir = os.path.join(UPLOAD_DIR, job_id)
    os.makedirs(job_dir, exist_ok=True)

    # Save uploaded video (stream-based size check)
    video_filename = video.filename or "video.mp4"
    video_path = os.path.join(job_dir, video_filename)
    written = 0
    with open(video_path, "wb") as f:
        while chunk := await video.read(1024 * 1024):  # 1MB chunks
            written += len(chunk)
            if written > MAX_UPLOAD_SIZE:
                f.close()
                os.remove(video_path)
                shutil.rmtree(job_dir, ignore_errors=True)
                return JSONResponse(
                    {"error": f"File exceeds {MAX_UPLOAD_SIZE // (1024*1024)}MB limit"},
                    status_code=413,
                )
            f.write(chunk)

    # Save BGM if provided
    bgm_path = None
    if dub and audio_mode == "custom_bgm" and bgm and bgm.filename:
        bgm_path = os.path.join(job_dir, bgm.filename)
        with open(bgm_path, "wb") as f:
            shutil.copyfileobj(bgm.file, f)

    # Calculate total steps dynamically
    total_steps = 4  # audio, transcribe, translate, subtitles
    if translate_ocr:
        total_steps += 1
    if dub:
        total_steps += 2  # TTS + merge
    total_steps += 1  # finalize/burn step

    job = {
        "id": job_id,
        "status": "queued",
        "step": 0,
        "total_steps": total_steps,
        "step_label": "Queued",
        "progress": 0,
        "error": None,
        "files": [],
        "video_path": video_path,
        "video_name": video_filename,
    }
    jobs[job_id] = job

    # Enqueue for sequential processing
    _logger.info(f"Job {job_id}: video={video_filename}, lang={target_lang}, "
                 f"burn={burn}, dub={dub}, ocr={translate_ocr}, model={whisper_model}, "
                 f"audio_mode={audio_mode}")
    _job_queue.put((
        job_id, video_path, target_lang, source_lang or None,
        whisper_model, burn, dub, bgm_path, tts_voice or None, batch_size,
        audio_mode, bgm_volume, translate_ocr, ocr_quality,
    ))
    _ensure_worker()

    return {"job_id": job_id}


def _ensure_worker():
    """Start the background worker thread if not already running."""
    global _worker_thread
    with _worker_lock:
        if _worker_thread is not None and _worker_thread.is_alive():
            return
        _logger.info("Starting worker thread")
        _worker_thread = threading.Thread(target=_worker_loop, daemon=True)
        _worker_thread.start()


def _worker_loop():
    """Process jobs one at a time from the queue."""
    _logger.info("Worker thread started")
    while True:
        try:
            args = _job_queue.get(timeout=5)
        except _queue.Empty:
            continue
        job_id = args[0]
        try:
            _logger.info(f"Processing job {job_id}")
            _run_pipeline(*args)
            final_status = jobs.get(job_id, {}).get("status", "unknown")
            _logger.info(f"Job {job_id} finished — status={final_status}")
        except BaseException as e:
            tb = traceback.format_exc()
            _logger.error(f"Job {job_id} failed: {e}\n{tb}")
            _update(job_id, status="error", error=str(e))
        finally:
            _job_queue.task_done()


@app.get("/api/status/{job_id}")
async def get_status(job_id: str):
    """Poll job status."""
    job = jobs.get(job_id)
    if not job:
        return JSONResponse({"error": "Job not found"}, status_code=404)
    return {
        "id": job["id"],
        "status": job["status"],
        "step": job["step"],
        "total_steps": job["total_steps"],
        "step_label": job["step_label"],
        "progress": job["progress"],
        "error": job["error"],
        "files": job["files"],
    }


@app.get("/api/debug")
async def debug_info():
    """Debug endpoint to check worker status (disabled in production)."""
    if os.environ.get("PRODUCTION"):
        return JSONResponse({"error": "disabled"}, status_code=403)
    return {
        "worker_alive": _worker_thread is not None and _worker_thread.is_alive(),
        "queue_size": _job_queue.qsize(),
        "jobs": {jid: {"status": j["status"], "step_label": j["step_label"], "progress": j["progress"]}
                 for jid, j in jobs.items()},
    }


@app.get("/api/download/{job_id}/{filename}")
async def download_file(job_id: str, filename: str):
    """Download a result file."""
    job = jobs.get(job_id)
    if not job:
        return JSONResponse({"error": "Job not found"}, status_code=404)

    job_dir = os.path.join(UPLOAD_DIR, job_id)
    file_path = os.path.join(job_dir, filename)

    if not os.path.isfile(file_path):
        return JSONResponse({"error": "File not found"}, status_code=404)

    return FileResponse(file_path, filename=filename)


# ─── Pipeline runner ───────────────────────────────────────────────────────────

def _update(job_id: str, **kwargs):
    job = jobs.get(job_id)
    if job:
        job.update(kwargs)


def _run_pipeline(
    job_id: str,
    video_path: str,
    target_lang: str,
    source_lang: str | None,
    whisper_model: str,
    burn: bool,
    dub: bool,
    bgm_path: str | None,
    tts_voice: str | None,
    batch_size: int,
    audio_mode: str = "keep_original_bgm",
    bgm_volume: float = 0.25,
    translate_ocr: bool = False,
    ocr_quality: str = "fast",
):
    try:
        job_dir = os.path.dirname(video_path)
        video_base = os.path.splitext(os.path.basename(video_path))[0]
        total_steps = jobs[job_id]["total_steps"]
        current_step = 0

        # Step 1: Extract audio
        current_step += 1
        _update(job_id, status="running", step=current_step, total_steps=total_steps,
                step_label="Extracting audio", progress=5)
        wav_path = extract_audio(video_path, job_dir)
        _update(job_id, progress=15)

        # Step 2: Transcribe
        current_step += 1
        _update(job_id, step=current_step, step_label=f"Transcribing ({whisper_model})", progress=20)
        segments, detected_lang = transcribe(
            wav_path, model_name=whisper_model, source_lang=source_lang,
            cache_dir=job_dir, use_cache=True,
        )
        src_lang = source_lang or detected_lang
        _update(job_id, progress=45)

        # Step 3: Translate
        current_step += 1
        cache_path = os.path.join(job_dir, f"{video_base}.{src_lang}_{target_lang}.translated.json")
        _update(job_id, step=current_step, step_label=f"Translating {src_lang} -> {target_lang}", progress=50)
        translated_segments = translate_segments(
            segments, source_lang=src_lang, target_lang=target_lang,
            batch_size=batch_size, cache_path=cache_path, use_cache=True,
        )
        _update(job_id, progress=65)

        # Step 3.5 (optional): OCR — quality modes: fast / quality / premium
        ocr_filter = None
        ocr_overlays = None  # list of overlay dicts for quality/premium modes
        if translate_ocr:
            current_step += 1
            _update(job_id, step=current_step, step_label="Detecting on-screen text (OCR)", progress=67)
            try:
                from pipeline.ocr import (
                    extract_key_frames, detect_text_regions,
                    group_persistent_texts, group_persistent_texts_tracked,
                    translate_ocr_texts, generate_drawtext_filter,
                    generate_overlay_images, inpaint_all_regions,
                )

                video_info = get_video_info(video_path)
                frames_dir = os.path.join(job_dir, "_ocr_frames")
                key_frames = extract_key_frames(video_path, frames_dir, interval=2.0)

                _update(job_id, step_label="Running OCR...", progress=69)
                frame_results = detect_text_regions(key_frames, lang=src_lang)

                # Group texts — use Kalman tracking for premium mode
                if ocr_quality == "premium":
                    text_groups = group_persistent_texts_tracked(frame_results)
                else:
                    text_groups = group_persistent_texts(frame_results)

                if text_groups:
                    _update(job_id, step_label="Translating on-screen text...", progress=72)
                    text_groups = translate_ocr_texts(text_groups, src_lang, target_lang)

                    # ffmpeg drawtext cannot render Vietnamese/diacritics — auto-upgrade
                    _DRAWTEXT_UNSAFE_LANGS = {"vi", "fr", "es", "de", "pt", "it", "ru", "th", "hi", "ar"}
                    if ocr_quality == "fast" and target_lang in _DRAWTEXT_UNSAFE_LANGS:
                        _logger.info(f"Auto-upgrading OCR quality fast->quality for lang={target_lang}")
                        ocr_quality = "quality"

                    if ocr_quality == "fast":
                        # Phase 1: ffmpeg drawtext (ASCII/CJK only — no diacritics)
                        ocr_filter = generate_drawtext_filter(
                            text_groups, video_info["width"], video_info["height"], target_lang
                        )
                    elif ocr_quality == "quality":
                        # Phase 2: Pillow overlay PNGs (better style, centering, word-wrap)
                        _update(job_id, step_label="Rendering text overlays...", progress=73)
                        ocr_overlays = generate_overlay_images(
                            text_groups, video_info["width"], video_info["height"],
                            job_dir, target_lang
                        )
                    elif ocr_quality == "premium":
                        # Phase 3: Inpaint + overlay (best quality, removes original text cleanly)
                        _update(job_id, step_label="Inpainting text regions...", progress=73)
                        inpaint_patches = inpaint_all_regions(video_path, text_groups, frames_dir)
                        _update(job_id, step_label="Rendering text overlays...", progress=74)
                        ocr_overlays = generate_overlay_images(
                            text_groups, video_info["width"], video_info["height"],
                            job_dir, target_lang, inpaint_patches=inpaint_patches,
                        )

                # Don't delete frames_dir yet if we still need it for inpainting
                if ocr_quality != "premium":
                    shutil.rmtree(frames_dir, ignore_errors=True)
            except ImportError as e:
                _logger.warning(f"OCR module not available: {e}")
                print(f"  OCR module not available ({e}), skipping...")
            _update(job_id, progress=75)

        # Step 4: Generate SRT
        current_step += 1
        srt_path = os.path.join(job_dir, f"{video_base}.{target_lang}.srt")
        _update(job_id, step=current_step, step_label="Generating subtitles", progress=76)
        generate_srt(translated_segments, srt_path, text_key="translated_text")

        result_files = []
        has_segments = len(translated_segments) > 0

        # Only include SRT if there are actual segments
        if has_segments:
            result_files.append({"name": os.path.basename(srt_path), "type": "srt", "label": "Subtitles (.srt)"})

        # Determine if dubbing is actually possible (need segments for TTS)
        can_dub = dub and has_segments
        video_ext = os.path.splitext(video_path)[1]
        base_video = video_path  # track which video to use for burn/OCR

        if can_dub:
            # Get video duration for proper audio padding
            from pipeline.dub import get_audio_duration
            vid_duration = get_audio_duration(video_path)

            # Extract HQ audio for Demucs if needed
            original_audio_hq = None
            if audio_mode == "keep_original_bgm":
                original_audio_hq = extract_audio_hq(video_path, job_dir)

            # Step: TTS + mix
            current_step += 1
            dubbed_audio_path = os.path.join(job_dir, f"{video_base}.{target_lang}.dubbed.m4a")
            label = "Generating TTS + separating audio" if audio_mode == "keep_original_bgm" else "Generating TTS audio"
            _update(job_id, step=current_step, step_label=label, progress=78)
            build_dubbed_audio(
                translated_segments,
                lang=target_lang,
                output_path=dubbed_audio_path,
                output_dir=job_dir,
                custom_voice=tts_voice,
                audio_mode=audio_mode,
                bgm_path=bgm_path,
                bgm_volume=bgm_volume,
                original_audio_path=original_audio_hq,
                video_duration=vid_duration,
            )
            _update(job_id, progress=88)

            # Cleanup HQ audio
            if original_audio_hq and os.path.isfile(original_audio_hq):
                os.remove(original_audio_hq)

            # Step: Merge
            current_step += 1
            output_video = os.path.join(job_dir, f"{video_base}.{target_lang}.dubbed{video_ext}")
            _update(job_id, step=current_step, step_label="Merging dubbed audio", progress=90)
            dub_video(video_path, dubbed_audio_path, output_video)
            result_files.append({"name": os.path.basename(output_video), "type": "video", "label": "Dubbed video"})
            base_video = output_video
        elif dub and not has_segments:
            # Skip dub steps (no speech to dub), advance step counter
            current_step += 2  # skip TTS + Merge steps
            _update(job_id, step=current_step, step_label="No speech detected, skipping dubbing", progress=88)

        # Step: Burn subtitles / OCR overlay
        current_step += 1
        has_burn_work = (burn and has_segments) or ocr_filter or ocr_overlays
        if has_burn_work:
            final_video = os.path.join(job_dir, f"{video_base}.{target_lang}.final{video_ext}")
            _update(job_id, step=current_step, step_label="Burning subtitles/text overlay", progress=93)

            if ocr_overlays:
                # Quality/Premium mode: use overlay PNGs
                burn_with_overlays(
                    base_video, ocr_overlays, final_video,
                    srt_path=srt_path if (burn and has_segments) else None,
                )
            elif burn and has_segments:
                # Fast mode with subtitles (+ optional drawtext OCR filter)
                burn_subtitles(base_video, srt_path, final_video, ocr_filter=ocr_filter)
            elif ocr_filter:
                # Fast mode OCR only (no subtitles)
                burn_ocr_overlay(base_video, ocr_filter, final_video)

            label = "Dubbed + Subtitles" if can_dub else "Video with text overlay"
            result_files.append({"name": os.path.basename(final_video), "type": "video", "label": label})
        else:
            _update(job_id, step=current_step, step_label="Finalizing", progress=95)

        # Cleanup
        try:
            os.remove(wav_path)
        except OSError:
            pass
        for temp_dir in ["_tts_temp", "_demucs_temp", "_ocr_frames", "_ocr_overlays"]:
            temp_path = os.path.join(job_dir, temp_dir)
            if os.path.isdir(temp_path):
                shutil.rmtree(temp_path, ignore_errors=True)

        global _last_activity
        _last_activity = time.time()
        _update(job_id, status="done", step=total_steps, step_label="Complete",
                progress=100, files=result_files)

    except Exception as e:
        import traceback as _tb
        tb_str = _tb.format_exc()
        _logger.error(f"Job {job_id} pipeline error: {e}\n{tb_str}")
        _update(job_id, status="error", error=str(e))


# ─── Static files & SPA ───────────────────────────────────────────────────────

static_dir = os.path.join(os.path.dirname(__file__), "static")
os.makedirs(static_dir, exist_ok=True)
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/")
async def index():
    return FileResponse(os.path.join(static_dir, "index.html"))


if __name__ == "__main__":
    import uvicorn
    check_ffmpeg()
    print("\n  Video Translator Web UI")
    print("  http://localhost:3456\n")
    uvicorn.run(app, host="0.0.0.0", port=3456)
