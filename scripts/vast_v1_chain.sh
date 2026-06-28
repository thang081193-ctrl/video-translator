#!/usr/bin/env bash
# RECONSTRUCTED 2026-06-27 from session capture; retrim-after-outro removed.
#
# V1 (BGM-only) per-pack chain: brandpass -> package -> qa_voice_mix.
# The post-package `retrim_endcards.py` step from the original is DELETED: brandpass's
# --trim-endcard already strips the source competitor card and package appends the BRAND
# outro; a retrim after the outro append ate the brand outro (the 2026-06-27 root cause).
# brandpass owns trimming; package owns the outro. DO NOT re-add a retrim here.
#
# Args: JOB WM OUTRO
#   JOB   = /workspace/jobs/<pack>
#   WM    = watermark/logo png
#   OUTRO = outro video mp4
# Worker count is env-overridable: V1_BP_WORKERS (default 12).
set -uo pipefail

JOB="${1:?usage: vast_v1_chain.sh JOB WM OUTRO}"
WM="${2:?need WM}"
OUTRO="${3:?need OUTRO}"

ROOT=/workspace/video-translator
RUN="$ROOT/_claude_skills/meta-ads-prepare-ultimate/run.py"
QA="$ROOT/_claude_skills/meta-ads-prepare/qa_voice_mix.py"
BGM="$ROOT/_bgm"
DST="$JOB/_out"
mkdir -p "$DST"

TAIL=/workspace/_tail
mkdir -p "$TAIL"
hb(){ date +%s > "$TAIL/heartbeat"; }
say(){ echo "===== [$(date +%F_%H:%M:%S)] $* ====="; hb; }

BP_WORKERS="${V1_BP_WORKERS:-12}"

export VIDEO_TRANSLATOR_ROOT="$ROOT" PYTHONIOENCODING=utf-8
. "$ROOT/scripts/box_python.sh" 2>/dev/null || true   # make python3 the torch venv (non-login ssh)
cd "$ROOT" || exit 9

rc=0

say "V1 brandpass ($JOB  workers=$BP_WORKERS)"
# --bgm-pool is OPTIONAL: pass it only when the dir has tracks, else brandpass
# hard-exits ("--bgm-pool not a dir") and fails the whole batch.
BGM_ARG=()
if [ -d "$BGM" ] && find "$BGM" -maxdepth 2 -type f \( -name '*.mp3' -o -name '*.m4a' -o -name '*.wav' -o -name '*.aac' -o -name '*.ogg' \) 2>/dev/null | grep -q .; then
  BGM_ARG=(--bgm-pool "$BGM")
else
  say "WARN: no BGM pool at $BGM -> keeping SOURCE audio (Meta copyright risk; deploy a pool for real packs)"
fi
python3 -u "$RUN" brandpass --src "$JOB" --dst "$DST" \
  --watermark "$WM" --outro-video "$OUTRO" "${BGM_ARG[@]}" --trim-endcard \
  --workers "$BP_WORKERS" || rc=$?
hb

say "V1 package ($JOB)"
python3 -u "$RUN" package --src "$JOB" --dst "$DST" "${BGM_ARG[@]}" || rc=$?
hb

# ROOT-CAUSE FIX: retrim_endcards.py REMOVED here (was between package and qa in the original).
# --trim-endcard above already stripped the competitor card; package appended the brand outro;
# a post-append retrim would eat the brand outro. brandpass owns trimming; package owns the outro.

say "V1 qa ($JOB)"
python3 -u "$QA" "$DST" || rc=$?
hb

say "V1 chain done ($JOB) rc=$rc"
exit "$rc"
