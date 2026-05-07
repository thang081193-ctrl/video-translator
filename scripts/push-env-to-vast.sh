#!/usr/bin/env bash
# Push local .env to a Vast.ai instance over scp.
#
# Why this exists:
#   .env holds GEMINI_API_KEYS and lives ONLY on the local PC (gitignored).
#   When deploying to a fresh Vast.ai instance, those keys need to land at
#   /workspace/video-translator/.env without being typed/pasted into chat
#   or committed to git.
#
# Usage:
#   bash scripts/push-env-to-vast.sh <vast-host> [vast-port]
#
# Examples:
#   bash scripts/push-env-to-vast.sh ssh4.vast.ai 12345
#   bash scripts/push-env-to-vast.sh root@ssh4.vast.ai 12345
#
# Get <vast-host> + <vast-port> from the Vast.ai dashboard "SSH" button on
# your instance card (or your ~/.ssh/config if you have an alias set up).
#
# Idempotent — re-running just overwrites the remote .env. Safe to run
# after rotating local keys.

set -euo pipefail

if [ $# -lt 1 ]; then
  echo "ERROR: missing arguments" >&2
  echo "Usage: $0 <vast-host> [vast-port]" >&2
  echo "Example: $0 ssh4.vast.ai 12345" >&2
  exit 1
fi

HOST="$1"
PORT="${2:-22}"
# Resolve repo root from script location (script is in scripts/, env is at root)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCAL_ENV="$(dirname "$SCRIPT_DIR")/.env"
REMOTE_PATH="/workspace/video-translator/.env"

if [ ! -f "$LOCAL_ENV" ]; then
  echo "ERROR: $LOCAL_ENV not found" >&2
  echo "Create one with at minimum: GEMINI_API_KEYS=key1,key2" >&2
  exit 1
fi

# Strip "root@" prefix if user included it — scp wants host alone here.
HOST_BARE="${HOST#root@}"

echo "→ Pushing .env to root@${HOST_BARE}:${REMOTE_PATH} (port ${PORT})"
scp -P "$PORT" -o StrictHostKeyChecking=accept-new "$LOCAL_ENV" "root@${HOST_BARE}:${REMOTE_PATH}"

echo "✓ Push complete. Verify on Vast:"
echo "  ssh -p $PORT root@${HOST_BARE} 'wc -l ${REMOTE_PATH}'"
echo
echo "Then restart the server (vastai-restart skill, or):"
echo "  ssh -p $PORT root@${HOST_BARE} 'cd /workspace/video-translator && pkill -f uvicorn; sleep 1 && nohup uvicorn web_app:app --host 127.0.0.1 --port 3456 &> server.log &'"
