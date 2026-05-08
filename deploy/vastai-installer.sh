#!/bin/bash
#
# Video Translator — Vast.ai manual installer (P6.B)
#
# Idempotent 9-step installer with resume support, progress feedback,
# and clear error messages. For users who don't want to use the Docker
# image at ghcr.io/thang081193-ctrl/video-translator:latest.
#
# USAGE:
#   NGROK_TOKEN=2abc... GEMINI_KEYS=key1,key2 bash vastai-installer.sh
#
#   # Or remote one-liner (when image visibility is set Public):
#   NGROK_TOKEN=2abc... GEMINI_KEYS=key1,key2 bash <(curl -fsSL \
#     https://raw.githubusercontent.com/thang081193-ctrl/video-translator/main/deploy/vastai-installer.sh)
#
# ENV VARS (use env vars, NOT command flags — keys would leak into bash history):
#   NGROK_TOKEN=...    REQUIRED — ngrok authtoken
#   GEMINI_KEYS=...    REQUIRED — comma-separated Gemini API keys
#   GROK_KEYS=...      OPTIONAL — comma-separated Grok API keys (xai-... prefix)
#   VERTEX_KEYS=...    OPTIONAL — comma-separated Vertex AI keys
#   SKIP_STEP=N        OPTIONAL — skip steps 1..N (resume from step N+1)
#   RESET=1            OPTIONAL — clear all sentinel files (force re-run from scratch)
#   DRY_RUN=1          OPTIONAL — print step labels but don't execute
#
# FLAGS:
#   --help             Show this help and exit
#
# After successful install, run:
#   bash deploy/vastai-start.sh
#
# Phase 6.B — see docs/execution/reports/P6.B-cold-start.md

set -euo pipefail

# ─── Constants ───────────────────────────────────────────────────────────────
REPO_URL="https://github.com/thang081193-ctrl/video-translator.git"
INSTALL_DIR="/workspace/video-translator"
SENTINEL_DIR="/workspace/.vt-installer"
LOG_DIR="/workspace/logs"
TOTAL_STEPS=9

# ─── Help ────────────────────────────────────────────────────────────────────
if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
    grep -E '^# (USAGE|ENV VARS|FLAGS|After)' "$0" | sed 's/^# //'
    echo
    echo "Steps:"
    echo "  [1/$TOTAL_STEPS] apt install system deps (ffmpeg, curl, git, fonts-noto-*)"
    echo "  [2/$TOTAL_STEPS] pip install torch + cu121 (~2GB, slow)"
    echo "  [3/$TOTAL_STEPS] git clone repo into $INSTALL_DIR"
    echo "  [4/$TOTAL_STEPS] pip install -r requirements.txt"
    echo "  [5/$TOTAL_STEPS] Install ngrok binary + add authtoken"
    echo "  [6/$TOTAL_STEPS] Install cloudflared binary"
    echo "  [7/$TOTAL_STEPS] Pre-download ML models (Whisper medium+large-v3, EasyOCR en+vi, Demucs htdemucs) WITH PROGRESS"
    echo "  [8/$TOTAL_STEPS] Write .env from \$GEMINI_KEYS / \$GROK_KEYS / \$VERTEX_KEYS"
    echo "  [9/$TOTAL_STEPS] Health check (start server + curl /api/health)"
    exit 0
fi

# ─── Validate required env vars ──────────────────────────────────────────────
NGROK_TOKEN="${NGROK_TOKEN:-}"
GEMINI_KEYS="${GEMINI_KEYS:-}"
GROK_KEYS="${GROK_KEYS:-}"
VERTEX_KEYS="${VERTEX_KEYS:-}"
SKIP_STEP="${SKIP_STEP:-0}"
RESET="${RESET:-0}"
DRY_RUN="${DRY_RUN:-0}"

if [[ -z "$NGROK_TOKEN" ]]; then
    echo "ERROR: NGROK_TOKEN env var is required" >&2
    echo "  Get one at https://dashboard.ngrok.com/get-started/your-authtoken" >&2
    exit 1
fi

if [[ -z "$GEMINI_KEYS" && -z "$GROK_KEYS" && -z "$VERTEX_KEYS" ]]; then
    echo "ERROR: at least one of GEMINI_KEYS / GROK_KEYS / VERTEX_KEYS is required" >&2
    exit 1
fi

# ─── Setup ───────────────────────────────────────────────────────────────────
mkdir -p "$SENTINEL_DIR" "$LOG_DIR"

if [[ "$RESET" == "1" ]]; then
    echo "RESET=1 — clearing sentinel files at $SENTINEL_DIR"
    rm -rf "$SENTINEL_DIR"/*.done
fi

# Track current step for error reporting
CURRENT_STEP=0
CURRENT_LABEL=""

on_error() {
    local lineno=$1
    local exit_code=$2
    echo "" >&2
    echo "═══════════════════════════════════════════════════════════════" >&2
    echo "ERROR at line $lineno (exit code $exit_code)" >&2
    echo "Failed during: [$CURRENT_STEP/$TOTAL_STEPS] $CURRENT_LABEL" >&2
    echo "" >&2
    echo "To resume from this step:" >&2
    echo "  SKIP_STEP=$((CURRENT_STEP - 1)) NGROK_TOKEN=... GEMINI_KEYS=... bash $0" >&2
    echo "" >&2
    echo "To start completely over:" >&2
    echo "  RESET=1 NGROK_TOKEN=... GEMINI_KEYS=... bash $0" >&2
    echo "═══════════════════════════════════════════════════════════════" >&2
    exit "$exit_code"
}
trap 'on_error $LINENO $?' ERR

# ─── Helper: run a step (idempotent via sentinel) ────────────────────────────
run_step() {
    local step_num=$1
    local label=$2
    local sentinel="$SENTINEL_DIR/step-${step_num}.done"
    CURRENT_STEP=$step_num
    CURRENT_LABEL=$label

    if [[ "$step_num" -le "$SKIP_STEP" ]]; then
        echo "[${step_num}/${TOTAL_STEPS}] $label (SKIPPED via SKIP_STEP=$SKIP_STEP)"
        return 0
    fi

    if [[ -f "$sentinel" ]]; then
        echo "[${step_num}/${TOTAL_STEPS}] $label (already done — sentinel exists)"
        return 0
    fi

    echo "[${step_num}/${TOTAL_STEPS}] $label..."
    if [[ "$DRY_RUN" == "1" ]]; then
        echo "  (DRY_RUN — not executing)"
        return 0
    fi

    # The step body is whatever comes after the run_step call. We just
    # mark the sentinel after the caller's commands return successfully.
    # (Caller is responsible for actually running the work + touching sentinel.)
}

mark_done() {
    touch "$SENTINEL_DIR/step-${CURRENT_STEP}.done"
}

# ─── Step 1: apt install system deps ─────────────────────────────────────────
run_step 1 "Installing system packages (ffmpeg, fonts-noto-*, curl, git)"
if [[ "$DRY_RUN" != "1" && ! -f "$SENTINEL_DIR/step-1.done" && "$SKIP_STEP" -lt 1 ]]; then
    apt-get update -qq
    apt-get install -y --no-install-recommends \
        ffmpeg curl git ca-certificates jq \
        fonts-noto-cjk fonts-noto-core fonts-noto-extra fonts-dejavu-core \
        fonts-noto-ui-core fonts-noto-unhinted \
        2>&1 | tee "$LOG_DIR/step1-apt.log" | tail -5
    rm -rf /var/lib/apt/lists/*
    mark_done
fi

# ─── Step 2: pip install torch + cu121 ───────────────────────────────────────
run_step 2 "Installing PyTorch + CUDA 12.1 wheels (~2GB, may take 2-5 min)"
if [[ "$DRY_RUN" != "1" && ! -f "$SENTINEL_DIR/step-2.done" && "$SKIP_STEP" -lt 2 ]]; then
    pip install --progress-bar on \
        torch torchaudio --index-url https://download.pytorch.org/whl/cu121 \
        2>&1 | tee "$LOG_DIR/step2-torch.log" | tail -10
    mark_done
fi

# ─── Step 3: git clone repo ──────────────────────────────────────────────────
run_step 3 "Cloning repository to $INSTALL_DIR"
if [[ "$DRY_RUN" != "1" && ! -f "$SENTINEL_DIR/step-3.done" && "$SKIP_STEP" -lt 3 ]]; then
    if [[ -d "$INSTALL_DIR/.git" ]]; then
        echo "  Repo already exists, pulling latest..."
        git -C "$INSTALL_DIR" pull --ff-only 2>&1 | tee "$LOG_DIR/step3-git.log" | tail -5
    else
        git clone --depth 1 "$REPO_URL" "$INSTALL_DIR" 2>&1 | tee "$LOG_DIR/step3-git.log" | tail -5
    fi
    mark_done
fi

# ─── Step 4: pip install requirements.txt ────────────────────────────────────
run_step 4 "Installing app dependencies (requirements.txt)"
if [[ "$DRY_RUN" != "1" && ! -f "$SENTINEL_DIR/step-4.done" && "$SKIP_STEP" -lt 4 ]]; then
    pip install --progress-bar on -r "$INSTALL_DIR/requirements.txt" \
        2>&1 | tee "$LOG_DIR/step4-deps.log" | tail -10
    mark_done
fi

# ─── Step 5: Install ngrok binary + authtoken ────────────────────────────────
run_step 5 "Installing ngrok binary + configuring authtoken"
if [[ "$DRY_RUN" != "1" && ! -f "$SENTINEL_DIR/step-5.done" && "$SKIP_STEP" -lt 5 ]]; then
    if ! command -v ngrok >/dev/null 2>&1; then
        curl -fsSL https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-linux-amd64.tgz \
            | tar xz -C /usr/local/bin
        chmod +x /usr/local/bin/ngrok
    fi
    ngrok config add-authtoken "$NGROK_TOKEN" 2>&1 | tee "$LOG_DIR/step5-ngrok.log"
    mark_done
fi

# ─── Step 6: Install cloudflared binary ──────────────────────────────────────
run_step 6 "Installing cloudflared binary (for trycloudflare quick tunnel)"
if [[ "$DRY_RUN" != "1" && ! -f "$SENTINEL_DIR/step-6.done" && "$SKIP_STEP" -lt 6 ]]; then
    if ! command -v cloudflared >/dev/null 2>&1; then
        curl -fsSL -o /usr/local/bin/cloudflared \
            https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64
        chmod +x /usr/local/bin/cloudflared
    fi
    cloudflared --version 2>&1 | tee "$LOG_DIR/step6-cloudflared.log"
    mark_done
fi

# ─── Step 7: Pre-download ML models WITH PROGRESS ────────────────────────────
run_step 7 "Pre-downloading ML models (Whisper, EasyOCR, Demucs) — ~5GB total"
if [[ "$DRY_RUN" != "1" && ! -f "$SENTINEL_DIR/step-7.done" && "$SKIP_STEP" -lt 7 ]]; then
    # Write a Python helper that does each download with visible logging.
    # Forces line-buffered output via python3 -u + flush=True.
    cat > /tmp/vt-download-models.py <<'PYEOF'
"""Download all ML models with progress feedback. Used by vastai-installer.sh step 7."""
import sys
import time

def step(label):
    sys.stdout.write(f"  → {label}...\n")
    sys.stdout.flush()
    return time.time()

def done(label, t0):
    elapsed = time.time() - t0
    sys.stdout.write(f"  ✓ {label} done ({elapsed:.0f}s)\n")
    sys.stdout.flush()

t0 = step("Whisper medium (~1.5GB)")
from faster_whisper import WhisperModel
WhisperModel("medium", device="cpu")
done("Whisper medium", t0)

t0 = step("Whisper large-v3 (~3GB)")
WhisperModel("large-v3", device="cpu")
done("Whisper large-v3", t0)

t0 = step("EasyOCR English reader (~500MB)")
import easyocr
easyocr.Reader(['en'])
done("EasyOCR English", t0)

t0 = step("EasyOCR Vietnamese reader (~500MB)")
easyocr.Reader(['vi'])
done("EasyOCR Vietnamese", t0)

t0 = step("Demucs htdemucs (~80MB)")
try:
    from demucs.api import Separator
    Separator(model="htdemucs")
except ImportError:
    from demucs import pretrained
    pretrained.get_model("htdemucs")
done("Demucs htdemucs", t0)

print("✓ All models cached successfully.")
PYEOF
    TERM=xterm-256color python3 -u /tmp/vt-download-models.py \
        2>&1 | tee "$LOG_DIR/step7-models.log"
    rm /tmp/vt-download-models.py
    mark_done
fi

# ─── Step 8: Write .env file ─────────────────────────────────────────────────
run_step 8 "Writing .env file with API keys"
if [[ "$DRY_RUN" != "1" && ! -f "$SENTINEL_DIR/step-8.done" && "$SKIP_STEP" -lt 8 ]]; then
    ENV_FILE="$INSTALL_DIR/.env"
    {
        echo "# Auto-generated by vastai-installer.sh on $(date -Iseconds)"
        [[ -n "$GEMINI_KEYS" ]] && echo "GEMINI_API_KEYS=$GEMINI_KEYS"
        [[ -n "$GROK_KEYS" ]] && echo "GROK_API_KEYS=$GROK_KEYS"
        [[ -n "$VERTEX_KEYS" ]] && echo "VERTEX_API_KEYS=$VERTEX_KEYS"
        # NVENC: Vast.ai consumer GPUs (RTX 30xx/40xx) typically have ffmpeg
        # h264_nvenc compiled in but the host driver blocks OpenEncodeSessionEx
        # ("unsupported device (2)") — convert/burn fail with exit 187. Force
        # libx264 by default. Override with USE_NVENC=true if your host supports it.
        echo "USE_NVENC=false"
    } > "$ENV_FILE"
    chmod 600 "$ENV_FILE"
    echo "  → wrote $ENV_FILE (mode 600)"
    mark_done
fi

# ─── Step 9: Health check ────────────────────────────────────────────────────
run_step 9 "Health check (starting server, curl /api/health)"
if [[ "$DRY_RUN" != "1" && ! -f "$SENTINEL_DIR/step-9.done" && "$SKIP_STEP" -lt 9 ]]; then
    cd "$INSTALL_DIR"
    pkill -f uvicorn 2>/dev/null || true
    sleep 1
    nohup python3 -m uvicorn web_app:app --host 127.0.0.1 --port 3456 \
        > "$LOG_DIR/uvicorn-healthcheck.log" 2>&1 &
    SERVER_PID=$!

    SUCCESS=0
    for i in {1..30}; do
        if curl -sf http://127.0.0.1:3456/api/health > /dev/null 2>&1; then
            echo "  ✓ /api/health responding (after ${i}s)"
            SUCCESS=1
            break
        fi
        sleep 1
    done

    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true

    if [[ "$SUCCESS" != "1" ]]; then
        echo "  ✗ Health check FAILED — see $LOG_DIR/uvicorn-healthcheck.log" >&2
        tail -20 "$LOG_DIR/uvicorn-healthcheck.log" >&2
        exit 1
    fi
    mark_done
fi

# ─── Done ────────────────────────────────────────────────────────────────────
cat <<EOF

═══════════════════════════════════════════════════════════════
  ✓ Video Translator installed successfully

  Next: bash $INSTALL_DIR/deploy/vastai-start.sh
  Logs: $LOG_DIR/
  Repo: $INSTALL_DIR
═══════════════════════════════════════════════════════════════
EOF
