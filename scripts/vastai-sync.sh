#!/usr/bin/env bash
# Two-way file/dir sync with a Vast.ai instance over scp.
#
# Why this exists:
#   The /vast-meta-ultimate flow runs GPU phases (Whisper/Demucs/TTS render) on Vast
#   but the translate step happens in Claude Code on the local PC. Sources,
#   translations.jsonl, and finished videos need to hop between machines
#   without retyping scp flags every time.
#
# Usage:
#   bash scripts/vastai-sync.sh up   <vast-host> <vast-port> <local-path>  <remote-path>
#   bash scripts/vastai-sync.sh down <vast-host> <vast-port> <remote-path> <local-path>
#
# Examples:
#   bash scripts/vastai-sync.sh up   ssh4.vast.ai 12345 "D:/ads/batch0612" /workspace/jobs/batch0612
#   bash scripts/vastai-sync.sh down ssh4.vast.ai 12345 \
#     /workspace/jobs/batch0612/_super_saiyan/translations.jsonl \
#     "D:/ads/batch0612/_super_saiyan/translations.jsonl"
#   bash scripts/vastai-sync.sh down ssh4.vast.ai 12345 \
#     /workspace/jobs/batch0612/_translated_en "D:/ads/batch0612/_translated_en"
#
# Get <vast-host> + <vast-port> from the Vast.ai dashboard "SSH" button on
# your instance card (same as push-env-to-vast.sh).
#
# Works for both single files and directories. Directory copies are
# CONTENTS-into-destination (no nested extra folder on re-run), so the
# script is idempotent — re-running just overwrites. Destination parent
# dirs are created automatically on both ends.

set -euo pipefail

usage() {
  echo "Usage: $0 up   <vast-host> <vast-port> <local-path>  <remote-path>" >&2
  echo "       $0 down <vast-host> <vast-port> <remote-path> <local-path>" >&2
  echo "Example: $0 down ssh4.vast.ai 12345 /workspace/jobs/b1/_super_saiyan/translations.jsonl ./b1/_super_saiyan/translations.jsonl" >&2
  exit 1
}

if [ $# -ne 5 ]; then
  echo "ERROR: expected 5 arguments, got $#" >&2
  usage
fi

ACTION="$1"
# Strip "root@" prefix if user included it — we add it ourselves.
HOST_BARE="${2#root@}"
PORT="$3"
SSH_OPTS=(-o StrictHostKeyChecking=accept-new)

case "$ACTION" in
  up)
    LOCAL="$4"; REMOTE="$5"
    if [ ! -e "$LOCAL" ]; then
      echo "ERROR: local path not found: $LOCAL" >&2
      exit 1
    fi
    echo "→ up: $LOCAL → root@${HOST_BARE}:${REMOTE} (port ${PORT})"
    if [ -d "$LOCAL" ]; then
      # Contents-copy ("dir/.") so re-runs don't nest dir inside dir.
      ssh -p "$PORT" "${SSH_OPTS[@]}" "root@${HOST_BARE}" "mkdir -p \"$REMOTE\""
      scp -P "$PORT" "${SSH_OPTS[@]}" -r "$LOCAL/." "root@${HOST_BARE}:${REMOTE}/"
    else
      ssh -p "$PORT" "${SSH_OPTS[@]}" "root@${HOST_BARE}" "mkdir -p \"$(dirname "$REMOTE")\""
      scp -P "$PORT" "${SSH_OPTS[@]}" "$LOCAL" "root@${HOST_BARE}:${REMOTE}"
    fi
    ;;
  down)
    REMOTE="$4"; LOCAL="$5"
    echo "→ down: root@${HOST_BARE}:${REMOTE} → $LOCAL (port ${PORT})"
    if ssh -p "$PORT" "${SSH_OPTS[@]}" "root@${HOST_BARE}" "test -d \"$REMOTE\""; then
      mkdir -p "$LOCAL"
      scp -P "$PORT" "${SSH_OPTS[@]}" -r "root@${HOST_BARE}:${REMOTE}/." "$LOCAL/"
    else
      mkdir -p "$(dirname "$LOCAL")"
      scp -P "$PORT" "${SSH_OPTS[@]}" "root@${HOST_BARE}:${REMOTE}" "$LOCAL"
    fi
    ;;
  *)
    echo "ERROR: unknown action '$ACTION'" >&2
    usage
    ;;
esac

echo "✓ Sync complete."
