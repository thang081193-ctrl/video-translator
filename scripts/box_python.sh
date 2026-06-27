#!/usr/bin/env bash
# scripts/box_python.sh — SOURCE this near the top of every BOX-SIDE entry script.
# ---------------------------------------------------------------------------
# Vast GPU images (e.g. vastai/pytorch:cuda-13.1.2-auto) ship torch inside a
# venv (/venv/main) or conda env that is on PATH ONLY for LOGIN shells. A
# non-interactive `ssh host 'script.sh'` — exactly how this tail layer launches
# work — gets the bare system /usr/bin/python3 with NO torch, so the brandpass
# CUDA gate + Demucs separation silently CPU-fall or hard ImportError. This
# prepends the first python3 that can actually `import torch`, so `python3`
# resolves correctly for THIS script and every child it spawns (the nohup'd
# park_timer, vast_v2_chain, etc. all inherit the exported PATH).
#
# Safe to source repeatedly (PATH guard => idempotent) and safe on the LAPTOP
# (none of the candidate dirs exist there, so it's a no-op and the local python
# is left untouched). Never exits / never sets -e — it only mutates PATH.
# ---------------------------------------------------------------------------
_vt_pick_py() {
  local cand
  # already have a torch-capable python3 on PATH? then nothing to do.
  if command -v python3 >/dev/null 2>&1 && python3 -c 'import torch' >/dev/null 2>&1; then
    return 0
  fi
  for cand in /venv/main/bin /venv/*/bin /opt/conda/bin /usr/local/bin; do
    [ -x "$cand/python3" ] || continue
    case ":$PATH:" in *":$cand:"*) continue;; esac
    if "$cand/python3" -c 'import torch' >/dev/null 2>&1; then
      PATH="$cand:$PATH"; export PATH
      return 0
    fi
  done
  return 0   # leave PATH as-is; preflight/setup will report a real torch miss
}
_vt_pick_py
