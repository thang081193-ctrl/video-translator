---
description: "Restart server on Vast.ai when it hangs or crashes"
---

Give the user the restart command. This force-kills everything and starts fresh.

```
pkill -9 -f uvicorn; pkill -9 -f ngrok; fuser -k 3456/tcp 2>/dev/null; sleep 2 && cd /workspace/video-translator && git pull && uvicorn web_app:app --host 127.0.0.1 --port 3456 & sleep 3 && ngrok http 127.0.0.1:3456
```

This also does `git pull` to get latest code changes.

If the user reports specific errors, help debug them. Common issues:
- **Port in use**: `fuser -k 3456/tcp` should fix it
- **Module not found**: pip install the missing package
- **CUDA OOM**: Check if another process is using GPU with `nvidia-smi`
- **Ngrok auth error**: `ngrok config add-authtoken <TOKEN>` (get token from https://dashboard.ngrok.com/get-started/your-authtoken)
