#!/usr/bin/env bash
# tail_qa.sh — box-side QA artifact builder. Launched alongside the render.
#
# Waits on the ATOMIC $TAIL/BATCH.DONE marker (parses status via python3 json — NEVER greps
# a log line), then calls outro_detect.py to build the human-eyeball end-frame montage + a
# per-file brand-outro tally across every pack x lang-folder x V1/V2.
#
# It does NOT stop, upload, notify, or tear the instance down — the local PC owns delivery and
# the human owns teardown. The destroy verb must NOT appear here (safety invariant).
#
# Packs are GENERIC: derived from the marker's "counts" object keys (the packs the batch
# actually produced), overridable via $PACKS. No batch numbers are hardcoded.
set -uo pipefail

ROOT="${ROOT:-/workspace/video-translator}"
TAIL="${TAIL:-/workspace/_tail}"
JOBS_ROOT="${JOBS_ROOT:-/workspace/jobs}"
QADIR="$TAIL/_qa_montage"
MARKER="$TAIL/BATCH.DONE"
mkdir -p "$QADIR"

stamp(){ date +%F_%H:%M:%S; }
say(){ echo "===== [$(stamp)] tail_qa: $* ====="; }

# Drain by MARKER, not process-name. full_batch.sh writes BATCH.DONE atomically AFTER all
# ThreadPools join, so no live render ffmpeg can exist once it appears. Bounded wait.
WAITED=0; MAXWAIT="${MAXWAIT:-50400}"   # 14h
until [ -f "$MARKER" ]; do
  sleep 60; WAITED=$((WAITED + 60))
  if [ "$WAITED" -ge "$MAXWAIT" ]; then
    say "NO BATCH.DONE after ${MAXWAIT}s -> INCOMPLETE (no montage built)"
    exit 2
  fi
done

# Parse status from the atomic JSON marker (never grep a log line).
STATUS=$(python3 -c 'import json,sys
try:
    print(json.load(open(sys.argv[1])).get("status","?"))
except Exception:
    print("PARSE_FAIL")' "$MARKER")
say "BATCH.DONE present, status=$STATUS"

# Derive packs generically from the marker counts; allow $PACKS override.
if [ -n "${PACKS:-}" ]; then
  PACKS_CSV="$PACKS"
else
  PACKS_CSV=$(python3 -c 'import json,sys
try:
    d=json.load(open(sys.argv[1]))
    print(",".join(sorted((d.get("counts") or {}).keys())))
except Exception:
    print("")' "$MARKER")
fi

# Fallback: if the marker carried no counts, discover packs by scanning the jobs root.
if [ -z "$PACKS_CSV" ]; then
  say "marker had no counts -> discovering packs under $JOBS_ROOT"
  PACKS_CSV=$(python3 -c 'import os,sys
root=sys.argv[1]
packs=[]
try:
    for d in sorted(os.listdir(root)):
        p=os.path.join(root,d)
        if os.path.isdir(p) and (os.path.isdir(os.path.join(p,"_out")) or os.path.isdir(os.path.join(p,"_out_v2"))):
            packs.append(d)
except Exception:
    pass
print(",".join(packs))' "$JOBS_ROOT")
fi

if [ -z "$PACKS_CSV" ]; then
  say "no packs to QA (empty counts and none discovered) -> nothing to do"
  exit 0
fi
say "QA packs: $PACKS_CSV"

# Build montage + outro tally (READ-ONLY detector; never modifies mp4, never stops the box).
python3 "$ROOT/scripts/outro_detect.py" \
  --jobs-root "$JOBS_ROOT" --out "$QADIR" --packs "$PACKS_CSV" \
  > "$TAIL/outro_detect.log" 2>&1
rc=$?
if [ "$rc" -ne 0 ]; then
  say "outro_detect.py rc=$rc (see $TAIL/outro_detect.log) — montage may be partial"
fi

say "QA montage + outro tally written to $QADIR (batch status was $STATUS)"
exit 0
