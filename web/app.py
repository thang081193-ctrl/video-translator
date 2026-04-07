"""FastAPI application factory."""

from __future__ import annotations

import os
import threading
import time

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from pipeline.config import cfg
from pipeline.logger import get_logger
from web.routes import router
from web.worker import cleanup_old_jobs, auto_stop_check

log = get_logger("App")

UPLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "uploads")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    os.makedirs(UPLOAD_DIR, exist_ok=True)

    app = FastAPI(title="Video Translator")
    app.include_router(router)

    # Static files & SPA
    static_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")
    os.makedirs(static_dir, exist_ok=True)
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/")
    async def index():
        return FileResponse(os.path.join(static_dir, "index.html"))

    # Start background maintenance thread
    maintenance = threading.Thread(target=_maintenance_loop, daemon=True)
    maintenance.start()

    return app


def _maintenance_loop():
    """Background thread: cleanup old jobs + auto-stop idle Vast.ai instance."""
    while True:
        time.sleep(cfg.web.cleanup_interval)
        try:
            cleanup_old_jobs(UPLOAD_DIR)
        except Exception as e:
            log.error(f"Cleanup error: {e}")
        try:
            auto_stop_check()
        except Exception as e:
            log.error(f"Auto-stop check error: {e}")
