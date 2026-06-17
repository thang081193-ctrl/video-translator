---
description: "Local-extended twin of meta-ads-prepare-ultimate: runs the SAME full pipeline but offloads every GPU-heavy phase (scan/Whisper, dub/Demucs+TTS, voiceover, brandpass/render) to a rented Vast.ai GPU, keeps the translate step $0 in THIS chat (Claude, never Gemini), and AUTO-DESTROYS the Vast instance when the batch is verified done. Use when the local PC can't run the GPU work or you don't want it lagging the machine."
---

`vast-meta-ultimate` is **`meta-ads-prepare-ultimate` extended to Vast.ai** — same skill,
same `run.py`, same manifest. The ONLY difference vs the local skill: the GPU-heavy phases
run on a rented Vast GPU instead of this PC, and the instance is **auto-destroyed** at the
end. The LLM-reasoning + translate steps stay in **THIS chat** (Claude, $0 — NEVER route
translation to Gemini). The local PC stays free (no Whisper/Demucs/render load lagging it).

Read `_claude_skills/meta-ads-prepare-ultimate/SKILL.md` for the pipeline itself (the 6 steps,
the manifest schema, the voiced/BGM-only split, all subcommand flags). This doc only covers
**where each phase runs + the sync + the auto-destroy**. `run.py` is git-tracked, so after a
`git pull` it already exists on Vast at `/workspace/video-translator/`; files hop between
machines with `scripts/vastai-sync.sh`. Convention: the job lives at `/workspace/jobs/<batch>`
on Vast, `<local-batch-dir>` locally; the whole job folder (incl. `_ultimate/manifest.json`)
is what you sync.

## Which phase runs where

| phase (meta-ultimate subcommand) | runs on | why |
|---|---|---|
| `scan` (Whisper) | **Vast (GPU)** | transcription is the heaviest step |
| Step 2 manifest-fill — vertical / language / copy / **translation** | **this chat (Claude)** | edits `manifest.json`; $0, never Gemini |
| `bgm-suggest`, `organize` | either (local fine) | pure reasoning / file moves |
| `dub`, `voiceover` (Demucs + Edge-TTS) | **Vast (GPU)** | source separation + TTS render |
| `brandpass` (resize 9:16 + render + BGM swap) | **Vast (GPU)** | the encode is GPU-bound |
| `package`, `retrim_endcards`, `qa_voice_mix` | **local** | CSV / QA + cv2 card-detect, light |

## Prereqs + capture instance identity upfront

**Ask the user for** (or read from context): the local batch folder, a batch name, and the
Vast instance — `<HOST>` `<PORT>` from the dashboard SSH button, **and the `<INSTANCE_ID>`**
(needed for auto-destroy). Grab the id with the vastai CLI if not given:

```bash
vastai show instances          # first column = INSTANCE_ID; note the one matching <HOST>:<PORT>
```

- Instance from /vastai-setup **manual install** path (repo at `/workspace/video-translator`,
  system `python3`). Docker setup needs `-v /workspace/jobs:/workspace/jobs` + a `docker exec
  -e PYTHONIOENCODING=utf-8 vt` prefix — manual install is simpler for this flow.
- vastai CLI installed + keyed locally for the destroy step: `pip install vastai` then
  `vastai set api-key <VAST_API_KEY>` (key from the Vast account page). Verify: `vastai show
  instances` lists your instance.
- Make the repo on Vast current first:
  `ssh -p <PORT> root@<HOST> 'cd /workspace/video-translator && git pull --ff-only'`.

## Phase 0 — upload sources (skip if already on Vast)

```bash
bash scripts/vastai-sync.sh up <HOST> <PORT> "<local-batch-dir>" /workspace/jobs/<batch>
```
If the sources come from URLs (Meta Ad Library scrapes etc.), download them directly on Vast —
datacenter bandwidth beats a home upload by a lot.

## Phase 1 — scan on Vast (GPU: Whisper, once)

```bash
ssh -p <PORT> root@<HOST> 'cd /workspace/video-translator && PYTHONIOENCODING=utf-8 \
  python3 -u _claude_skills/meta-ads-prepare-ultimate/run.py scan \
  --src /workspace/jobs/<batch> --whisper small'
```
First run downloads Whisper + Demucs weights (several GB) — slow once, cached after. Writes
`/workspace/jobs/<batch>/_ultimate/manifest.json`. `--whisper medium` for noisy/tonal sources.

## Phase 2 — fill the manifest IN THIS CHAT (the $0 step)

```bash
bash scripts/vastai-sync.sh down <HOST> <PORT> \
  /workspace/jobs/<batch>/_ultimate/manifest.json \
  "<local-batch-dir>/_ultimate/manifest.json"
```
Claude fills every video's `vertical`, `language`, `copy`, and `segments[].translations`
per meta-ultimate SKILL.md → Step 2 (read the transcript, NOT the folder; translate the hook
not the words; never Gemini). Then push the manifest back:

```bash
bash scripts/vastai-sync.sh up <HOST> <PORT> \
  "<local-batch-dir>/_ultimate/manifest.json" \
  /workspace/jobs/<batch>/_ultimate/manifest.json
```

(`organize` + `bgm-suggest` can run locally or on Vast — they're light. Run `organize` where
the files are about to be processed; for this flow, on Vast just before dub/brandpass.)

## Phase 3 — dub / voiceover / brandpass on Vast (GPU)

Same `run.py` subcommands as the local skill, just over SSH. Example brandpass:

```bash
ssh -p <PORT> root@<HOST> 'cd /workspace/video-translator && PYTHONIOENCODING=utf-8 \
  python3 -u _claude_skills/meta-ads-prepare-ultimate/run.py brandpass \
  --src /workspace/jobs/<batch> --dst /workspace/jobs/<batch>/_out \
  --vertical <v> --target-langs <...> --watermark <...> [--bgm-pool <...>] --workers 4'
```
Run `dub` / `voiceover` the same way when the batch needs them. Edge-TTS needs outbound
internet — Vast instances have it. The voice-audibility gate (`VOICEMIX` log) is built in.

## Phase 4 — pull deliverables + finish locally (MUST pass before destroy)

Tar the campaign tree on Vast first (one stream beats thousands of scp round-trips):

```bash
ssh -p <PORT> root@<HOST> 'cd /workspace/jobs/<batch> && tar -cf _out.tar _out'
bash scripts/vastai-sync.sh down <HOST> <PORT> /workspace/jobs/<batch>/_out.tar "<local-batch-dir>/_out.tar"
tar -xf "<local-batch-dir>/_out.tar" -C "<local-batch-dir>"
```
Then finish locally (these are light, no GPU):

```bash
python _claude_skills/meta-ads-prepare-ultimate/run.py package --src "<local-batch-dir>" --dst "<local-batch-dir>/_out" --vertical <v> --target-langs <...>
python _claude_skills/meta-ads-prepare-ultimate/retrim_endcards.py all "<local-batch-dir>/_out"
python _claude_skills/meta-ads-prepare/qa_voice_mix.py "<local-batch-dir>/_out" --whisper --expect-voice
```

## Phase 5 — AUTO-DESTROY the Vast instance

**Hard gate — destroy is IRREVERSIBLE (kills the instance + its disk).** Only proceed once
ALL of these hold, else you lose the GPU work:
1. The deliverables tar downloaded + untarred locally (the `_out/` tree exists locally).
2. `package` QA gate passed (`qa_report.csv` → 0 FAIL) and the local file count matches the
   expected output count.
3. Nothing else still running on the instance you need.

When the gate passes, destroy automatically:

```bash
vastai destroy instance <INSTANCE_ID>
vastai show instances          # confirm it's gone (no longer listed)
```
This stops billing immediately. (Use `vastai stop instance <INSTANCE_ID>` only if the user
explicitly wants to KEEP the disk for a follow-up run — the default for this skill is
**destroy**, per its contract.) If the deliverables did NOT verify, do **not** destroy — keep
the instance and re-pull / re-run the failed phase first.

## Pure dub-only batch (lean shortcut)

If all you need is "localize the voice" — no classify / organize / brandpass — skip the full
pipeline and use the super-saiyan scripts instead (still GPU-on-Vast + $0 translate + the same
sync + auto-destroy gate):
`extract.py` (Phase 1) → fill `translation_en` in chat (Phase 2) → `apply.py` (Phase 3), all
under `_claude_skills/super-saiyan-translate/`. Entries with an empty `translation_en` are
skipped `SKIP-INCOMPLETE` (no silent Gemini fallback).

## Troubleshooting

- **Demucs `AssertionError` on clips < ~8s**: known htdemucs limit, NOT GPU contention —
  re-run that clip via the music-only path (empty transcript).
- **CUDA OOM**: check `nvidia-smi`. GPU→CPU fallback is sticky after one OOM — restart the
  process to get GPU back.
- **scp asks for password**: the SSH key isn't attached to the instance — add it in the Vast
  console (same fix as push-env-to-vast.sh).
- **`vastai` not found / not authed**: `pip install vastai` + `vastai set api-key <key>`.
- **Idle auto-stop**: long extract/apply runs over SSH don't tick the web server's idle timer —
  if the instance has auto-stop configured, make sure it won't kill a mid-batch run.
- See `reference_vastai_brandpass.md` (memory) for the torch cu124 pin + faster-whisper import
  gotcha + GPU-snapshot false alarm.
