#!/usr/bin/env python3
"""Wrapper to ensure user site-packages are loaded before importing."""
import os
import sys

# Add user site-packages (pip --user installs go here)
_user_site = os.path.join(os.environ.get("APPDATA", ""), "Python", "Python314", "site-packages")
if os.path.isdir(_user_site) and _user_site not in sys.path:
    sys.path.insert(0, _user_site)

# Change to script directory
os.chdir(os.path.dirname(os.path.abspath(__file__)))

# Now import and run
from web_app import app  # noqa
import uvicorn

print("\n  Video Translator Web UI")
print("  http://localhost:8000\n")
uvicorn.run(app, host="0.0.0.0", port=8000)
