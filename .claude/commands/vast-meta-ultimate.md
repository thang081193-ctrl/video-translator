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

## Divide & conquer — when the human is needed (and when NOT)

The whole point: **the operator is needed at EXACTLY TWO brief moments; everything else is
autonomous and the box self-PARKs — never sit at the PC waiting for a render to finish.**

| phase | who | at the PC? |
|---|---|---|
| **0. upload + `scan_prep.sh`** (Whisper transcribe, all packs) | autonomous on Vast | no — launch + walk away |
| **1. fill the manifest** (translations + copy + vertical/angle/`vo_gender` + lyric/off-vertical/betting decisions) | **Opus, in this chat — the ONLY $0 / judgment step** | **yes — ONE short session** |
| **2. `full_batch.sh`** (dub → voiceover → **brandpass = outro + logo watermark + fingerprint change + BGM swap** → package → QA → self-STOP) | autonomous on Vast | no — walk away / sleep |
| **3. `download.sh` + visual QA + `human_destroy.sh`** | operator | **yes — ~5 min, any time later** |

**Opus is needed at EXACTLY ONE point** (phase 1, fill the manifest). brandpass — outro,
watermark, fingerprint, resize, BGM — and dub/voiceover/package/QA are **pure compute Vast
runs alone**. To make phase 1 instant ("turn on the PC and it just works"), phase 0 runs scan
UP FRONT so the transcripts are already waiting: `\$TAIL/SCAN.DONE` is the signal. After phase 1
launches `full_batch.sh`, `park_timer.sh` self-STOPs the box when done (reversible `vastai stop`,
never destroy) so GPU billing ends with zero babysitting; the operator returns whenever.

### Which subcommand runs where

| phase (subcommand) | runs on | why |
|---|---|---|
| `scan` (Whisper) | **Vast** (phase 0, autonomous) | front-loaded so phase 1 needs no wait |
| manifest-fill — vertical / language / copy / `vo_gender` / **translation** | **this chat (Claude)** | edits `manifest.json`; $0, never Gemini; the ONE human/AI gate |
| `bgm-suggest`, `organize` | Vast (in `full_batch.sh`) | pure reasoning / file moves |
| `dub`, `voiceover` (Demucs + Edge-TTS) | **Vast** | source separation + TTS render |
| `brandpass` (resize 9:16 + outro + watermark + fingerprint + BGM) | **Vast** | the encode is GPU-bound — and **no `retrim_endcards` after the outro** (it strips the brand outro — 2026-06-26 bug) |
| `package`, `qa_voice_mix` | **Vast** (in `full_batch.sh`) | run where the files are, before self-STOP |
| pull + **visual outro QA** + destroy | **local** | the hard human gate; destroy is a hand-typed token |

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

## Phase 1 — front-load scan on Vast (autonomous; transcripts ready for the Opus session)

Launch the scan of every uploaded pack and **walk away** — it runs `preflight --strict` first
(so it never stops mid-way to install/download), transcribes, and writes `\$TAIL/SCAN.DONE`:

```bash
ssh -p <PORT> root@<HOST> 'cd /workspace/video-translator && \
  nohup bash scripts/scan_prep.sh > /workspace/_tail/scan_prep.log 2>&1 &'
```
When `SCAN.DONE` exists (`ssh ... 'cat /workspace/_tail/SCAN.DONE'`), the manifests are ready —
turning on the PC + opening this chat lets Opus fill them immediately. With a fresh box, the
one-shot env (Docker image or `deploy/setup.sh`) has already prefetched Whisper/Demucs/EasyOCR
so there is **no multi-GB first-run download**.

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

## Phase 3 — autonomous render + self-PARK (zero babysitting)

Once the manifest is filled, launch the hardened batch and **go to sleep**. `full_batch.sh`
gates each pack on `run.py status --strict` (refuses to start if the manifest is under-filled —
a silently-skipped lang would otherwise be a short pack), runs dub → voiceover → brandpass
(outro + watermark + fingerprint + BGM) → package → QA, writes an atomic count-gated
`BATCH.DONE`, builds the end-frame QA montage, and `park_timer.sh` does the **only** autonomous
lifecycle act — a verified `vastai stop` (reversible; **never destroy**) — so GPU billing ends:

```bash
ssh -p <PORT> root@<HOST> '
  cd /workspace/video-translator &&
  nohup bash scripts/full_batch.sh > /workspace/_tail/full_batch.log 2>&1 &
  nohup bash scripts/tail_qa.sh    > /workspace/_tail/tail_qa.log    2>&1 &
  echo launched'
```
Edge-TTS needs outbound internet (Vast has it); the voice-audibility gate (`VOICEMIX`) is
built in. The box self-STOPs minutes after the work + QA-montage finish; the operator is not
needed until the pull.

## Phase 4 — pull (checked invariant) + visual QA (the human gate, any time later)

One command pulls deliverables **and the manifest-driven source set** (so a re-run never
needs re-translation) **and** the end-frame QA montage, PIPESTATUS-checked, with per-pack count
asserts. It refuses to print `DOWNLOAD_COMPLETE` unless everything is on local disk, then
signals the box to stand its park timer down:

```bash
bash scripts/download.sh <HOST> <PORT> <INSTANCE_ID>
```
`package` + `qa_voice_mix` already ran on Vast inside `full_batch.sh`. **Do NOT run
`retrim_endcards` here** (or anywhere after the outro append — it strips the brand outro, the
2026-06-26 defect; brandpass `--trim-endcard` already removed the competitor card).

Then the **mandatory visual gate**: open `_deliverables_<batch>/_qa_montage/montage_*.jpg` +
`outro_report.json` and eyeball that EVERY pack ends on the brand outro with no competitor
card. Audio/count QA cannot see a missing outro — this human look is the authority.

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
