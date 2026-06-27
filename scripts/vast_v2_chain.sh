#!/usr/bin/env bash
# RECONSTRUCTED 2026-06-27 from session capture; retrim-after-outro removed.
#
# V2 (voiced) per-pack chain: dub -> brandpass -> voiceover -> package -> qa_voice_mix.
# The post-package `retrim_endcards.py` step from the original is DELETED: brandpass's
# --trim-endcard already strips the source competitor card and package appends the BRAND
# outro; a retrim after the outro append ate the brand outro (the 2026-06-27 root cause).
# brandpass owns trimming; package owns the outro. DO NOT re-add a retrim here.
#
# Args: JOB LANGS VOBANK APPCFG WM OUTRO
#   JOB    = /workspace/jobs/<pack>
#   LANGS  = comma list, e.g. id,fr,en
#   VOBANK = path to vo_bank_*.json
#   APPCFG = path to app.json
#   WM     = watermark/logo png
#   OUTRO  = outro video mp4
# Worker counts are env-overridable: V2_DUB_WORKERS (default 6), V2_BP_WORKERS (default 12).
set -uo pipefail

JOB="${1:?usage: vast_v2_chain.sh JOB LANGS VOBANK APPCFG WM OUTRO}"
LANGS="${2:?need LANGS}"
VOBANK="${3:?need VOBANK}"
APPCFG="${4:?need APPCFG}"
WM="${5:?need WM}"
OUTRO="${6:?need OUTRO}"

ROOT=/workspace/video-translator
RUN="$ROOT/_claude_skills/meta-ads-prepare-ultimate/run.py"
QA="$ROOT/_claude_skills/meta-ads-prepare/qa_voice_mix.py"
BGM="$ROOT/_bgm"
DST="$JOB/_out_v2"
LOG="$JOB/_ultimate"
mkdir -p "$DST" "$LOG"

TAIL=/workspace/_tail
mkdir -p "$TAIL"
hb(){ date +%s > "$TAIL/heartbeat"; }
say(){ echo "===== [$(date +%F_%H:%M:%S)] $* ====="; hb; }

DUB_WORKERS="${V2_DUB_WORKERS:-6}"
BP_WORKERS="${V2_BP_WORKERS:-12}"

export VIDEO_TRANSLATOR_ROOT="$ROOT" PYTHONIOENCODING=utf-8
. "$ROOT/scripts/box_python.sh" 2>/dev/null || true   # make python3 the torch venv (non-login ssh)
cd "$ROOT" || exit 9

rc=0

say "V2 dub  ($JOB  langs=$LANGS  workers=$DUB_WORKERS)"
python3 -u "$RUN" dub --src "$JOB" --target-langs "$LANGS" --workers "$DUB_WORKERS" || rc=$?
hb

say "V2 brandpass ($JOB  workers=$BP_WORKERS)"
python3 -u "$RUN" brandpass --src "$JOB" --dst "$DST" --target-langs "$LANGS" \
  --watermark "$WM" --outro-video "$OUTRO" --bgm-pool "$BGM" --trim-endcard \
  --workers "$BP_WORKERS" || rc=$?
hb

say "V2 voiceover ($JOB)"
python3 -u "$RUN" voiceover --src "$JOB" --dst "$DST" --app-config "$APPCFG" \
  --vo-bank "$VOBANK" --target-langs "$LANGS" || rc=$?
hb

say "V2 package ($JOB)"
python3 -u "$RUN" package --src "$JOB" --dst "$DST" --target-langs "$LANGS" || rc=$?
hb

# ROOT-CAUSE FIX: retrim_endcards.py REMOVED here (was between package and qa in the original).
# --trim-endcard above already stripped the competitor card; package appended the brand outro;
# a post-append retrim would eat the brand outro. brandpass owns trimming; package owns the outro.

say "V2 qa ($JOB)"
python3 -u "$QA" "$DST" --whisper --expect-voice || rc=$?
hb

say "V2 chain done ($JOB) rc=$rc"
exit "$rc"
