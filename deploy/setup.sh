#!/usr/bin/env bash
# deploy/setup.sh
# ---------------------------------------------------------------------------
# ONE idempotent setup pass for the Vast.ai cu130 box (or the Dockerfile.vast
# build layer). Re-runnable: apt is no-op when satisfied, pip respects pins,
# model prefetch is skipped by the loaders if the cache already exists.
#
# Order (all in one pass, fail-fast):
#   1. apt: ffmpeg + CJK/Thai/DejaVu fonts + build-essential + jq (+ curl/git/ca-certs)
#   2. VERIFY torch cuda (exit 7 if not available — do NOT reinstall torch)
#   3. pip -r requirements-vast.txt  (NOT torch)
#   4. MODEL PREFETCH: whisper tiny+small+medium + demucs htdemucs
#                      + easyocr en,vi,ch_sim,ja,ko   (all CPU int8 for whisper)
#   5. edge-tts reachability smoke (fail HERE, not mid-render)
#
# This makes the box "never stop to INSTALL or DOWNLOAD". It does NOT make it
# crash-proof against a Demucs hang or an edge-tts outage (runtime-service risks).
# ---------------------------------------------------------------------------
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
export HF_HUB_DOWNLOAD_TIMEOUT=600
export PYTHONIOENCODING=utf-8

HERE="$(cd "$(dirname "$0")" && pwd)"
REQ="${REQ:-$HERE/../requirements-vast.txt}"
[ -f "$REQ" ] || REQ="$HERE/requirements-vast.txt"   # Docker build copies it next to setup.sh
[ -f "$REQ" ] || { echo "FATAL: requirements-vast.txt not found (looked near $HERE)"; exit 1; }

say(){ echo "===== [setup] $* ====="; }

# ---- 1) apt: fonts + ffmpeg + build-essential + jq -------------------------
# fonts-noto-cjk -> CJK ; fonts-noto-extra -> Bengali/Thai/Devanagari/Arabic/etc.
say "apt: ffmpeg + fonts + build-essential + jq"
apt-get update -qq
apt-get install -y --no-install-recommends \
    ffmpeg \
    fonts-noto-cjk fonts-noto-core fonts-noto-extra fonts-dejavu-core \
    build-essential jq curl git ca-certificates
rm -rf /var/lib/apt/lists/*

# ---- 2) torch: VERIFY ONLY (base ships cu130) — never reinstall ------------
# A cu121 reinstall on a cu130 box silently CPU-falls the brandpass gate + Demucs.
say "verify torch.cuda (exit 7 if CUDA unavailable — NOT reinstalling torch)"
python3 - <<'PY'
import sys
try:
    import torch
except Exception as e:
    print(f"FATAL: torch import failed: {e}")
    sys.exit(7)
print("torch", torch.__version__, "cuda_available", torch.cuda.is_available())
if not torch.cuda.is_available():
    print("FATAL: torch.cuda.is_available() is False on a GPU box.")
    print("       This is the cu121-on-cu130 silent-CPU bug. Do NOT reinstall torch;")
    print("       rent/build on the correct cu130 base image.")
    sys.exit(7)
PY

# ---- 3) pip the lock (NOT torch) -------------------------------------------
say "pip install -r $REQ (torch excluded by design)"
python3 -m pip install --no-cache-dir -r "$REQ"

# ---- 4) MODEL PREFETCH -----------------------------------------------------
# Prefetch what the CODE actually loads so the first render is fully offline:
#   whisper: tiny (QA gate) + small (run.py default) + medium  -> all CPU int8
#   demucs : htdemucs
#   easyocr: en,vi,ch_sim,ja,ko  (detector maps zh->ch_sim,ja,ko; NOT 'th' -> en reader)
# large-v3 is OPTIONAL (huge; only on explicit --whisper large-v3) -> set PREFETCH_LARGE_V3=1.
say "prefetch models (whisper tiny+small+medium, demucs htdemucs, easyocr packs)"
PREFETCH_LARGE_V3="${PREFETCH_LARGE_V3:-0}" python3 - <<'PY'
import os
from faster_whisper import WhisperModel
tags = ["tiny", "small", "medium"]
if os.environ.get("PREFETCH_LARGE_V3") == "1":
    tags.append("large-v3")
for m in tags:
    WhisperModel(m, device="cpu", compute_type="int8")
    print("whisper", m, "ok", flush=True)

from demucs.api import Separator
Separator("htdemucs")
print("demucs htdemucs ok", flush=True)

import easyocr
# detector.py routes zh->ch_sim, plus ja, ko, and en/vi. Thai is NOT requested
# (Thai text routes to the 'en' reader), so do NOT prefetch a 'th' pack.
easyocr.Reader(["en", "vi", "ch_sim", "ja", "ko"])
print("easyocr en,vi,ch_sim,ja,ko ok", flush=True)
PY

# ---- 5) edge-tts reachability smoke ----------------------------------------
# Fail HERE (no model to cache) rather than mid-render. Non-fatal-by-design? No:
# edge-tts is the dub/voiceover SPOF, so a build-time outage should be surfaced.
say "edge-tts reachability smoke"
python3 - <<'PY'
import asyncio, edge_tts
async def _smoke():
    c = edge_tts.Communicate("hi", "en-US-AriaNeural")
    await c.save("/tmp/_edge_tts_smoke.mp3")
asyncio.run(_smoke())
print("edge-tts reachable")
PY
rm -f /tmp/_edge_tts_smoke.mp3

say "setup complete"
