---
description: "Restart server on Vast.ai — git pull + dual-tunnel restart"
---

Give the user the restart command. `deploy/vastai-start.sh` already kills old processes (uvicorn, ngrok, cloudflared) before starting fresh, so restarting is the same as starting — just run it again.

**Docker restart** (force-pulls latest image, useful after GHA build):
```bash
docker pull ghcr.io/thang081193-ctrl/video-translator:latest
docker stop vt 2>/dev/null; docker rm vt 2>/dev/null
docker run --gpus all -d -p 3456:3456 \
  -e GEMINI_API_KEYS="$KEYS" \
  -e NGROK_AUTHTOKEN="$NGROK_TOKEN" \
  -e CONTAINER_ID="$VAST_CONTAINERLABEL" \
  -e CONTAINER_API_KEY="$VAST_API_KEY" \
  --name vt ghcr.io/thang081193-ctrl/video-translator:latest
docker exec vt bash deploy/vastai-start.sh
```

**Manual install restart** (git pull + restart):
```bash
cd /workspace/video-translator && git pull --ff-only && bash deploy/vastai-start.sh
```

If the user reports specific errors, help debug:
- **Port in use**: `fuser -k 3456/tcp` should fix it
- **Module not found**: re-run installer with `SKIP_STEP=3` to redo pip install
- **CUDA OOM**: check `nvidia-smi`; remember that P6.A adds sticky GPU→CPU fallback, so after one OOM the whole process will use CPU until restart
- **Ngrok auth error**: `ngrok config add-authtoken $NGROK_TOKEN` (get token from https://dashboard.ngrok.com/get-started/your-authtoken)
- **Cloudflared rate-limited**: trycloudflare quick tunnels have ~50/hour per IP limit. Wait 10 min or set `NO_CLOUDFLARED=1` and use ngrok only
