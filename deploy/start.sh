#!/bin/bash
set -e

# Start nginx (reverse proxy + basic auth)
nginx

# Start Cloudflare Tunnel if token is set
if [ -n "$CF_TUNNEL_TOKEN" ]; then
    echo "Starting Cloudflare Tunnel..."
    cloudflared tunnel run --token "$CF_TUNNEL_TOKEN" &
fi

# Runtime preflight: GPU visibility and API keys
echo "Running preflight checks..."
python3 -m pipeline.preflight

# Start the app (single worker; job store is in-memory, not multi-process-safe)
echo "Starting Video Translator on port 3456..."
PRODUCTION=1 exec uvicorn web_app:app --host 0.0.0.0 --port 3456 --workers 1
