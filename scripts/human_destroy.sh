#!/usr/bin/env bash
# human_destroy.sh — the ONE and ONLY path that ever runs `vastai destroy`.
#
# SAFETY INVARIANT: the literal string "vastai destroy" may appear in EXACTLY this file and
# nowhere else in the tail. Destroy is irreversible, so it is gated on ALL of:
#   1. a HUMAN-typed instance id (arg 1) AND the literal word DESTROY (arg 2) — no script can
#      synthesize that token;
#   2. DOWNLOAD proof — the local deliverables tree + outro_report.json exist on local disk
#      (i.e. download.sh already pulled deliverables + source + montage and printed
#      DOWNLOAD_COMPLETE);
#   3. a clean outro_report — zero "suspect_no_outro", OR an explicit hand-typed acknowledgment
#      that the operator visually confirmed the montage anyway.
#
# Run this ONLY after eyeballing _qa_montage/montage_*.jpg + outro_report.json.
#
# Usage: human_destroy.sh <INSTANCE_ID> DESTROY
#   DEST (deliverables root) overridable via $DEST for non-default batches.
set -euo pipefail

ID="${1:?usage: human_destroy.sh <INSTANCE_ID> DESTROY}"
TOKEN="${2:-}"
if [ "$TOKEN" != "DESTROY" ]; then
  echo "refusing: you must type DESTROY as arg 2 after viewing the montage."
  echo "usage: human_destroy.sh <INSTANCE_ID> DESTROY"
  exit 1
fi

DEST="${DEST:-/d/Dev/Tools/Video Translator/_deliverables_2606}"
REPORT="$DEST/_qa_montage/outro_report.json"

# --- DOWNLOAD proof: deliverables tree + report must be on local disk (download.sh ran) ---
if [ ! -d "$DEST" ]; then
  echo "refusing: deliverables dir '$DEST' missing — run download.sh (pull + DOWNLOAD_COMPLETE) first."
  exit 1
fi
if [ ! -f "$REPORT" ]; then
  echo "refusing: '$REPORT' missing — pull deliverables + montage and QA first."
  exit 1
fi
# at least one delivered mp4 must be on local disk (proves the evac actually landed)
n_mp4=$(find "$DEST" -name '*.mp4' ! -name '*.tmp.mp4' ! -name '*.bak' 2>/dev/null | wc -l)
if [ "$n_mp4" -lt 1 ]; then
  echo "refusing: no delivered mp4 found under '$DEST' — DOWNLOAD proof failed."
  exit 1
fi
echo "DOWNLOAD proof OK: $n_mp4 mp4 on local disk under $DEST"

# --- clean-report gate: refuse (unless acknowledged) if any file lacks a brand-outro card ---
susp=$(python3 -c 'import json,sys
try:
    d=json.load(open(sys.argv[1]))
    print(sum(p.get("suspect_count",0) for p in d.values() if isinstance(p,dict)))
except Exception:
    print(-1)' "$REPORT")

if [ "$susp" = "-1" ]; then
  echo "refusing: could not parse $REPORT — re-run outro_detect / re-pull montage."
  exit 1
fi

if [ "$susp" -ne 0 ]; then
  echo "WARNING: outro_report flags $susp file(s) with no card-tail (possible stripped/ missing outro)."
  echo "Open $DEST/_qa_montage/montage_*.jpg and confirm the brand outro by eye BEFORE proceeding."
  read -r -p "Have you VISUALLY confirmed the montage anyway? type YES-SAW-MONTAGE: " ack
  if [ "$ack" != "YES-SAW-MONTAGE" ]; then
    echo "aborted — not destroying."
    exit 1
  fi
fi

echo "All gates passed. Destroying instance $ID (IRREVERSIBLE) ..."
vastai destroy instance "$ID"
vastai show instances || true
echo "destroyed. source + deliverables remain on local disk at: $DEST"
