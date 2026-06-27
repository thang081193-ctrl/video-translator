#!/usr/bin/env bash
# GENERIC single-pack V2 launcher (any app) — the reusable orchestrator the per-batch
# full_batch.sh is an example of. preflight --strict FIRST, arm the self-PARK timer,
# gate on a lang-aware manifest check, run the generic vast_v2_chain, write an atomic
# count-gated BATCH.DONE for the tail. STOP is automatic (park_timer); DESTROY is never here.
#
# Usage: run_batch.sh <pack> <langs> <vobank> <appcfg> <watermark> <outro>
#   e.g. run_batch.sh scoredeck_test id,en \
#          /workspace/jobs/scoredeck_test/_ultimate/vo_bank_score.json \
#          /workspace/jobs/scoredeck_test/_ultimate/app.json \
#          /workspace/jobs/scoredeck_test/_assets/logo.png \
#          /workspace/jobs/scoredeck_test/_assets/outro.mp4
set -uo pipefail
ROOT=/workspace/video-translator
TAIL=/workspace/_tail; mkdir -p "$TAIL"
PACK="${1:?pack}"; LANGS="${2:?langs}"; VOBANK="${3:?vobank}"; APPCFG="${4:?appcfg}"; WM="${5:?watermark}"; OUTRO="${6:?outro}"
JOB=/workspace/jobs/$PACK
export VIDEO_TRANSLATOR_ROOT="$ROOT" PYTHONIOENCODING=utf-8
cd "$ROOT" || exit 9
say(){ echo "===== [$(date +%F_%H:%M:%S)] $* ====="; date +%s > "$TAIL/heartbeat"; }
mark_fail(){ printf '{"status":"FAIL","fail":1,"reason":"%s","finished_at":%s}\n' "$1" "$(date +%s)" > "$TAIL/BATCH.DONE.tmp"; mv -f "$TAIL/BATCH.DONE.tmp" "$TAIL/BATCH.DONE"; }

echo "$$" > "$TAIL/master_queue.pid"     # positive liveness handle for the web idle guard

# 1) deps gate FIRST — never stop mid-run to install/download
say "preflight --strict"
python3 pipeline/preflight.py --strict || { mark_fail preflight; exit 1; }

# 2) creds + arm the self-PARK cost-cap (reversible vastai stop; NEVER destroy)
[ -f /etc/vast.env ] && { set -a; . /etc/vast.env; set +a; }
CID="${CONTAINER_ID:-}"; CID="${CID#C.}"; KEY="${CONTAINER_API_KEY:-}"
if [ -n "$CID" ] && [ -n "$KEY" ]; then
  nohup bash "$ROOT/scripts/park_timer.sh" "$CID" "$KEY" "${MAXPARK:-43200}" "${MAXIDLE:-2700}" >> "$TAIL/park_timer.log" 2>&1 &
  echo $! > "$TAIL/park_timer.pid"; say "self-PARK armed (pid $(cat "$TAIL/park_timer.pid"))"
else
  say "WARN: no CONTAINER_ID/CONTAINER_API_KEY -> NO self-stop (will not bill-cap)"
fi

# 3) lang-aware manifest gate — refuse to render an under-filled manifest
say "manifest gate (status --strict)"
FAIL=0
python3 -u "$ROOT/_claude_skills/meta-ads-prepare-ultimate/run.py" status --src "$JOB" --target-langs "$LANGS" --strict \
  || { echo "MANIFEST INCOMPLETE — fill it before rendering" >&2; mark_fail manifest; exit 1; }

# 4) the generic V2 chain (dub -> brandpass[outro+watermark+fingerprint+bgm] -> voiceover -> package -> qa)
say "vast_v2_chain $PACK [$LANGS]"
bash "$ROOT/scripts/vast_v2_chain.sh" "$JOB" "$LANGS" "$VOBANK" "$APPCFG" "$WM" "$OUTRO" || FAIL=1

# 5) atomic count-gated BATCH.DONE (generic: this pack only)
n2=$(find "$JOB/_out_v2" -name '*.mp4' ! -name '*.tmp.mp4' ! -name '*.bak' 2>/dev/null | wc -l)
tmp=$(find "$JOB" -name '*.tmp.mp4' 2>/dev/null | wc -l)
[ "$tmp" -ne 0 ] && { echo "interrupted *.tmp.mp4 leftovers: $tmp" >&2; FAIL=1; }
[ "$n2" -lt 1 ] && { echo "no _out_v2 mp4 produced" >&2; FAIL=1; }
printf '{"status":"%s","fail":%s,"counts":{"%s":%s},"finished_at":%s}\n' \
  "$([ $FAIL -eq 0 ] && echo OK || echo FAIL)" "$FAIL" "$PACK" "$n2" "$(date +%s)" > "$TAIL/BATCH.DONE.tmp"
mv -f "$TAIL/BATCH.DONE.tmp" "$TAIL/BATCH.DONE"
say "BATCH DONE status=$([ $FAIL -eq 0 ] && echo OK || echo FAIL) ($n2 mp4 in _out_v2)"
exit 0
