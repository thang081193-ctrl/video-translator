---
description: "Start server + dual tunnels (ngrok + cloudflared trycloudflare) on Vast.ai"
---

Give the user the start command. This runs the `deploy/vastai-start.sh` script which kills existing processes, starts uvicorn, then starts ngrok AND cloudflared in parallel for redundancy. Two URLs are printed so the user can pick whichever responds (cloudflared has no "Visit Site" click, ngrok is the fallback if cloudflared rate-limits).

**Docker setup** (one line):
```bash
docker exec vt bash deploy/vastai-start.sh
```

**Manual install setup** (one line):
```bash
cd /workspace/video-translator && bash deploy/vastai-start.sh
```

The script prints a summary box with both tunnel URLs, e.g.:
```
═══════════════════════════════════════════════════════════════
  Video Translator — running
  Local:                http://127.0.0.1:3456
  Tunnel 1 (ngrok):     https://abc123.ngrok-free.app
  Tunnel 2 (cloudflared): https://random-name.trycloudflare.com
  Logs:  /workspace/logs/{uvicorn,ngrok,cloudflared}.log
═══════════════════════════════════════════════════════════════
```

**Key notes:**
- Uses `127.0.0.1` not `localhost` (avoids IPv6 issues on Vast.ai)
- **Cloudflared URL is preferred** — no "Visit Site" interstitial page
- **Ngrok URL is fallback** — free tier shows "Visit Site" page that must be clicked once
- Both tunnels run simultaneously — if cloudflared quick tunnel rate-limits, ngrok still works
- Optional env vars to disable one tunnel: `NO_NGROK=1` or `NO_CLOUDFLARED=1`
- If "address already in use" on port 3456: `fuser -k 3456/tcp` then re-run
