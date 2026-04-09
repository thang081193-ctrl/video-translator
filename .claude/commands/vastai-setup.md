---
description: "Setup Video Translator on a fresh Vast.ai GPU instance — Docker (recommended) or bash installer (with resume)"
---

Give the user TWO options for setup. Recommend Option A (Docker) — much faster (~3-5 min vs ~10-15 min) and zero stuck-points. Option B is the fallback for environments where Docker isn't available or the user prefers manual install.

**Ask first**: do they want Docker or manual install? Then ask for their tokens/keys.

---

## Option A — Docker (recommended, ~3-5 min)

**Prerequisite**: Vast.ai instance must support custom Docker images (most do). Image is published at `ghcr.io/thang081193-ctrl/video-translator:latest` (public, no auth needed).

```bash
docker run --gpus all -d \
  -p 3456:3456 \
  -e GEMINI_API_KEYS="key1,key2,key3" \
  -e NGROK_AUTHTOKEN="2abc..." \
  -e CONTAINER_ID="$VAST_CONTAINERLABEL" \
  -e CONTAINER_API_KEY="$VAST_API_KEY" \
  --name vt \
  ghcr.io/thang081193-ctrl/video-translator:latest

# Then start tunnels:
docker exec vt bash deploy/vastai-start.sh
```

The image has Whisper medium + large-v3, EasyOCR en+vi, Demucs htdemucs, ngrok, cloudflared, and all Python deps pre-baked. No download wait.

---

## Option B — Bash installer (manual install, ~10-15 min, with resume)

**For users who can't use Docker.** The installer is idempotent — if a step fails, re-run the same command and it skips completed steps.

```bash
NGROK_TOKEN=2abc... GEMINI_KEYS=key1,key2,key3 \
  bash <(curl -fsSL https://raw.githubusercontent.com/thang081193-ctrl/video-translator/main/deploy/vastai-installer.sh)
```

**Required env vars** (use env vars, NOT command flags — keys would leak into bash history):
- `NGROK_TOKEN` — get from https://dashboard.ngrok.com/get-started/your-authtoken
- `GEMINI_KEYS` — comma-separated Gemini API keys

**Optional env vars**:
- `GROK_KEYS` — comma-separated Grok keys (must start with `xai-`)
- `VERTEX_KEYS` — comma-separated Vertex AI keys
- `SKIP_STEP=N` — resume from step N+1
- `RESET=1` — clear sentinel files, force full re-run
- `DRY_RUN=1` — print step labels without executing

**Steps** (each idempotent via sentinel file in `/workspace/.vt-installer/`):
1. apt install system deps (ffmpeg, fonts-noto-*, curl, git)
2. pip install torch + cu121 (~2GB, 2-5 min)
3. git clone repo to `/workspace/video-translator`
4. pip install requirements.txt
5. Install ngrok binary + add authtoken
6. Install cloudflared binary
7. **Pre-download all ML models WITH PROGRESS** (Whisper medium+large-v3, EasyOCR en+vi, Demucs htdemucs)
8. Write `.env` from $GEMINI_KEYS / $GROK_KEYS / $VERTEX_KEYS
9. Health check (start server, curl /api/health)

If a step fails, re-run with `SKIP_STEP=<N-1>` to resume. If unsure where it failed, run with `RESET=1` to start over.

After install, start the server with `/vastai-start`.
