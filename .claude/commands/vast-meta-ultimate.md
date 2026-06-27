---
description: "Local-extended twin of meta-ads-prepare-ultimate: runs the SAME full pipeline but offloads every GPU-heavy phase (scan/Whisper, dub/Demucs+TTS, voiceover, brandpass/render) to a rented Vast.ai GPU, keeps the translate step $0 in THIS chat (Claude, never Gemini), and STOPS (reversible, never auto-destroy) the Vast instance when the batch is verified done — destroy is a human-typed token after visual QA. Use when the local PC can't run the GPU work or you don't want it lagging the machine."
---

`vast-meta-ultimate` is **`meta-ads-prepare-ultimate` extended to Vast.ai** — same skill,
same `run.py`, same manifest. The ONLY difference vs the local skill: the GPU-heavy phases
run on a rented Vast GPU instead of this PC, and the instance is **STOPPED** (reversible) at
the end — **never auto-destroyed**. Destroy is a deliberate, human-typed token issued via
`scripts/human_destroy.sh` AFTER visual outro/branding QA (audio QA is blind to a missing
brand outro — see the 2026-06-26 post-mortem). The LLM-reasoning + translate steps stay in
**THIS chat** (Claude, $0 — NEVER route
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

## Phase 5 — STOP only (NEVER auto-destroy)

This skill and ANY box-side script may ONLY `vastai stop` (reversible — keeps the disk and
the rendered tree; just pauses billing). **`vastai destroy` is FORBIDDEN to every
script/agent in this flow.** The 2026-06-26 post-mortem proved a green *audio* QA gate is
**blind to a missing brand outro** — exactly the defect that shipped — so destroy must not key
off it. Destroy is a **HUMAN** action, performed only after a human eyeballs the end-frame
montage.

When the deliverables verify (deliverables + source set pulled locally, `package` QA → 0 FAIL,
local count matches expected), STOP the instance — do NOT destroy:

```bash
vastai stop instance <INSTANCE_ID>          # reversible; disk + renders survive
vastai show instance <INSTANCE_ID> --raw    # confirm actual_status in stopped/exited/offline
```

If the deliverables did NOT verify, do **not** stop yet — re-pull / re-run the failed phase
first (a stopped box can be `vastai start instance <INSTANCE_ID>` to resume).

### Destroy is a separate, human-typed gate (after visual QA)

Destroy is IRREVERSIBLE (kills the instance + its disk). It happens ONLY after a human:
1. Confirmed the local pull is complete (deliverables + manifest-driven source set + montage
   all on local disk).
2. **OPENED the end-frame montage + `outro_report.json` and visually confirmed the brand
   outro on EVERY pack** with zero unexplained `suspect_no_outro`.

Then the operator hand-types the instance id AND the literal word `DESTROY`:

```bash
bash scripts/human_destroy.sh <INSTANCE_ID> DESTROY
```

No script/agent may synthesize that `DESTROY` token or call `vastai destroy` on its own — the
only file in this repo permitted to contain the string `vastai destroy` is
`scripts/human_destroy.sh`. Any orchestrator/tail must assert its argv/state never contains
the string `destroy`.

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
