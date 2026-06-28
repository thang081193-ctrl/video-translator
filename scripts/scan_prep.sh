#!/usr/bin/env bash
# PHASE 0 (autonomous, NO human) — scan every uploaded pack so transcripts are READY
# before the operator's single Opus manifest session. This is the "front-load the only
# Opus-dependent input" step: run it right after upload + walk away; when SCAN.DONE
# exists, turning on the PC + opening Claude lets Opus fill the manifest IMMEDIATELY
# (no waiting on Whisper). Pure compute; never needs a human; never stops/destroys.
#
# Usage: bash scripts/scan_prep.sh [pack1 pack2 ...]   (default: every /workspace/jobs/* with source)
set -uo pipefail
ROOT=/workspace/video-translator
RUN="$ROOT/_claude_skills/meta-ads-prepare-ultimate/run.py"
TAIL=/workspace/_tail; mkdir -p "$TAIL"
export VIDEO_TRANSLATOR_ROOT="$ROOT" PYTHONIOENCODING=utf-8
. "$ROOT/scripts/box_python.sh" 2>/dev/null || true   # make python3 the torch venv (non-login ssh)
cd "$ROOT" || exit 9
say(){ echo "===== [$(date +%F_%H:%M:%S)] $* ====="; date +%s > "$TAIL/heartbeat"; }

# Deps gate FIRST so scan never stops mid-way to install/download a model.
say "preflight --strict"
python3 pipeline/preflight.py --strict || { echo "PREFLIGHT FAIL — fix env before scan"; exit 1; }

# packs = args, else every job dir that has at least one source mp4
PACKS=("$@")
if [ ${#PACKS[@]} -eq 0 ]; then
  mapfile -t PACKS < <(for d in /workspace/jobs/*/; do
      [ -n "$(find "$d" -maxdepth 3 -name '*.mp4' ! -path '*/_out*' 2>/dev/null | head -1)" ] && basename "$d"
    done)
fi
[ ${#PACKS[@]} -eq 0 ] && { echo "no packs with source mp4 under /workspace/jobs"; exit 1; }

FAIL=0; summ="{"
for J in "${PACKS[@]}"; do
  D=/workspace/jobs/$J; [ -d "$D" ] || { echo "skip missing $J"; continue; }
  mkdir -p "$D/_ultimate"
  say "scan $J"
  if ! python3 -u "$RUN" scan --src "$D" > "$D/_ultimate/scan.log" 2>&1; then
    echo "SCAN FAIL $J (see $D/_ultimate/scan.log)" >&2; FAIL=1
  fi
  n=$(python3 -c "import json,os;p='$D/_ultimate/manifest.json';print(len(json.load(open(p)).get('videos',[])) if os.path.exists(p) else 0)" 2>/dev/null || echo 0)
  echo "  $J: $n videos transcribed"
  summ="$summ\"$J\":$n,"
done
summ="${summ%,}}"

printf '{"status":"%s","scanned":%s,"at":%s}\n' "$([ $FAIL -eq 0 ] && echo OK || echo FAIL)" "$summ" "$(date +%s)" > "$TAIL/SCAN.DONE.tmp"
mv -f "$TAIL/SCAN.DONE.tmp" "$TAIL/SCAN.DONE"
say "SCAN PREP DONE (status=$([ $FAIL -eq 0 ] && echo OK || echo FAIL)) — manifests ready for the Opus fill"

# optional push so the operator knows "transcripts ready -> open Claude" without watching
[ -n "${NTFY_TOPIC:-}" ] && curl -s -d "SCAN ready: open Claude, fill manifest, launch full_batch" "https://ntfy.sh/${NTFY_TOPIC}" >/dev/null 2>&1 || true
exit 0
