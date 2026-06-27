#!/usr/bin/env bash
# scripts/vast_rent.sh — RENT a Vast.ai box with SSH ALREADY working.
# ---------------------------------------------------------------------------
# ROOT-CAUSE FIX for the recurring "Permission denied (publickey)" /
# "Connection closed by proxy" friction:
#
#   Account-level SSH keys are BLOCKED on this account ("Team SSH keys are not
#   supported. SSH keys can only be created in personal context."), and the
#   per-instance `vastai attach ssh` does NOT reliably reach the running
#   container's authorized_keys (race + proxy lag). So instead we BAKE the
#   public key into /root/.ssh/authorized_keys via --onstart-cmd at creation.
#   The key is present the instant sshd starts — no UI dialog, no attach, no
#   propagation wait, works regardless of team/personal context.
#
# It also persists the Vast-injected CONTAINER_ID / CONTAINER_API_KEY into
# /etc/environment so the self-PARK timer (scripts/park_timer.sh) can stop the
# box later. NO secrets (no GitHub token, no .env) are placed in the onstart —
# the onstart is visible in the Vast UI/logs, so only the PUBLIC key goes there.
#
# Usage:  scripts/vast_rent.sh <offer_id> [disk_gb] [label] [image]
#   offer_id : numeric ID from `vastai search offers ...`
#   disk_gb  : default 60
#   label    : default vast-tail-<offer>
#   image    : default vastai/pytorch:cuda-13.1.2-auto  (cu130, torch preinstalled)
#
# Prints the new INSTANCE id on stdout (last line) so callers can capture it:
#   ID=$(scripts/vast_rent.sh 1234567 | tail -1)
# ---------------------------------------------------------------------------
set -uo pipefail

OFFER="${1:?offer_id required (from: vastai search offers ...)}"
DISK="${2:-60}"
LABEL="${3:-vast-tail-$OFFER}"
IMAGE="${4:-vastai/pytorch:cuda-13.1.2-auto}"
PUBKEY_FILE="${VAST_PUBKEY_FILE:-$HOME/.ssh/id_ed25519.pub}"

[ -f "$PUBKEY_FILE" ] || { echo "FATAL: pubkey not found: $PUBKEY_FILE (set VAST_PUBKEY_FILE)"; exit 2; }
PUBKEY="$(tr -d '\r\n' < "$PUBKEY_FILE")"
case "$PUBKEY" in
  ssh-ed25519\ *|ssh-rsa\ *|ecdsa-*) : ;;
  *) echo "FATAL: $PUBKEY_FILE does not look like an SSH public key"; exit 2 ;;
esac

# --- onstart: inject pubkey + persist Vast env. Public key only; no secrets. ---
# Single-quoted heredoc => the box evaluates $CONTAINER_* at boot, not us now.
read -r -d '' ONSTART <<ONSTART_EOF || true
mkdir -p /root/.ssh && chmod 700 /root/.ssh
grep -qxF '$PUBKEY' /root/.ssh/authorized_keys 2>/dev/null || echo '$PUBKEY' >> /root/.ssh/authorized_keys
chmod 600 /root/.ssh/authorized_keys
# persist Vast lifecycle creds for the self-PARK timer (NOT secrets we own)
env | grep -E '^(CONTAINER_ID|CONTAINER_API_KEY)=' >> /etc/environment 2>/dev/null || true
mkdir -p /workspace && date +%s > /workspace/.ssh_ready
ONSTART_EOF

echo "===== renting offer $OFFER (disk ${DISK}G, image $IMAGE) =====" >&2
echo "      pubkey: ${PUBKEY##* } baked into authorized_keys via onstart"   >&2

OUT="$(vastai create instance "$OFFER" \
  --image "$IMAGE" \
  --disk "$DISK" \
  --ssh --direct \
  --label "$LABEL" \
  --onstart-cmd "$ONSTART" \
  --raw 2>&1)"
RC=$?
echo "$OUT" >&2
[ $RC -eq 0 ] || { echo "FATAL: vastai create failed (rc=$RC)"; exit $RC; }

# `--raw` returns JSON like {"success": true, "new_contract": 1234567}
NEWID="$(printf '%s' "$OUT" | python3 -c 'import sys,json,re
raw=sys.stdin.read()
m=re.search(r"\{.*\}",raw,re.S)
if not m: sys.exit(1)
d=json.loads(m.group(0))
print(d.get("new_contract") or d.get("id") or "")' 2>/dev/null)"

[ -n "$NEWID" ] || { echo "FATAL: could not parse new instance id from create output"; exit 1; }
echo "===== instance $NEWID created — SSH key is pre-baked; use scripts/vast_connect.sh $NEWID =====" >&2
echo "$NEWID"
