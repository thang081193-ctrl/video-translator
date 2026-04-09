#!/bin/bash
#
# Video Translator — Vast.ai dual-tunnel start script (P6.B)
#
# Starts uvicorn on 127.0.0.1:3456 + ngrok + cloudflared trycloudflare
# quick tunnel in parallel. Prints both tunnel URLs so users can pick
# whichever responds (cloudflared has no "Visit Site" interstitial,
# ngrok provides a fallback if cloudflared is rate-limited).
#
# USAGE:
#   bash deploy/vastai-start.sh
#
# Logs: /workspace/logs/{uvicorn,ngrok,cloudflared}.log
# Stop: pkill -f uvicorn ; pkill -f ngrok ; pkill -f cloudflared
#
# Env vars (optional):
#   PORT              Local port (default 3456)
#   NO_NGROK=1        Skip ngrok (use only cloudflared)
#   NO_CLOUDFLARED=1  Skip cloudflared (use only ngrok)

set -euo pipefail

# ─── Constants ───────────────────────────────────────────────────────────────
INSTALL_DIR="/workspace/video-translator"
LOG_DIR="/workspace/logs"
PORT="${PORT:-3456}"
NO_NGROK="${NO_NGROK:-0}"
NO_CLOUDFLARED="${NO_CLOUDFLARED:-0}"

mkdir -p "$LOG_DIR"

# ─── 1. Kill old processes ───────────────────────────────────────────────────
echo "[1/4] Killing old processes (uvicorn, ngrok, cloudflared)..."
pkill -f uvicorn 2>/dev/null || true
pkill -f ngrok 2>/dev/null || true
pkill -f "cloudflared tunnel" 2>/dev/null || true
sleep 1

# ─── 2. Start uvicorn ────────────────────────────────────────────────────────
echo "[2/4] Starting uvicorn on 127.0.0.1:$PORT..."
cd "$INSTALL_DIR"
nohup python3 -m uvicorn web_app:app \
    --host 127.0.0.1 --port "$PORT" \
    > "$LOG_DIR/uvicorn.log" 2>&1 &
UVICORN_PID=$!

# Wait for /api/health
SUCCESS=0
for i in {1..30}; do
    if curl -sf "http://127.0.0.1:$PORT/api/health" > /dev/null 2>&1; then
        echo "  ✓ uvicorn ready (PID $UVICORN_PID, after ${i}s)"
        SUCCESS=1
        break
    fi
    sleep 1
done

if [[ "$SUCCESS" != "1" ]]; then
    echo "  ✗ uvicorn failed to start — see $LOG_DIR/uvicorn.log" >&2
    tail -30 "$LOG_DIR/uvicorn.log" >&2
    exit 1
fi

# ─── 3. Start tunnels in parallel ────────────────────────────────────────────
NGROK_PID=""
CF_PID=""

if [[ "$NO_NGROK" != "1" ]]; then
    if command -v ngrok >/dev/null 2>&1; then
        echo "[3/4] Starting ngrok..."
        nohup ngrok http "127.0.0.1:$PORT" \
            --log=stdout --log-format=json \
            > "$LOG_DIR/ngrok.log" 2>&1 &
        NGROK_PID=$!
    else
        echo "[3/4] ngrok binary not found, skipping (set NO_NGROK=1 to silence)"
    fi
fi

if [[ "$NO_CLOUDFLARED" != "1" ]]; then
    if command -v cloudflared >/dev/null 2>&1; then
        echo "[3/4] Starting cloudflared trycloudflare quick tunnel..."
        nohup cloudflared tunnel \
            --url "http://127.0.0.1:$PORT" \
            --no-autoupdate \
            > "$LOG_DIR/cloudflared.log" 2>&1 &
        CF_PID=$!
    else
        echo "[3/4] cloudflared binary not found, skipping (set NO_CLOUDFLARED=1 to silence)"
    fi
fi

# ─── 4. Parse tunnel URLs ────────────────────────────────────────────────────
echo "[4/4] Waiting for tunnel URLs (max 30s each)..."
NGROK_URL=""
CF_URL=""

for i in {1..30}; do
    if [[ -n "$NGROK_PID" && -z "$NGROK_URL" ]]; then
        # Ngrok exposes its API on 127.0.0.1:4040
        NGROK_URL=$(curl -s http://127.0.0.1:4040/api/tunnels 2>/dev/null \
            | grep -oE '"public_url":"https://[^"]+"' \
            | head -1 \
            | sed 's/"public_url":"//; s/"$//' || true)
    fi
    if [[ -n "$CF_PID" && -z "$CF_URL" ]]; then
        # Cloudflared prints "https://*.trycloudflare.com" to its log
        CF_URL=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' \
            "$LOG_DIR/cloudflared.log" 2>/dev/null | head -1 || true)
    fi
    # Both ready (or both skipped) — we're done waiting
    if [[ ( -n "$NGROK_URL" || -z "$NGROK_PID" ) && ( -n "$CF_URL" || -z "$CF_PID" ) ]]; then
        break
    fi
    sleep 1
done

# ─── Print summary ───────────────────────────────────────────────────────────
cat <<EOF

═══════════════════════════════════════════════════════════════
  Video Translator — running

  Local:                http://127.0.0.1:$PORT
EOF

if [[ -n "$NGROK_PID" ]]; then
    echo "  Tunnel 1 (ngrok):     ${NGROK_URL:-FAILED — check $LOG_DIR/ngrok.log}"
fi
if [[ -n "$CF_PID" ]]; then
    echo "  Tunnel 2 (cloudflared): ${CF_URL:-FAILED — check $LOG_DIR/cloudflared.log}"
fi

cat <<EOF

  Logs:  $LOG_DIR/{uvicorn,ngrok,cloudflared}.log
  PIDs:  uvicorn=$UVICORN_PID${NGROK_PID:+, ngrok=$NGROK_PID}${CF_PID:+, cloudflared=$CF_PID}
  Stop:  pkill -f uvicorn ; pkill -f ngrok ; pkill -f cloudflared
═══════════════════════════════════════════════════════════════
EOF

# ─── External health check (best-effort) ─────────────────────────────────────
if [[ -n "$NGROK_URL" ]]; then
    if curl -sf "$NGROK_URL/api/health" > /dev/null 2>&1; then
        echo "  ✓ ngrok URL responding externally"
    else
        echo "  ⚠ ngrok URL not yet reachable externally (DNS warmup or 'Visit Site' interstitial — try in a browser)"
    fi
fi
if [[ -n "$CF_URL" ]]; then
    if curl -sf "$CF_URL/api/health" > /dev/null 2>&1; then
        echo "  ✓ cloudflared URL responding externally"
    else
        echo "  ⚠ cloudflared URL not yet reachable externally (DNS warmup, retry in 10s)"
    fi
fi

# ─── Trap signals so children get killed if user Ctrl+C ──────────────────────
cleanup() {
    echo ""
    echo "Shutting down..."
    [[ -n "$UVICORN_PID" ]] && kill "$UVICORN_PID" 2>/dev/null || true
    [[ -n "$NGROK_PID" ]] && kill "$NGROK_PID" 2>/dev/null || true
    [[ -n "$CF_PID" ]] && kill "$CF_PID" 2>/dev/null || true
    exit 0
}
trap cleanup SIGTERM SIGINT

# Keep script alive so children don't get reaped (e.g. if running as PID 1)
echo ""
echo "Script will keep running to maintain background processes."
echo "Press Ctrl+C to stop everything, or detach with Ctrl+Z + 'bg' + 'disown'."
wait
