---
description: "Check Vast.ai instance status — GPU, processes, disk, server health"
---

Give the user diagnostic commands to check their Vast.ai instance. Paste these into Jupyter Terminal:

**All-in-one status check:**
```
echo "=== GPU ===" && nvidia-smi --query-gpu=name,memory.used,memory.total,utilization.gpu --format=csv,noheader && echo "=== DISK ===" && df -h /workspace && echo "=== PROCESSES ===" && ps aux | grep -E "uvicorn|cloudflared" | grep -v grep && echo "=== SERVER ===" && curl -s http://127.0.0.1:3456/api/debug 2>/dev/null || echo "Server not running" && echo "=== JOBS ===" && ls -lt /workspace/video-translator/uploads/ 2>/dev/null | head -10 || echo "No uploads dir"
```

This shows: GPU memory usage, disk space, running processes, server health, and recent jobs.
