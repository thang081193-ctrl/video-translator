---
description: "Check Vast.ai instance status — GPU, processes, disk, server health, tunnel URLs, logs"
---

Give the user diagnostic commands to check their Vast.ai instance. Paste these into Jupyter Terminal.

**All-in-one status check:**
```bash
echo "=== GPU ===" && nvidia-smi --query-gpu=name,memory.used,memory.total,utilization.gpu --format=csv,noheader && \
echo "=== DISK ===" && df -h /workspace && \
echo "=== PROCESSES ===" && ps aux | grep -E "uvicorn|ngrok|cloudflared" | grep -v grep && \
echo "=== SERVER ===" && curl -s http://127.0.0.1:3456/api/health 2>/dev/null || echo "Server not running" && \
echo "=== /api/gpu (P6.C) ===" && curl -s http://127.0.0.1:3456/api/gpu 2>/dev/null | jq . 2>/dev/null || echo "(P6.C endpoint not yet available)" && \
echo "=== NGROK URL ===" && curl -s http://127.0.0.1:4040/api/tunnels 2>/dev/null | jq -r '.tunnels[0].public_url' 2>/dev/null || echo "(ngrok not running)" && \
echo "=== CLOUDFLARED URL ===" && grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' /workspace/logs/cloudflared.log 2>/dev/null | tail -1 || echo "(cloudflared not running)" && \
echo "=== JOBS ===" && ls -lt /workspace/video-translator/uploads/ 2>/dev/null | head -10 || echo "No uploads dir"
```

**Individual checks:**

- **GPU memory**: `nvidia-smi --query-gpu=memory.used,memory.total,utilization.gpu --format=csv`
- **Sticky GPU flag (P6.A)**: Check server logs for `GPU disabled for the rest of this process:` — if you see this, next job will run on CPU (slower)
- **Tunnel logs**: `tail -20 /workspace/logs/{uvicorn,ngrok,cloudflared}.log`
- **Auto-stop status (P6.C)**: `curl -s http://127.0.0.1:3456/api/gpu | jq '.force_cpu_sticky'` — returns `true` if the sticky GPU flag has been tripped
- **Job queue**: `ls /workspace/video-translator/uploads/ | wc -l`
- **Whisper model cache size**: look for `Reusing cached Whisper model` vs `Loading Whisper model` in logs

**Common issues:**
- **Server not responding**: `bash /workspace/video-translator/deploy/vastai-start.sh` to restart
- **Port conflict**: `fuser -k 3456/tcp` then restart
- **Tunnel rate-limited**: cloudflared has ~50/hour limit, wait 10 min or use `NO_CLOUDFLARED=1` to only use ngrok
