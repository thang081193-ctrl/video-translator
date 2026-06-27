#!/usr/bin/env bash
# park_timer.sh — the ONE autonomous lifecycle act in the whole tail.
#
# Liveness-aware MAXPARK/MAXIDLE cost-cap. Stops SELF via `vastai stop` (reversible,
# billing-cap) — it NEVER tears the instance down. The destroy verb must NOT appear in this
# file (safety invariant: that verb lives ONLY in scripts/human_destroy.sh).
#
# Fires a stop when EITHER:
#   * wall-clock since start  >= MAXPARK, OR
#   * heartbeat stale > MAXIDLE  AND  mp4 count unchanged for > MAXIDLE.
# Then rc-checks the stop, verify-polls `vastai show instance --raw` until actual_status
# is stopped/exited/offline, and ALARMS (never fire-and-forget) if it cannot confirm.
#
# Idempotent via the ledger (key 'stopped':true). Stands down on $TAIL/PULL_OK.
# Fail-closed: missing CID/KEY -> refuse to ever stop and exit 3.
#
# Usage: park_timer.sh <CID> <KEY> <MAXPARK> <MAXIDLE>
#   CID must already be the BARE instance id (caller strips the "C." label prefix).
set -uo pipefail

CID="${1:-}"; KEY="${2:-}"; MAXPARK="${3:-43200}"; MAXIDLE="${4:-2700}"
TAIL="${TAIL:-/workspace/_tail}"; mkdir -p "$TAIL"
LEDGER="$TAIL/state.json"
HERE="$(cd "$(dirname "$0")" && pwd)"
START=$(date +%s)

stamp(){ date +%F_%H:%M:%S; }
log(){ echo "[$(stamp)] park_timer: $*"; }

# fail-closed: refuse to arm without creds (rc=3 is a contract return code the selftest checks)
if [ -z "$CID" ] || [ -z "$KEY" ]; then
  log "missing CID/KEY -> NOT arming (fail-closed)"
  exit 3
fi

mp_count(){ find /workspace/jobs -name '*.mp4' ! -name '*.tmp.mp4' ! -name '*.bak' 2>/dev/null | wc -l; }

already_stopped(){
  # idempotency via ledger: 'stopped':true means a prior run already stopped us
  [ "$(python3 "$HERE/ledger.py" get stopped 2>/dev/null)" = "True" ]
}

actual_status(){
  # print the instance's actual_status, or empty on any error (parse-safe one-statement python)
  vastai show instance "$CID" --raw --api-key "$KEY" 2>/dev/null \
    | python3 -c 'import sys,json
try:
    d=json.load(sys.stdin); print(d.get("actual_status","") if isinstance(d,dict) else "")
except Exception:
    print("")' 2>/dev/null
}

verify_stopped(){  # poll up to ~75s; 0=confirmed stopped, 1=still running
  local i st
  for i in 1 2 3; do
    st="$(actual_status)"
    log "  verify[$i] actual_status=${st:-<none>}"
    case "$st" in stopped|exited|offline) return 0;; esac
    sleep 25
  done
  return 1
}

do_stop(){ # <reason>
  if already_stopped; then
    log "already stopped (ledger) -> no-op"
    exit 0
  fi
  log "STOP trigger: $1"
  local out rc
  out="$(vastai stop instance "$CID" --api-key "$KEY" 2>&1)"; rc=$?
  log "  'vastai stop instance' rc=$rc :: ${out}"
  if [ "$rc" -ne 0 ]; then
    log "  STOP CMD returned rc=$rc (will still verify before alarming)"
  fi
  if verify_stopped; then
    python3 "$HERE/ledger.py" set stopped true reason "$1" stopped_at "$(date +%s)" || true
    log "  STOP CONFIRMED (ledger updated)"
  else
    # fail-closed alarm: do NOT pretend success. Leave the box up; operator must intervene.
    python3 "$HERE/ledger.py" set stop_failed true reason "$1" stop_failed_at "$(date +%s)" || true
    log "  !!! SELF-STOP FAILED — instance $CID still running. Operator must stop it manually."
  fi
  exit 0
}

# If a previous run already parked us, exit immediately (relaunch-safe).
if already_stopped; then
  log "ledger says stopped:true at launch -> nothing to do"
  exit 0
fi

last_change=$(date +%s); last_n=$(mp_count)
log "armed: MAXPARK=${MAXPARK}s MAXIDLE=${MAXIDLE}s start_mp4=${last_n}"

while :; do
  now=$(date +%s)

  # operator/local-pull owns the box now -> stand down without stopping
  if [ -f "$TAIL/PULL_OK" ]; then
    log "PULL_OK present -> standing down timer (no stop)"
    exit 0
  fi

  # absolute wall-clock cap
  if [ $((now - START)) -ge "$MAXPARK" ]; then
    do_stop "MAXPARK wall-clock ${MAXPARK}s reached"
  fi

  # liveness: mp4-count delta is the trustworthy primary; heartbeat freshness is secondary
  n=$(mp_count)
  if [ "$n" -ne "$last_n" ]; then last_n=$n; last_change=$now; fi
  hb=$(cat "$TAIL/heartbeat" 2>/dev/null || echo 0)
  case "$hb" in ''|*[!0-9]*) hb=0;; esac
  idle_hb=$(( now - hb ))
  idle_mp=$(( now - last_change ))
  if [ "$idle_hb" -gt "$MAXIDLE" ] && [ "$idle_mp" -gt "$MAXIDLE" ]; then
    do_stop "idle: heartbeat stale ${idle_hb}s AND no new mp4 ${idle_mp}s (> ${MAXIDLE}s)"
  fi

  sleep 60
done
