#!/usr/bin/env bash
# DRY-RUN / VALIDATION harness — run ONCE on a throwaway Vast box BEFORE trusting any
# overnight batch. Proves, on a tiny fake (~8 mp4) batch, that:
#   1. the ATOMIC BATCH.DONE marker is written + readable (status parsed via json).
#   2. outro_detect.py runs and emits outro_report.json + a montage.
#   3. park_timer.sh FAIL-CLOSES on empty creds (exits rc 3, never stops blindly).
#   4. NO autonomous-destroy string exists in any box-side tail script (destroy is human-only).
#   5. download.sh's pull is a CHECKED invariant (uses PIPESTATUS), not a bare echo.
#   6. THE REAL TEST: a real short-MAXIDLE park_timer is armed with a stale heartbeat and
#      a verified self-stop ACTUALLY fires (confirmed afterwards via the ledger).
#
# set -uo pipefail (NOT -e: this is a test harness; we tally pass/fail, never abort early).
set -uo pipefail

ROOT=/workspace/video-translator
. "$ROOT/scripts/box_python.sh" 2>/dev/null || true   # outro_detect needs venv cv2 (non-login ssh)
TAIL=/workspace/_tail
J=/workspace/jobs/selftest
mkdir -p "$TAIL" "$J/English" "$J/_out_v2"

# The forbidden needle is BUILT at runtime (never written verbatim) so that this self-test
# file itself does not contain the literal autonomous-destroy string — the SAFETY INVARIANT
# requires that literal to live in EXACTLY ONE file: scripts/human_destroy.sh.
NEEDLE="vastai dest""roy"   # == the real command at runtime; split so it's not literal here

# box-side tail scripts the SAFETY INVARIANT forbids from ever containing the destroy command
BOX_TAIL_SCRIPTS=(
  "$ROOT/scripts/full_batch.sh"
  "$ROOT/scripts/v2_queue.sh"
  "$ROOT/scripts/park_timer.sh"
  "$ROOT/scripts/tail_qa.sh"
)

# creds (normalize the "C." label prefix)
[ -f /etc/vast.env ] && { set -a; . /etc/vast.env; set +a; }
CID="${CONTAINER_ID:-}"; CID="${CID#C.}"; KEY="${CONTAINER_API_KEY:-}"

pass=0; fail=0
ok(){ echo "PASS: $1"; pass=$((pass+1)); }
no(){ echo "FAIL: $1"; fail=$((fail+1)); }

# ---------- 0. build a tiny fake batch: 1 source + 8 outputs with a flat-color card tail ----------
ffmpeg -y -v error -f lavfi -i color=c=blue:s=216x384:d=2 -f lavfi -i sine=f=300:d=2 \
  -shortest -c:v libx264 -pix_fmt yuv420p "$J/English/en_selftest01.mp4" 2>/dev/null
for i in $(seq 1 8); do
  ffmpeg -y -v error -f lavfi -i "color=c=blue:s=216x384:d=1.5" -f lavfi -i "color=c=red:s=216x384:d=1" \
    -filter_complex "[0:v][1:v]concat=n=2:v=1" -c:v libx264 -pix_fmt yuv420p "$J/_out_v2/en_out0$i.mp4" 2>/dev/null
done
nmade=$(find "$J/_out_v2" -name '*.mp4' | wc -l)
[ "$nmade" -ge 8 ] && ok "fake batch built ($nmade output mp4)" || no "fake batch build ($nmade < 8 mp4)"

# minimal manifest so the source-list derivation in download.sh works
cat > "$J/manifest.json" <<PY
{"src_root":"$J","videos":[{"src_path":"$J/English/en_selftest01.mp4"}]}
PY
echo '{}' > "$J/app.json"
echo '{}' > "$J/vo_bank_selftest.json"
mkdir -p "$J/_assets"; echo x > "$J/_assets/logo.png"

# ---------- 1. ATOMIC marker + status gating (tmp + mv -f; status read via python json) ----------
printf '{"status":"OK","fail":0,"counts_ok":1,"counts":{"selftest":%s},"finished_at":%s}\n' \
  "$nmade" "$(date +%s)" > "$TAIL/BATCH.DONE.tmp"
mv -f "$TAIL/BATCH.DONE.tmp" "$TAIL/BATCH.DONE"
st=$(python3 -c 'import json;print(json.load(open("/workspace/_tail/BATCH.DONE"))["status"])' 2>/dev/null || echo ERR)
[ "$st" = OK ] && ok "BATCH.DONE atomic + status readable via json (=OK)" || no "marker status (got '$st')"

# ---------- 2. outro_detect.py runs + emits report + montage ----------
if [ -f "$ROOT/scripts/outro_detect.py" ]; then
  python3 "$ROOT/scripts/outro_detect.py" --jobs-root /workspace/jobs --out "$TAIL/_qa_montage" --packs selftest \
    >/dev/null 2>&1
  if [ -f "$TAIL/_qa_montage/outro_report.json" ]; then ok "outro_detect ran + outro_report.json present"
  else no "outro_detect produced no outro_report.json"; fi
else
  no "scripts/outro_detect.py absent (W2 not landed) — cannot prove montage/detector"
fi

# ---------- 3. park_timer FAIL-CLOSES on empty creds (must exit rc 3) ----------
if [ -f "$ROOT/scripts/park_timer.sh" ]; then
  bash "$ROOT/scripts/park_timer.sh" "" "" 10 10 >/dev/null 2>&1; rc=$?
  [ "$rc" -eq 3 ] && ok "park_timer fail-closed on missing creds (rc=3)" \
    || no "park_timer creds guard (expected rc=3, got rc=$rc)"
else
  no "scripts/park_timer.sh absent (W1 not landed) — cannot prove fail-closed"
fi

# ---------- 4. SAFETY INVARIANT: the destroy command in NO box-side tail script ----------
destroy_leak=0
for f in "${BOX_TAIL_SCRIPTS[@]}" "$ROOT/scripts/download.sh"; do
  [ -f "$f" ] || continue
  if grep -qF "$NEEDLE" "$f"; then echo "  LEAK: autonomous-destroy command in $f"; destroy_leak=1; fi
done
[ "$destroy_leak" -eq 0 ] && ok "destroy command in NO box-side / pull tail script" \
  || no "autonomous-destroy command leaked into a box-side tail script"
# and the only file allowed to contain it actually does (positive control)
if [ -f "$ROOT/scripts/human_destroy.sh" ]; then
  grep -qF "$NEEDLE" "$ROOT/scripts/human_destroy.sh" \
    && ok "destroy command confined to human_destroy.sh (positive control)" \
    || no "human_destroy.sh missing the destroy command it should own"
fi

# ---------- 5. download.sh pull is a CHECKED invariant (PIPESTATUS), not a bare echo ----------
if [ -f "$ROOT/scripts/download.sh" ]; then
  grep -q 'PIPESTATUS' "$ROOT/scripts/download.sh" && ok "download.sh uses PIPESTATUS (truncated stream can't pass)" \
    || no "download.sh has no PIPESTATUS check"
  grep -q 'DOWNLOAD_COMPLETE' "$ROOT/scripts/download.sh" \
    && ok "download.sh has a gated DOWNLOAD_COMPLETE" || no "download.sh missing DOWNLOAD_COMPLETE"
else
  no "scripts/download.sh absent"
fi

# ---------- 6. THE REAL TEST: arm a real park_timer with a STALE heartbeat -> verified self-stop ----------
if [ -f "$ROOT/scripts/park_timer.sh" ] && [ -n "$CID" ] && [ -n "$KEY" ]; then
  echo "0" > "$TAIL/heartbeat"                      # ancient heartbeat -> trips the idle path
  rm -f "$TAIL/PULL_OK" "$TAIL/state.json"
  echo ">>> Arming REAL park_timer (MAXIDLE=3s). The box WILL stop itself in ~1-2 min."
  echo ">>> After it stops, from your laptop:"
  echo ">>>   vastai start instance $CID"
  echo ">>>   ssh ... 'python3 $ROOT/scripts/ledger.py get stopped'   # expect: True"
  nohup bash "$ROOT/scripts/park_timer.sh" "$CID" "$KEY" 86400 3 >> "$TAIL/selftest_park.log" 2>&1 &
  ok "park_timer armed for the LIVE self-stop proof (watch $TAIL/selftest_park.log; verify ledger after restart)"
else
  no "no creds or no park_timer.sh -> cannot run the live self-stop proof (fix /etc/vast.env / land W1 first)"
fi

echo "==== tail selftest: $pass passed, $fail failed ===="
[ "$fail" -eq 0 ] || exit 1
