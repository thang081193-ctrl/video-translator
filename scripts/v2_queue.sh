#!/usr/bin/env bash
# Follow-on V2 voiced queue. Waits for the master batch's POSITIVE completion marker
# ($TAIL/BATCH.DONE with status==OK) — NEVER a hardcoded PID (was `ps -p 45801`, which
# breaks on PID reuse / relaunch / reboot). Refuses to start V2 chains if the master FAILED.
#
# HARDENED (W3-orchestration, HARDEN_final.md §5 + AUDIT bug list):
#   - marker wait, not PID poll: survives relaunch + the box self-rebooting.
#   - status gate: only runs if BATCH.DONE.status == OK (no false-DONE on master failure).
#   - reads status via python3 json (never greps a log line).
#   - NEVER destroys (final destroy is the human gate in scripts/human_destroy.sh).
set -uo pipefail

ROOT=/workspace/video-translator
CHAIN="$ROOT/scripts/vast_v2_chain.sh"
TAIL=/workspace/_tail
say(){ echo "===== [$(date +%F_%H:%M:%S)] $* ====="; }

say "WAIT for master batch marker $TAIL/BATCH.DONE"
WAITED=0; MAXWAIT=${MAXWAIT:-50400}   # 14h
until [ -f "$TAIL/BATCH.DONE" ]; do
  sleep 60; WAITED=$((WAITED+60))
  if [ "$WAITED" -ge "$MAXWAIT" ]; then
    say "no BATCH.DONE after ${MAXWAIT}s -> aborting V2 queue"; exit 2
  fi
done

STATUS=$(python3 -c 'import json,sys
try: print(json.load(open("/workspace/_tail/BATCH.DONE")).get("status","FAIL"))
except Exception: print("FAIL")' 2>/dev/null || echo FAIL)
say "master batch BATCH.DONE status=$STATUS"
if [ "$STATUS" != OK ]; then
  say "master batch status=$STATUS -> NOT starting V2 queue"; exit 1
fi

say "master batch OK -> starting V2 voiced chains"

# 1. DecoAI VOICED V2 (id,fr,en)
say "DECO V2 chain start"
bash "$CHAIN" /workspace/jobs/deco2506 id,fr,en \
  /workspace/jobs/deco2506/_ultimate/vo_bank_deco.json \
  /workspace/jobs/deco2506/_ultimate/app.json \
  /workspace/jobs/deco2506/_assets/logo.png \
  /workspace/jobs/deco2506/_assets/outro.mp4
say "DECO V2 chain returned"

# 2. ScoreDeck VOICED V2 (en,id,th,vi)
say "SCORE V2 chain start"
bash "$CHAIN" /workspace/jobs/score2506 en,id,th,vi \
  /workspace/jobs/score2506/_ultimate/vo_bank_score.json \
  /workspace/jobs/score2506/_ultimate/app.json \
  /workspace/jobs/score2506/_assets/logo.png \
  /workspace/jobs/score2506/_assets/outro.mp4
say "SCORE V2 chain returned"

say "V2 QUEUE COMPLETE. Ready for local pull + human visual-QA + destroy gate."
