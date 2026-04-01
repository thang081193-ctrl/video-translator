#!/usr/bin/env python3
"""Launcher that ensures user site-packages are on sys.path."""
import site
import sys

# Ensure user site-packages are loaded
site.ENABLE_USER_SITE = True
user_site = site.getusersitepackages()
if user_site not in sys.path:
    sys.path.insert(0, user_site)

# Now launch the app
import web_app  # noqa: trigger module-level startup
import uvicorn

from pipeline.audio import check_ffmpeg
check_ffmpeg()
print("\n  Video Translator Web UI")
print("  http://localhost:8000\n")
uvicorn.run(web_app.app, host="0.0.0.0", port=8000)
