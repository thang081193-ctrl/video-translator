"""FastAPI route handlers."""

from __future__ import annotations

import os
import shutil
import uuid

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from pipeline.config import cfg
from pipeline.languages import DEFAULT_VOICES, ALL_LANGUAGE_CODES, list_for_ui
from pipeline.logger import get_logger
from web.pipeline_runner import PipelineParams
from web.worker import jobs, get_job, create_job

log = get_logger("Routes")

router = APIRouter(prefix="/api")


def _parse_bool(val: str) -> bool:
    """Parse string boolean from form data."""
    if isinstance(val, bool):
        return val
    return str(val).lower() in ("true", "1", "yes", "on")


@router.get("/languages")
async def get_languages():
    """Return supported languages for the frontend dropdown.

    Both `languages` (ordered list) and `voices` (code→voice mapping)
    derive from pipeline.languages registry — single source of truth.
    """
    return {"languages": list_for_ui(), "voices": DEFAULT_VOICES}


@router.get("/health")
async def health_check():
    """Health check endpoint for Docker/deployment."""
    return {"status": "ok"}


@router.get("/gpu")
async def gpu_stats():
    """GPU statistics endpoint (P6.C).

    Returns VRAM/temperature/utilization from nvidia-smi + the state of
    the P6.A in-process model caches (Whisper LRU, Demucs separator,
    EasyOCR readers) + sticky GPU/CPU flag.

    Never raises — on error returns `{"available": False, "error": ...}`.
    Safe to poll from web UI without auth (no sensitive data exposed).
    """
    try:
        from pipeline.gpu_stats import collect_gpu_stats
        return collect_gpu_stats()
    except Exception as e:
        log.warning(f"/api/gpu failed to collect stats: {e}")
        return {"available": False, "error": str(e)}


@router.post("/translate")
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
    # ─── Early validation: fail before any expensive work ────────────────────
    if target_lang not in ALL_LANGUAGE_CODES:
        return JSONResponse(
            {"error": f"Unsupported target_lang '{target_lang}'. "
                      f"Must be one of: {sorted(ALL_LANGUAGE_CODES)}"},
            status_code=400,
        )
    if source_lang and source_lang not in ALL_LANGUAGE_CODES:
        return JSONResponse(
            {"error": f"Unsupported source_lang '{source_lang}'. "
                      f"Must be one of: {sorted(ALL_LANGUAGE_CODES)}"},
            status_code=400,
        )

    burn = _parse_bool(burn)
    dub = _parse_bool(dub)
    translate_ocr = _parse_bool(translate_ocr)
    if ocr_quality not in ("fast", "quality", "premium"):
        ocr_quality = "fast"
    batch_size = max(1, batch_size)
    bgm_volume = max(0.05, min(1.0, bgm_volume))

    job_id = str(uuid.uuid4())[:8]
    upload_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "uploads")
    job_dir = os.path.join(upload_dir, job_id)
    os.makedirs(job_dir, exist_ok=True)

    # Save uploaded video (stream-based size check)
    video_filename = video.filename or "video.mp4"
    video_path = os.path.join(job_dir, video_filename)
    written = 0
    with open(video_path, "wb") as f:
        while chunk := await video.read(1024 * 1024):
            written += len(chunk)
            if written > cfg.web.max_upload_bytes:
                f.close()
                os.remove(video_path)
                shutil.rmtree(job_dir, ignore_errors=True)
                return JSONResponse(
                    {"error": f"File exceeds {cfg.web.max_upload_bytes // (1024*1024)}MB limit"},
                    status_code=413,
                )
            f.write(chunk)

    # Save BGM if provided
    bgm_path = None
    if dub and audio_mode == "custom_bgm" and bgm and bgm.filename:
        bgm_path = os.path.join(job_dir, bgm.filename)
        with open(bgm_path, "wb") as f:
            shutil.copyfileobj(bgm.file, f)

    params = PipelineParams(
        video_path=video_path,
        target_lang=target_lang,
        source_lang=source_lang or None,
        whisper_model=whisper_model,
        burn=burn,
        dub=dub,
        bgm_path=bgm_path,
        tts_voice=tts_voice or None,
        batch_size=batch_size,
        audio_mode=audio_mode,
        bgm_volume=bgm_volume,
        translate_ocr=translate_ocr,
        ocr_quality=ocr_quality,
        output_dir=job_dir,
    )

    log.info(f"Job {job_id}: video={video_filename}, lang={target_lang}, "
             f"burn={burn}, dub={dub}, ocr={translate_ocr}, model={whisper_model}, "
             f"audio_mode={audio_mode}")
    create_job(job_id, params, video_filename)

    return {"job_id": job_id}


@router.get("/status/{job_id}")
async def get_status(job_id: str):
    """Poll job status."""
    job = get_job(job_id)
    if not job:
        return JSONResponse({"error": "Job not found"}, status_code=404)
    return {
        "id": job["id"],
        "status": job["status"],
        "step": job["step"],
        "total_steps": job["total_steps"],
        "step_label": job["step_label"],
        "progress": job["progress"],
        "eta_seconds": job.get("eta_seconds", 0),
        "error": job["error"],
        "files": list(job["files"]),
    }


@router.get("/debug")
async def debug_info():
    """Debug endpoint (disabled in production)."""
    if os.environ.get("PRODUCTION"):
        return JSONResponse({"error": "disabled"}, status_code=403)
    from web.worker import _worker_thread, _job_queue
    return {
        "worker_alive": _worker_thread is not None and _worker_thread.is_alive(),
        "queue_size": _job_queue.qsize(),
        "jobs": {jid: {"status": j["status"], "step_label": j["step_label"], "progress": j["progress"]}
                 for jid, j in jobs.items()},
    }


@router.get("/download/{job_id}/{filename}")
async def download_file(job_id: str, filename: str):
    """Download a result file."""
    job = get_job(job_id)
    if not job:
        return JSONResponse({"error": "Job not found"}, status_code=404)

    upload_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "uploads")
    file_path = os.path.join(upload_dir, job_id, filename)

    if not os.path.isfile(file_path):
        return JSONResponse({"error": "File not found"}, status_code=404)

    return FileResponse(file_path, filename=filename)
