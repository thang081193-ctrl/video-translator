"""Job queue and background worker thread for sequential video processing."""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
import traceback
import queue as _queue

from pipeline.config import cfg
from pipeline.logger import get_logger
from web.pipeline_runner import (
    PipelineParams, PipelineResult, run_pipeline, count_steps, estimate_eta_seconds,
)

log = get_logger("Worker")

# In-memory job store (protected by _jobs_lock)
jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()

# Sequential job queue — process one video at a time to avoid OOM
_job_queue: _queue.Queue = _queue.Queue()
_worker_thread: threading.Thread | None = None
_worker_lock = threading.Lock()

# Track last activity for auto-stop
last_activity = time.time()


def update_job(job_id: str, **kwargs):
    """Thread-safe job status update."""
    with _jobs_lock:
        job = jobs.get(job_id)
        if job:
            job.update(kwargs)


def get_job(job_id: str) -> dict | None:
    """Thread-safe job retrieval."""
    with _jobs_lock:
        job = jobs.get(job_id)
        if job:
            return dict(job)
        return None


def create_job(job_id: str, params: PipelineParams, video_name: str) -> dict:
    """Create a new job entry and enqueue it."""
    total_steps = count_steps(params)

    # P6.C: compute advisory ETA for /api/status display. Best-effort —
    # if ffprobe or nvidia-smi is unavailable, eta defaults to 0.
    eta_seconds = 0
    try:
        from pipeline.dub.separator import get_audio_duration
        from pipeline.gpu_stats import collect_gpu_stats
        video_duration = get_audio_duration(params.video_path)
        gpu_name = collect_gpu_stats().get("name")
        eta_seconds = estimate_eta_seconds(params, video_duration, gpu_name)
    except Exception as e:
        log.debug(f"ETA estimation failed for {job_id}: {e}")

    job = {
        "id": job_id,
        "status": "queued",
        "step": 0,
        "total_steps": total_steps,
        "step_label": "Queued",
        "progress": 0,
        "eta_seconds": eta_seconds,
        "error": None,
        "files": [],
        "video_path": params.video_path,
        "video_name": video_name,
    }
    with _jobs_lock:
        jobs[job_id] = job

    _job_queue.put((job_id, params))
    _ensure_worker()
    return job


def _ensure_worker():
    """Start the background worker thread if not already running."""
    global _worker_thread
    with _worker_lock:
        if _worker_thread is not None and _worker_thread.is_alive():
            return
        log.info("Starting worker thread")
        _worker_thread = threading.Thread(target=_worker_loop, daemon=True)
        _worker_thread.start()


def _worker_loop():
    """Process jobs one at a time from the queue."""
    log.info("Worker thread started")
    while True:
        try:
            args = _job_queue.get(timeout=cfg.web.worker_queue_timeout)
        except _queue.Empty:
            continue

        job_id, params = args
        try:
            log.info(f"Processing job {job_id}")
            pipeline_thread = threading.Thread(
                target=_run_job, args=(job_id, params), daemon=True,
            )
            pipeline_thread.start()
            pipeline_thread.join(timeout=cfg.web.job_timeout)
            if pipeline_thread.is_alive():
                log.error(f"Job {job_id} timed out after {cfg.web.job_timeout}s")
                update_job(job_id, status="error", error=f"Job timed out after {cfg.web.job_timeout // 60} minutes")
            else:
                final_status = (get_job(job_id) or {}).get("status", "unknown")
                log.info(f"Job {job_id} finished — status={final_status}")
        except BaseException as e:
            tb = traceback.format_exc()
            log.error(f"Job {job_id} failed: {e}\n{tb}")
            update_job(job_id, status="error", error=str(e))
        finally:
            _job_queue.task_done()


def _run_job(job_id: str, params: PipelineParams):
    """Execute the pipeline for a single job."""
    try:
        def on_progress(step, total, label, pct):
            update_job(job_id, status="running", step=step, total_steps=total,
                       step_label=label, progress=pct)

        update_job(job_id, status="running")
        result = run_pipeline(params, on_progress=on_progress)

        global last_activity
        last_activity = time.time()
        update_job(job_id, status="done", step=count_steps(params),
                   step_label="Complete", progress=100, files=result.files)

    except Exception as e:
        tb = traceback.format_exc()
        log.error(f"Job {job_id} pipeline error: {e}\n{tb}")
        update_job(job_id, status="error", error=str(e))


def cleanup_old_jobs(upload_dir: str):
    """Delete job files and records older than configured max age."""
    cutoff = time.time() - cfg.web.cleanup_max_age
    for job_id in list(jobs.keys()):
        job = jobs[job_id]
        if job["status"] in ("queued", "running"):
            continue
        job_dir = os.path.join(upload_dir, job_id)
        if os.path.isdir(job_dir):
            try:
                if os.path.getmtime(job_dir) < cutoff:
                    shutil.rmtree(job_dir, ignore_errors=True)
                    del jobs[job_id]
                    log.info(f"Cleaned up old job {job_id}")
            except OSError:
                pass


def auto_stop_check():
    """Check if Vast.ai instance should be stopped due to idle timeout."""
    cid = os.environ.get("CONTAINER_ID")
    api_key = os.environ.get("CONTAINER_API_KEY")
    if cid and api_key and (time.time() - last_activity) > cfg.web.idle_timeout:
        log.info(f"Idle for >{cfg.web.idle_timeout // 60}min, stopping instance {cid}")
        try:
            subprocess.run(
                ["vastai", "stop", "instance", cid, "--api-key", api_key],
                capture_output=True, timeout=30,
            )
        except Exception as e:
            log.error(f"Auto-stop failed: {e}")
