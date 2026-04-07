---
description: "Start server + ngrok tunnel on Vast.ai — kills old processes, starts uvicorn + ngrok"
---

Give the user the start command for their Vast.ai instance. This kills any existing processes and starts fresh.

**Single command — Start server + ngrok tunnel:**
```
pkill -f uvicorn; pkill -f ngrok; sleep 1 && cd /workspace/video-translator && uvicorn web_app:app --host 127.0.0.1 --port 3456 & sleep 3 && ngrok http 127.0.0.1:3456
```

**Key notes:**
- Use `127.0.0.1` not `localhost` (avoids IPv6 issues)
- Ngrok shows URL in the terminal dashboard — look for `Forwarding https://xxxx.ngrok-free.app`
- If "address already in use" error: run `fuser -k 3456/tcp` then retry
- Do NOT use Vast.ai's built-in Tunnel UI or cloudflared — unreliable, hay bi rate limit
- Ngrok free hien trang "Visit Site" phai click qua 1 lan
