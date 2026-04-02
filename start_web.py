#!/usr/bin/env python3
"""Launcher that ensures user site-packages are on sys.path."""
import site
import sys
import os

# Ensure user site-packages are loaded
site.ENABLE_USER_SITE = True
user_site = site.getusersitepackages()
if user_site not in sys.path:
    sys.path.insert(0, user_site)

from dotenv import load_dotenv
from pipeline.preflight import env_flag, run_preflight

load_dotenv()
run_preflight(
    require_translation_keys=env_flag("REQUIRE_TRANSLATION_KEYS", True),
    require_grok=env_flag("REQUIRE_GROK", False),
    require_cuda=env_flag("REQUIRE_CUDA", False),
)

# Now launch the app
import web_app  # noqa: trigger module-level startup
import uvicorn

from pipeline.audio import check_ffmpeg
check_ffmpeg()
port = int(os.environ.get("PORT", 8000))
print("\n  Video Translator Web UI")
print(f"  http://localhost:{port}\n")
uvicorn.run(web_app.app, host="0.0.0.0", port=port)
