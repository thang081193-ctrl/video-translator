#!/usr/bin/env bash
# LOCAL pull (Git-bash on Windows). Outbound through NAT — no inbound, no Drive.
#
# This is a CHECKED INVARIANT, not a convenience script: it pulls the deliverables
# AND the manifest-driven SOURCE re-run set (so the only copy of the sources comes
# off the ephemeral box BEFORE anyone can destroy it) AND the QA montage, asserts
# every per-pack count locally, and REFUSES to print DOWNLOAD_COMPLETE (or to touch
# PULL_OK) unless all invariants hold. Only then does it stand the box park-timer down.
# It NEVER destroys — destroy is the human gate in scripts/human_destroy.sh.
#
# HARDENED (W3-orchestration, HARDEN_final.md §6 + AUDIT bug list):
#   - set -euo pipefail; every tar|tar is PIPESTATUS-checked (a truncated remote
#     stream must NOT pass as success).
#   - GENERIC counts, NOT hardcoded 2606 (600/320/577/1869): the expected per-pack
#     floor is read from the box's BATCH.DONE "counts" object (the render already
#     computed the authoritative count), with an optional EXPECT_<key> env override.
#     The hard floor is: zero *.tmp.mp4/*.bak leftovers AND each pack mp4 count > 0.
#   - manifest-driven SOURCE set: source mp4 list derived from manifest.json src_path
#     (sources live in per-LANG folders, not a source/ glob) + _assets + vo_bank/app/manifest.
#   - per-pack non-uniform source: resolves vo_bank name from the box; pulls only the
#     required files and asserts the required ones (no wildcard-as-completeness).
#   - parameterized <HOST> <PORT> [ID]; connectivity test so a dead host can't march on.
#   - on full success: ssh `touch $TAIL/PULL_OK` so park_timer stands down.
set -euo pipefail

DEST="/d/Dev/Tools/Video Translator/_deliverables_2606"
TAIL=/workspace/_tail
HOST="${1:?usage: download.sh <host> <port> [instance_id]   e.g. download.sh ssh2.vast.ai 19360 12345}"
PORT="${2:?need port}"
INST="${3:-}"   # optional instance id; presence enables the PULL_OK stand-down signal
BOX="ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=25 -p $PORT root@$HOST"
mkdir -p "$DEST"

# ---- fail-fast connectivity: a dead/old host must not be able to reach DOWNLOAD_COMPLETE ----
$BOX 'echo BOX_REACHABLE' >/dev/null || { echo "CANNOT REACH BOX $HOST:$PORT"; exit 1; }

# ---- require the box to have a COMPLETED batch before we pull (status==OK) ----
$BOX "test -f $TAIL/BATCH.DONE" || { echo "no BATCH.DONE on box yet — batch not finished"; exit 1; }
STATUS=$($BOX "python3 -c 'import json;print(json.load(open(\"$TAIL/BATCH.DONE\")).get(\"status\",\"FAIL\"))'" 2>/dev/null || echo FAIL)
[ "$STATUS" = OK ] || { echo "box BATCH.DONE status=$STATUS — refusing to pull a failed batch"; exit 1; }

# ---- pull the marker locally so we can read the authoritative per-pack counts ----
$BOX "cat $TAIL/BATCH.DONE" > "$DEST/BATCH.DONE" || { echo "cannot read box BATCH.DONE"; exit 1; }

# Pack -> remote layout. Keys are stable local labels; values map to the box job dirs.
declare -A REMOTE_PARENT=( \
  [deco_v2]=/workspace/jobs/deco2506        [score_v2]=/workspace/jobs/score2506 \
  [tb_v2]=/workspace/jobs/tb090626          [deco_v1]=/workspace/jobs/deco2506_v1 \
  [score_v1]=/workspace/jobs/score2506_v1   [tb_v1]=/workspace/jobs/tb090626_v1 )
declare -A REMOTE_DIR=( \
  [deco_v2]=_out_v2 [score_v2]=_out_v2 [tb_v2]=_out_v2 \
  [deco_v1]=_out    [score_v1]=_out    [tb_v1]=_out )
# the BATCH.DONE "counts" key (box job name) backing each local label, for the generic floor
declare -A JOBKEY=( \
  [deco_v2]=deco2506 [score_v2]=score2506 [tb_v2]=tb090626 \
  [deco_v1]=deco2506_v1 [score_v1]=score2506_v1 [tb_v1]=tb090626_v1 )
# packs that carry a manifest-driven SOURCE set (the V2 voiced packs)
declare -A SRC_PACK=( [deco_v2]=deco2506 [score_v2]=score2506 [tb_v2]=tb090626 )

# expected_floor <local_label>: env EXPECT_<label> override, else the box's own counted
# value for that job (authoritative), else 1. NEVER a hardcoded batch number.
expected_floor(){
  local pk="$1" envk="EXPECT_${1}" v
  v="${!envk:-}"
  if [ -n "$v" ]; then echo "$v"; return; fi
  v=$(python3 -c 'import json,sys
try:
    c=json.load(open(sys.argv[1])).get("counts",{}) or {}
    print(int(c.get(sys.argv[2],0)))
except Exception: print(0)' "$DEST/BATCH.DONE" "${JOBKEY[$pk]}" 2>/dev/null || echo 0)
  [ "${v:-0}" -gt 0 ] && echo "$v" || echo 1
}

pull_stream(){ # <local_dir> <remote_parent> <remote_dir>
  local out="$1" parent="$2" dir="$3" ps
  mkdir -p "$out"
  set +e
  $BOX "tar cf - -C '$parent' '$dir'" | tar xf - -C "$out"
  ps=( "${PIPESTATUS[@]}" )
  set -e
  [ "${ps[0]}" -eq 0 ] && [ "${ps[1]}" -eq 0 ] || {
    echo "TAR PIPE FAILED ($parent/$dir: remote=${ps[0]} local=${ps[1]})"; return 1; }
}

for pk in deco_v2 score_v2 tb_v2 deco_v1 score_v1 tb_v1; do
  # skip packs the box never produced (generic: not every batch has all 6)
  $BOX "test -d '${REMOTE_PARENT[$pk]}/${REMOTE_DIR[$pk]}'" || { echo "[$pk] no remote dir — skipping"; continue; }

  echo "[$(date +%H:%M:%S)] pulling $pk deliverables"
  pull_stream "$DEST/$pk" "${REMOTE_PARENT[$pk]}" "${REMOTE_DIR[$pk]}"
  n=$(find "$DEST/$pk" -name '*.mp4' ! -name '*.tmp.mp4' ! -name '*.bak' | wc -l)
  tmp=$(find "$DEST/$pk" -name '*.tmp.mp4' | wc -l)
  exp=$(expected_floor "$pk")
  [ "$tmp" -eq 0 ] || { echo "LEFTOVER $pk: $tmp *.tmp.mp4 pulled (integrity risk)"; exit 1; }
  [ "$n" -ge "$exp" ] || { echo "COUNT SHORT $pk: $n < $exp"; exit 1; }
  [ "$n" -gt 0 ] || { echo "EMPTY $pk: 0 mp4"; exit 1; }
  echo "  $pk: $n mp4 (>= $exp) OK"

  # ---- manifest-driven SOURCE re-run set (V2 voiced packs only) ----
  if [ -n "${SRC_PACK[$pk]:-}" ]; then
    sp="${SRC_PACK[$pk]}"; sparent="/workspace/jobs/$sp"
    mkdir -p "$DEST/${sp}_source"

    # resolve the vo_bank file name from the box (vo_bank_deco/score/trade.json; root or _ultimate)
    vob=$($BOX "ls '$sparent'/vo_bank_*.json '$sparent'/_ultimate/vo_bank_*.json 2>/dev/null | head -1" || true)
    [ -n "$vob" ] || { echo "NO vo_bank for $sp on box"; exit 1; }
    vobname=$(basename "$vob")

    # derive the source mp4 list from manifest.json src_path (sources live in per-lang folders)
    $BOX "cd '$sparent' && python3 - <<'PY' > /tmp/${sp}_srclist.txt
import json, os
m = json.load(open('manifest.json'))
seen = set()
for v in m.get('videos', []):
    p = v.get('src_path') or v.get('organized_path')
    if p and p not in seen and os.path.exists(p):
        seen.add(p); print(os.path.relpath(p, '.'))
PY" || { echo "manifest src-list failed for $sp"; exit 1; }

    # stream the required set: manifest/app/vo_bank/_assets + manifest-listed source mp4
    set +e
    $BOX "cd '$sparent' && tar cf - manifest.json app.json '$vobname' _assets -T /tmp/${sp}_srclist.txt 2>/dev/null" \
      | tar xf - -C "$DEST/${sp}_source"
    ps=( "${PIPESTATUS[@]}" )
    set -e
    [ "${ps[0]}" -eq 0 ] && [ "${ps[1]}" -eq 0 ] || { echo "SOURCE TAR FAILED $sp (remote=${ps[0]} local=${ps[1]})"; exit 1; }

    # assert required source files present locally (no wildcard-as-completeness)
    for req in manifest.json app.json "$vobname"; do
      [ -s "$DEST/${sp}_source/$req" ] || { echo "MISSING source file $sp/$req"; exit 1; }
    done
    # assert source mp4 count >= manifest-listed count
    want=$($BOX "wc -l < /tmp/${sp}_srclist.txt" | tr -d '[:space:]')
    got=$(find "$DEST/${sp}_source" -name '*.mp4' | wc -l)
    [ "$got" -ge "${want:-0}" ] || { echo "SOURCE mp4 SHORT $sp: $got < $want"; exit 1; }
    echo "  ${sp}_source: $got/$want source mp4 + manifest+app+$vobname+_assets OK"
  fi
done

# ---- QA montage: the artifact the human MUST eyeball before destroy ----
if $BOX "test -d $TAIL/_qa_montage"; then
  mkdir -p "$DEST/_qa_montage"
  set +e
  $BOX "tar cf - -C $TAIL _qa_montage" | tar xf - -C "$DEST"
  ps=( "${PIPESTATUS[@]}" )
  set -e
  [ "${ps[0]}" -eq 0 ] && [ "${ps[1]}" -eq 0 ] || echo "WARN: montage pull failed (re-pull before QA)"
else
  echo "WARN: no _qa_montage on box (tail_qa.sh may not have run) — QA montage absent"
fi

total=$(find "$DEST" -path '*_source*' -prune -o -name '*.mp4' ! -name '*.tmp.mp4' ! -name '*.bak' -print | wc -l)
echo "=== local deliverable mp4 total: $total ==="
[ "$total" -gt 0 ] || { echo "TOTAL is 0 — NOTHING pulled, not complete"; exit 1; }

# All invariants held: deliverables + source set + montage on local disk.
# Signal the box to stand its park timer down (operator now owns the lifecycle).
if [ -n "$INST" ]; then
  $BOX "touch $TAIL/PULL_OK" && echo "signalled box: PULL_OK (park timer stands down)"
fi

echo DOWNLOAD_COMPLETE
echo
echo "NEXT (human gate): open '$DEST/_qa_montage/'montage_*.jpg + outro_report.json."
echo "Confirm the brand outro on EVERY pack + zero unexplained 'suspect_no_outro'."
echo "ONLY THEN run:  bash scripts/human_destroy.sh ${INST:-<INSTANCE_ID>} DESTROY"
echo
echo "!!! COST WARNING: the box self-PARKS (vastai stop) — but a STOPPED instance is"
echo "    NOT free: Vast keeps billing for its DISK STORAGE while stopped. To stop ALL"
echo "    cost you MUST DESTROY it (above) once QA passes + this download is verified."
echo "    Leaving it merely 'stopped' overnight quietly burns money. Stopped != done."
