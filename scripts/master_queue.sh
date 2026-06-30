#!/usr/bin/env bash
# ONE-OFF unattended orchestrator for the 2026-06-25 multi-app batch.
# Runs sequentially on Vast (single GPU) so phases never contend:
#   1. wait for the already-running TradeBuddy V2 brandpass to finish
#   2. TradeBuddy V2 tail: voiceover -> package -> retrim -> qa
#   3. TradeBuddy V1 (BGM-only) full chain
#   4. DecoAI V1 (BGM-only) full chain
#   5. ScoreDeck V1 (BGM-only) full chain
# Does NOT destroy the instance (DecoAI/ScoreDeck VOICED V2 still pending a live fill).
set -u
ROOT=/workspace/video-translator
RUN="$ROOT/_claude_skills/meta-ads-prepare-ultimate/run.py"
RETRIM="$ROOT/_claude_skills/meta-ads-prepare-ultimate/retrim_endcards.py"
QA="$ROOT/_claude_skills/meta-ads-prepare/qa_voice_mix.py"
BGM="$ROOT/_bgm"
export VIDEO_TRANSLATOR_ROOT="$ROOT" PYTHONIOENCODING=utf-8
cd "$ROOT" || exit 9
say(){ echo "===== [$(date +%F_%H:%M:%S)] $* ====="; }

TB=/workspace/jobs/tb090626
TBWM="$TB/_assets/logo.png"; TBOUT="$TB/_assets/outro.mp4"

# ---- 1. wait for the in-flight TB V2 brandpass to complete ----
say "WAIT for TB V2 brandpass to finish"
while ! grep -aqE "^\[brandpass\] ok=" "$TB/_ultimate/brandpass_v2.log" 2>/dev/null; do
  sleep 30
done
say "TB V2 brandpass finished: $(grep -aE '^\[brandpass\] ok=' "$TB/_ultimate/brandpass_v2.log" | tail -1)"

# ---- 2. TB V2 tail ----
DST="$TB/_out_v2"
say "TB V2 voiceover"
python3 -u "$RUN" voiceover --src "$TB" --dst "$DST" --app-config "$TB/_ultimate/app.json" \
  --vo-bank "$TB/_ultimate/vo_bank_trade.json" --target-langs ar,ru,id,en > "$TB/_ultimate/v2_voiceover.log" 2>&1
say "TB V2 package"
python3 -u "$RUN" package --src "$TB" --dst "$DST" --target-langs ar,ru,id,en > "$TB/_ultimate/v2_package.log" 2>&1
python3 -u "$RETRIM" all "$DST" > "$TB/_ultimate/v2_retrim.log" 2>&1
python3 -u "$QA" "$DST" --whisper --expect-voice > "$TB/_ultimate/v2_qa.log" 2>&1
say "TB V2 DONE"

# ---- 3. TB V1 ----
say "TB V1 chain"
bash "$ROOT/scripts/vast_v1_chain.sh" /workspace/jobs/tb090626_v1 "$TBWM" "$TBOUT"

# ---- 4. DecoAI V1 ----
say "DecoAI V1 chain"
bash "$ROOT/scripts/vast_v1_chain.sh" /workspace/jobs/deco2506_v1 \
  /workspace/jobs/deco2506/_assets/logo.png /workspace/jobs/deco2506/_assets/outro.mp4

# ---- 5. ScoreDeck V1 ----
say "ScoreDeck V1 chain"
bash "$ROOT/scripts/vast_v1_chain.sh" /workspace/jobs/score2506_v1 \
  /workspace/jobs/score2506/_assets/logo.png /workspace/jobs/score2506/_assets/outro.mp4

say "MASTER QUEUE COMPLETE (TB V2+V1, Deco V1, Score V1). DecoAI/ScoreDeck VOICED V2 still pending live fill."
