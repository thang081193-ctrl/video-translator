---
description: "Vast twin of the meta-ultimate pack flow — dub/localize a video batch with $0 Claude translation: GPU phases (Whisper/Demucs/TTS render) on Vast.ai, translate step in this chat, files synced via scripts/vastai-sync.sh"
---

Hybrid dub flow: every GPU-heavy step runs on the Vast.ai instance; the translate step is done
by Claude IN THIS CHAT (covered by subscription = $0 extra — NEVER route dub translation to
Gemini); files hop between machines with `scripts/vastai-sync.sh`. The local PC stays free —
no Whisper/Demucs/render load (heavy local runs lag the PC). Natural companion to a
`meta-ads-prepare-ultimate` pack: feed it the masters that need voiced localization.

This is the Vast split of the `super-saiyan-translate` flow
(`_claude_skills/super-saiyan-translate/` — SKILL.md + extract.py + apply.py, all git-tracked,
so they exist on Vast at `/workspace/video-translator/` after `git pull`).

**Ask the user for** (or read from context): `<HOST>` `<PORT>` (Vast dashboard → SSH button on
the instance card), the local batch folder, and a batch name. Convention: the job lives at
`/workspace/jobs/<batch>` on Vast.

**Prereqs:** instance installed via /vastai-setup **manual install** path (repo at
`/workspace/video-translator`, system `python3`). The Docker setup's `docker run` has no
`/workspace/jobs` volume mount — for this flow either add `-v /workspace/jobs:/workspace/jobs`
to the run command and prefix phase commands with `docker exec -e PYTHONIOENCODING=utf-8 vt`,
or just use the manual install (simpler). Make sure the repo on Vast is current:
`ssh -p <PORT> root@<HOST> 'cd /workspace/video-translator && git pull --ff-only'`.

## Phase 0 — upload sources (skip if already on Vast)

```bash
bash scripts/vastai-sync.sh up <HOST> <PORT> "<local-batch-dir>" /workspace/jobs/<batch>
```

If the sources come from URLs (Meta Ad Library scrapes etc.), download them directly on Vast
instead — datacenter bandwidth beats a home-upload by a lot.

## Phase 1 — extract on Vast (GPU: Whisper + Demucs)

```bash
ssh -p <PORT> root@<HOST> 'cd /workspace/video-translator && PYTHONIOENCODING=utf-8 \
  python3 -u _claude_skills/super-saiyan-translate/extract.py /workspace/jobs/<batch> --whisper small'
```

`--whisper medium` for noisy/tonal sources. First run downloads Whisper + Demucs weights
(several GB) — slow once, cached after. Writes
`/workspace/jobs/<batch>/_super_saiyan/translations.jsonl` with empty `translation_en` fields.

## Phase 2 — translate locally (the $0 step)

```bash
bash scripts/vastai-sync.sh down <HOST> <PORT> \
  /workspace/jobs/<batch>/_super_saiyan/translations.jsonl \
  "<local-batch-dir>/_super_saiyan/translations.jsonl"
```

Claude fills every `translation_en` following
`_claude_skills/super-saiyan-translate/SKILL.md` → **Translation guidelines** (translate the
hook not the words, match syllable count for TTS timing, keep emojis + product names, native
CTA cadence, only fill `translation_en` — never touch timings). Then push back:

```bash
bash scripts/vastai-sync.sh up <HOST> <PORT> \
  "<local-batch-dir>/_super_saiyan/translations.jsonl" \
  /workspace/jobs/<batch>/_super_saiyan/translations.jsonl
```

## Phase 3 — apply on Vast (GPU: Edge-TTS + mix + render)

```bash
ssh -p <PORT> root@<HOST> 'cd /workspace/video-translator && PYTHONIOENCODING=utf-8 \
  python3 -u _claude_skills/super-saiyan-translate/apply.py /workspace/jobs/<batch> --workers 2'
```

Entries with any empty `translation_en` are skipped with `SKIP-INCOMPLETE` (by design — no
silent Gemini fallback). Edge-TTS needs outbound internet — Vast instances have it. The mixer's
voice-audibility gate (`VOICEMIX` log) is built in.

## Phase 4 — pull finals + QA

```bash
bash scripts/vastai-sync.sh down <HOST> <PORT> \
  /workspace/jobs/<batch>/_translated_en "<local-batch-dir>/_translated_en"
python _claude_skills/meta-ads-prepare/qa_voice_mix.py "<local-batch-dir>/_translated_en" --whisper --expect-voice
```

For big packs, tar on Vast first — one stream beats thousands of small scp round-trips:

```bash
ssh -p <PORT> root@<HOST> 'cd /workspace/jobs/<batch> && tar -cf _translated_en.tar _translated_en'
bash scripts/vastai-sync.sh down <HOST> <PORT> /workspace/jobs/<batch>/_translated_en.tar "<local-batch-dir>/_translated_en.tar"
tar -xf "<local-batch-dir>/_translated_en.tar" -C "<local-batch-dir>"
```

## EN→X localization packs (home-decor / ChartLens style)

Same sync pattern, different scripts: those batches use a batch-specific
`scripts/extract_transcripts.py` + `scripts/apply_dub.py` pair with
`_dub_cache/transcripts.json` + `translations.json` (target-lang fields like `fr`/`tr`) instead
of `_super_saiyan/translations.jsonl`. Adapt the batch script's `ROOT` to
`/workspace/jobs/<batch>` and sync those two JSON files in Phase 2 — everything else is
identical.

## Troubleshooting

- **Demucs `AssertionError` on clips < ~8s**: known htdemucs limit, NOT GPU contention —
  re-run that clip via the music-only path (empty transcript).
- **CUDA OOM**: check `nvidia-smi`. GPU→CPU fallback is sticky after one OOM — restart the
  process to get GPU back.
- **scp asks for password**: the SSH key isn't attached to the instance — add it in the Vast
  console (same fix as push-env-to-vast.sh).
- **Idle auto-stop**: long extract/apply runs over SSH don't tick the web server's idle timer —
  if the instance has auto-stop configured, check it won't kill a mid-batch run.
