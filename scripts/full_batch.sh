#!/usr/bin/env bash
# Run the ENTIRE multi-app batch on the Vast box. Order: voiced V2 packs first
# (Opus-translated, highest confidence), then TB V2, then the BGM-only V1 chains.
#
# HARDENED (W3-orchestration, HARDEN_final.md §0a/§5 + AUDIT bug list):
#   - FIRST action: `pipeline/preflight.py --strict` — fail fast (~5s) on any
#     missing binary/model/font BEFORE a single render starts (no mid-run stall).
#   - set -uo pipefail (NOT `set -e`: we want per-phase rc capture, not abort-on-first).
#   - source /etc/vast.env into THIS env + normalize the "C." label prefix off CID.
#   - park_timer.sh (the ONE autonomous cost-cap, a reversible `vastai stop`) armed
#     BEFORE pack 1, so a hang in pack 1 cannot dodge the bill cap.
#   - per-phase rc captured into FAIL; heartbeat refreshed between phases for liveness.
#   - dub/voiced chains gated on `run.py status --strict` (manifest-complete) so an
#     unfilled manifest can't silently produce a short pack.
#   - NO retrim_endcards after the outro append (deleted; see guard comment at §3).
#   - GENERIC count gate (derived per-pack, not the hardcoded 2606 numbers): assert
#     zero *.tmp.mp4/*.bak leftovers AND each pack's _out/_out_v2 mp4 count > 0.
#     Optional EXPECT_<pack> env override raises a pack's floor above 1.
#   - atomic, content-bearing BATCH.DONE marker (tmp + mv -f), never a bare echo.
set -uo pipefail

ROOT=/workspace/video-translator
RUN="$ROOT/_claude_skills/meta-ads-prepare-ultimate/run.py"
QA="$ROOT/_claude_skills/meta-ads-prepare/qa_voice_mix.py"
BGM="$ROOT/_bgm"
CHAIN2="$ROOT/scripts/vast_v2_chain.sh"   # voiced V2 chain (lives on box; audit for retrim-after-outro)
CHAIN1="$ROOT/scripts/vast_v1_chain.sh"   # BGM-only V1 chain (lives on box)
export VIDEO_TRANSLATOR_ROOT="$ROOT" PYTHONIOENCODING=utf-8
cd "$ROOT" || exit 9

TAIL=/workspace/_tail
mkdir -p "$TAIL"
say(){ echo "===== [$(date +%F_%H:%M:%S)] $* ====="; date +%s > "$TAIL/heartbeat"; }
hb(){ date +%s > "$TAIL/heartbeat"; }

# ---- STRICT PREFLIGHT: first thing, before anything renders. Exits on fail. ----
# Wired here because preflight was previously dead code (only deploy/start.sh called it).
say "preflight --strict"
if ! python3 "$ROOT/pipeline/preflight.py" --strict; then
  echo "PREFLIGHT FAILED -> aborting batch before any render" >&2
  printf '{"status":"FAIL","fail":1,"counts_ok":0,"counts":{},"finished_at":%s,"reason":"preflight"}\n' \
    "$(date +%s)" > "$TAIL/BATCH.DONE.tmp"
  mv -f "$TAIL/BATCH.DONE.tmp" "$TAIL/BATCH.DONE"
  exit 1
fi

# ---- creds: read persisted Vast env into THIS shell, normalize the "C." label ----
if [ -f /etc/vast.env ]; then set -a; . /etc/vast.env; set +a; fi
CID="${CONTAINER_ID:-}"; CID="${CID#C.}"      # strip "C." label prefix -> bare instance id
KEY="${CONTAINER_API_KEY:-}"

# ---- UNCONDITIONAL cost-cap armed BEFORE pack 1 (a hang can't dodge it). STOP, never destroy ----
MAXPARK=${MAXPARK:-43200}     # 12h hard wall-clock cap
MAXIDLE=${MAXIDLE:-2700}      # 45min: must exceed the largest legit inter-mp4 gap
if [ -n "$CID" ] && [ -n "$KEY" ]; then
  nohup bash "$ROOT/scripts/park_timer.sh" "$CID" "$KEY" "$MAXPARK" "$MAXIDLE" \
        >> "$TAIL/park_timer.log" 2>&1 &
  echo $! > "$TAIL/park_timer.pid"
  say "park_timer cost-cap armed (pid $(cat "$TAIL/park_timer.pid"), MAXPARK=${MAXPARK}s MAXIDLE=${MAXIDLE}s)"
else
  say "WARN: CONTAINER_ID/CONTAINER_API_KEY absent -> NO self-stop armed (will NOT bill-cap)"
fi

echo "$$" > "$TAIL/master_queue.pid"   # positive liveness handle (replaces hardcoded PID 45801)

TB=/workspace/jobs/tb090626
DECO=/workspace/jobs/deco2506
SCORE=/workspace/jobs/score2506
W=10   # brandpass workers (VRAM-safe)

FAIL=0
phase(){ # <label> <cmd...> : run, capture rc, accumulate FAIL, heartbeat
  local label="$1"; shift
  say "$label start"
  "$@"; local rc=$?
  hb
  if [ "$rc" -ne 0 ]; then echo "PHASE_FAIL rc=$rc :: $label" >&2; FAIL=1; fi
  say "$label rc=$rc"
}

# Gate any dub/voiced/brandpass work on a manifest-complete check. run.py `status --strict`
# must exit non-zero on any MISSING-* field (so an unfilled manifest never silently short-runs).
manifest_ready(){ # <src_dir> <langs>
  python3 -u "$RUN" status --src "$1" --target-langs "$2" --strict
}

# ---------- 1. DecoAI V2 ----------
if manifest_ready "$DECO" id,fr,en; then
  phase "DECO V2" bash "$CHAIN2" "$DECO" id,fr,en \
    "$DECO/_ultimate/vo_bank_deco.json" "$DECO/_ultimate/app.json" \
    "$DECO/_assets/logo.png" "$DECO/_assets/outro.mp4"
else
  echo "PHASE_FAIL rc=manifest :: DECO V2 (status --strict failed; manifest incomplete)" >&2; FAIL=1
fi

# ---------- 2. ScoreDeck V2 ----------
if manifest_ready "$SCORE" en,id,th,vi; then
  phase "SCORE V2" bash "$CHAIN2" "$SCORE" en,id,th,vi \
    "$SCORE/_ultimate/vo_bank_score.json" "$SCORE/_ultimate/app.json" \
    "$SCORE/_assets/logo.png" "$SCORE/_assets/outro.mp4"
else
  echo "PHASE_FAIL rc=manifest :: SCORE V2 (status --strict failed; manifest incomplete)" >&2; FAIL=1
fi

# ---------- 3. TradeBuddy V2 (inlined: brandpass -> voiceover -> package -> qa) ----------
if manifest_ready "$TB" ar,ru,id,en; then
  phase "TB V2 brandpass" python3 -u "$RUN" brandpass --src "$TB" --dst "$TB/_out_v2" \
    --target-langs ar,ru,id,en --watermark "$TB/_assets/logo.png" \
    --outro-video "$TB/_assets/outro.mp4" --bgm-pool "$BGM" --trim-endcard --workers "$W"
  phase "TB V2 voiceover" python3 -u "$RUN" voiceover --src "$TB" --dst "$TB/_out_v2" \
    --app-config "$TB/_ultimate/app.json" --vo-bank "$TB/_ultimate/vo_bank_trade.json" \
    --target-langs ar,ru,id,en
  phase "TB V2 package" python3 -u "$RUN" package --src "$TB" --dst "$TB/_out_v2" \
    --target-langs ar,ru,id,en
  # ----------------------------------------------------------------------------
  # ROOT-CAUSE FIX (6/27 outro bug): `retrim_endcards.py all` is REMOVED here.
  # `--trim-endcard` on brandpass already strips the competitor card; `package`
  # appended the BRAND outro; a post-append retrim ate the brand outro (postmortem
  # 2026-06-26 "Mistake 2"). brandpass owns trimming, package owns the outro.
  # DO NOT re-add a retrim after the outro append, in this file OR the box chains.
  # ----------------------------------------------------------------------------
  phase "TB V2 qa" python3 -u "$QA" "$TB/_out_v2" --whisper --expect-voice
else
  echo "PHASE_FAIL rc=manifest :: TB V2 (status --strict failed; manifest incomplete)" >&2; FAIL=1
fi

# ---------- 4. BGM-only V1 chains ----------
phase "DECO V1" bash "$CHAIN1" /workspace/jobs/deco2506_v1 "$DECO/_assets/logo.png" "$DECO/_assets/outro.mp4"
phase "SCORE V1" bash "$CHAIN1" /workspace/jobs/score2506_v1 "$SCORE/_assets/logo.png" "$SCORE/_assets/outro.mp4"
phase "TB V1" bash "$CHAIN1" /workspace/jobs/tb090626_v1 "$TB/_assets/logo.png" "$TB/_assets/outro.mp4"

# ---------- 5. GENERIC count-verified, exit-gated, ATOMIC completion marker ----------
# Counts EXCLUDE *.tmp.mp4 / *.bak (interrupted os.replace leftovers inflate counts).
# NOT hardcoded to the 2606 numbers: floor = 1 (each pack must produce >0 mp4) unless an
# EXPECT_<pack> env override raises it. *.tmp.mp4 leftovers are a hard integrity failure.
say "counting outputs (generic gate)"
COUNTS_OK=1
# Derive the pack list from the jobs dirs that actually exist (so this isn't pinned to 6 packs).
PACKS=()
for J in deco2506 score2506 tb090626 deco2506_v1 score2506_v1 tb090626_v1; do
  [ -d "/workspace/jobs/$J" ] && PACKS+=("$J")
done
counts_json="{"
for J in "${PACKS[@]}"; do
  d2=$(find "/workspace/jobs/$J/_out_v2" -name '*.mp4' ! -name '*.tmp.mp4' ! -name '*.bak' 2>/dev/null | wc -l)
  d1=$(find "/workspace/jobs/$J/_out"    -name '*.mp4' ! -name '*.tmp.mp4' ! -name '*.bak' 2>/dev/null | wc -l)
  n=$(( d2 + d1 ))
  tmp=$(find "/workspace/jobs/$J" -name '*.tmp.mp4' 2>/dev/null | wc -l)
  # generic expected floor: env override EXPECT_<pack>, else 1 (must produce at least one mp4)
  ev="EXPECT_${J}"; exp="${!ev:-1}"
  echo "  $J: _out=$d1 _out_v2=$d2 total=$n (tmp_leftover=$tmp floor=$exp)"
  if [ "$tmp" -ne 0 ]; then echo "  -> $J has $tmp interrupted *.tmp.mp4 (data-integrity risk)"; COUNTS_OK=0; fi
  if [ "$n" -lt "$exp" ]; then echo "  -> $J COUNT SHORT ($n < $exp)"; COUNTS_OK=0; fi
  counts_json="$counts_json\"$J\":$n,"
done
counts_json="${counts_json%,}}"
[ "$counts_json" = "}" ] && counts_json="{}"

if [ "$FAIL" -eq 0 ] && [ "$COUNTS_OK" -eq 1 ]; then STATUS=OK; else STATUS=FAIL; fi

# atomic write: tmp + mv -f (a watcher must never read a half-written marker)
printf '{"status":"%s","fail":%s,"counts_ok":%s,"counts":%s,"finished_at":%s}\n' \
  "$STATUS" "$FAIL" "$COUNTS_OK" "$counts_json" "$(date +%s)" > "$TAIL/BATCH.DONE.tmp"
mv -f "$TAIL/BATCH.DONE.tmp" "$TAIL/BATCH.DONE"

if [ "$STATUS" = OK ]; then
  say "FULL BATCH COMPLETE (status=OK)"
else
  say "FULL BATCH FAILED (status=$STATUS fail=$FAIL counts_ok=$COUNTS_OK)"
fi
exit 0
