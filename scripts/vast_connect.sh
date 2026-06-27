#!/usr/bin/env bash
# scripts/vast_connect.sh — robustly wait until a Vast.ai box is SSH-ready,
# then print "HOST PORT" (and optionally exec a command over SSH).
# ---------------------------------------------------------------------------
# This absorbs the two transient states that previously looked like hard
# failures and made us destroy good boxes:
#   * "Permission denied (publickey)"  -> sshd up but authorized_keys not yet
#                                         written by onstart (retry, don't quit)
#   * "Connection closed by ... proxy" -> Vast proxy not yet routing to sshd
#                                         (retry, don't quit)
# With scripts/vast_rent.sh baking the key in at creation, readiness is just a
# matter of WAITING — this script makes the wait a checked invariant, not a
# guess. It resolves host:port from `vastai ssh-url` (falls back to
# `vastai show instance`), so it works for --direct and proxied instances.
#
# Usage:
#   scripts/vast_connect.sh <instance_id> [max_wait_sec]      # wait + print HOST PORT
#   scripts/vast_connect.sh <instance_id> [max_wait_sec] --exec 'remote cmd'
# Env: VAST_SSH_KEY (private key, default ~/.ssh/id_ed25519)
# ---------------------------------------------------------------------------
set -uo pipefail

ID="${1:?instance_id required}"
MAXW="${2:-600}"
EXEC=""
if [ "${3:-}" = "--exec" ]; then EXEC="${4:?--exec needs a command}"; fi
KEY="${VAST_SSH_KEY:-$HOME/.ssh/id_ed25519}"
SSHOPTS=(-o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=/dev/null
         -o ConnectTimeout=12 -o BatchMode=yes -o LogLevel=ERROR -i "$KEY")

resolve() {  # echo "HOST PORT" or nothing
  local url host port
  url="$(vastai ssh-url "$ID" 2>/dev/null | tr -d '\r')"
  # form: ssh://root@HOST:PORT
  if [[ "$url" =~ @([^:]+):([0-9]+) ]]; then
    echo "${BASH_REMATCH[1]} ${BASH_REMATCH[2]}"; return 0
  fi
  # fallback: parse `vastai show instance --raw`
  vastai show instance "$ID" --raw 2>/dev/null | python3 -c 'import sys,json
try: d=json.load(sys.stdin)
except Exception: sys.exit(1)
h=d.get("ssh_host") or d.get("public_ipaddr") or ""
p=d.get("ssh_port") or d.get("direct_port_start") or ""
print(h,p) if h and p else sys.exit(1)' 2>/dev/null
}

echo "===== waiting for instance $ID to be SSH-ready (max ${MAXW}s) =====" >&2
t0=$(date +%s); HOST=""; PORT=""
while :; do
  now=$(date +%s); el=$(( now - t0 ))
  [ "$el" -ge "$MAXW" ] && { echo "TIMEOUT after ${el}s — still not SSH-ready" >&2; exit 1; }

  if [ -z "$HOST" ]; then
    read -r HOST PORT < <(resolve) || true
    [ -z "${HOST:-}" ] && { sleep 8; continue; }
    echo "  resolved $HOST:$PORT (after ${el}s)" >&2
  fi

  # one probe; classify the failure so we know it's worth retrying
  if err="$(ssh "${SSHOPTS[@]}" -p "$PORT" "root@$HOST" 'echo ok' 2>&1)"; then
    if [ "$err" = "ok" ]; then
      echo "===== SSH READY: $HOST:$PORT (after ${el}s) =====" >&2
      break
    fi
  fi
  case "$err" in
    *"Permission denied"*)       state="key-not-written-yet" ;;
    *"Connection closed"*)       state="proxy-not-routing-yet" ;;
    *"Connection refused"*)      state="sshd-not-up-yet" ;;
    *"Connection timed out"*|*"timed out"*) state="host-unreachable-yet" ;;
    *"Could not resolve"*)       state="dns-not-ready"; HOST="" ;;  # re-resolve
    *)                           state="transient" ;;
  esac
  echo "  [${el}s] not ready ($state) — retrying" >&2
  sleep 8
done

if [ -n "$EXEC" ]; then
  exec ssh "${SSHOPTS[@]}" -p "$PORT" "root@$HOST" "$EXEC"
fi
echo "$HOST $PORT"
