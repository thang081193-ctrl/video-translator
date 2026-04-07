#!/usr/bin/env python3
"""Video Translator — Web UI powered by FastAPI.

This is a thin entry point. All logic lives in web/ package.
"""

import io
import os
import sys

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
load_dotenv()

from pipeline.config import cfg
from pipeline.logger import setup_logging, get_logger
from pipeline.audio import check_ffmpeg

# Initialize structured logging for web mode
setup_logging(mode="web", log_file=os.path.join(os.path.dirname(__file__), "worker.log"))

from web.app import create_app

app = create_app()

if __name__ == "__main__":
    import uvicorn
    log = get_logger("WebApp")
    check_ffmpeg()
    log.info(f"Video Translator Web UI — http://localhost:{cfg.web.port}")
    uvicorn.run(app, host=cfg.web.host, port=cfg.web.port)
