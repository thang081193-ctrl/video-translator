---
name: super-saiyan-translate
description: High-quality video translation workflow where the LLM in this chat (Claude Opus) handles the translate step instead of the project's automated Gemini Flash call — preserving ad-hook intent, idiom, and cultural nuance. Two-phase: `extract` produces a JSONL of native-language transcripts; the user pastes it into the chat; the model returns the same JSONL with `translation_en` filled; `apply` dubs + mixes using the pre-translated text. Use when the user wants higher translation quality than the standard pipeline or wants to bypass Gemini API quota for the translate step.
---

# Super Saiyan Translate

Two-phase workflow that splits Whisper transcription and Edge-TTS dubbing (machine, fast, cheap) from the translation step (human-in-the-loop via the chat LLM — much higher quality for ad-hook copy).

Use this when:
- The default automated pipeline's Gemini Flash translation lacks emotional punch / hook fidelity.
- Daily Gemini quota is exhausted but the user still wants to dub videos today.
- The user wants to review translations before committing them to TTS.

Do NOT use when:
- Just a 1-off video — the standard pipeline is faster end-to-end.
- The user wants a fully automated batch — this requires a paste step.

## Two phases

### Phase 1 — extract
```bash
PYTHONIOENCODING=utf-8 "<video-translator>/.venv/Scripts/python.exe" -u extract.py <root> [--plan <csv>] [--whisper small|medium]
```
- Walks the source folder (or follows a translate-plan CSV if `--plan` is given).
- Whisper-transcribes every non-English video at full length.
- Writes one JSONL line per video to `<root>/_super_saiyan/translations.jsonl`:
  ```json
  {
    "file": "VI_120503.mp4",
    "source_path": "D:/.../1205/Tiếng Việt/VI_120503.mp4",
    "language_folder": "Tiếng Việt",
    "source_lang": "vi",
    "angle": "Couple Aesthetic Reveal",
    "out_path": "D:/.../1205/_translated_en/Couple_Aesthetic_Reveal/VI_120503_EN.mp4",
    "video_duration": 24.27,
    "segments": [
      {"id": 0, "start": 0.0, "end": 3.5, "text": "🔥 Nâng tầm nhan sắc...", "translation_en": ""}
    ]
  }
  ```
- The user pastes this file into the chat. The LLM fills in `translation_en` for every segment and returns the same JSONL.
- The user saves the LLM's response back to `<root>/_super_saiyan/translations.jsonl` (overwriting).

### Phase 2 — apply
```bash
PYTHONIOENCODING=utf-8 "<video-translator>/.venv/Scripts/python.exe" -u apply.py <root> [--workers 2] [--voice <edge-voice>]
```
- Reads `<root>/_super_saiyan/translations.jsonl`.
- For each entry where every segment has a non-empty `translation_en`:
  - Extracts HQ audio from the source video.
  - Runs Demucs (cached) to separate the original BGM from voice.
  - Calls `pipeline.dub.build_dubbed_audio` with the pre-translated segments (skipping the project's internal translate step entirely).
  - Calls `pipeline.dub.dub_video` to merge dubbed audio over the original video.
  - Writes to `out_path` (default `<root>/_translated_en/<angle_slug>/<stem>_EN.mp4`).
- Entries with any missing `translation_en` are skipped with `SKIP-INCOMPLETE` log.
- **Voice Audibility (auto):** `build_dubbed_audio`'s mixer measures both tracks and auto-cuts
  the BGM when the projected voice margin is under 8 dB (`VOICEMIX` log) — the dub can't be
  drowned by a hot source BGM. Audit output before shipping:
  `python "<skills-dir>/meta-ads-prepare/qa_voice_mix.py" "<root>/_translated_en" --whisper --expect-voice`.

## Translation guidelines (for the LLM in chat)

When asked to fill `translation_en`, follow these rules — they preserve ad-creative intent:

1. **Translate the hook, not the words.** "🔥 Nâng tầm nhan sắc với kiểu tóc phù hợp nhất" → "🔥 Find the hairstyle that levels up your look" (not literal "elevate your beauty").
2. **Match register and length.** TTS will speed-adjust to fit segment duration. A wordy literal translation gets compressed unintelligibly. Stay close to original syllable count.
3. **Keep emojis** — they survive TTS as silence which is fine; their presence helps reading.
4. **Preserve product names** — "ArtPic", "Artorys", "CapCut" stay as-is.
5. **CTA in last 1–2 segments** — translate to native English ad CTA cadence: "Try it free", "Download now", "Tap to install", not stiff "👉 Try Free Now".
6. **Don't fix typos / fillers** — if the source has "Ờm…" leave a brief English equivalent ("Uh…") rather than dropping it, since timing was tuned for it.
7. **Return the same JSONL structure** — only fill `translation_en`. Do not modify `start`/`end`/`text`/any other field.

## Hard requirements
1. **UTF-8 stdout** + `PYTHONIOENCODING=utf-8` — source transcripts contain CJK / Arabic / Devanagari / Thai.
2. **Idempotent rerun**: existing `translations.jsonl` is merged with new extractions by `file` key — don't clobber user-supplied translations.
3. **Whisper cache** at `<root>/_super_saiyan/_whisper_cache/{stem}.{model}.{lang}.transcript.json` — reuse across reruns.
4. **Demucs cache** lives in the Video Translator project's `_demucs_cache/` — both phases share it (apply phase reuses extract phase's separation work).
5. **Live progress** — one log line per video per stage. Don't run silent.
6. **Skip filled-segments-only entries on apply** — if `translation_en` is empty/whitespace for any segment, log `SKIP-INCOMPLETE` and move on.

## What NOT to do
- Don't auto-translate as a fallback in the apply step. If translations are missing, fail loudly so the user notices.
- Don't re-run Whisper on apply — the segments come from the JSONL, not a fresh transcribe.
- Don't generate SRT files — the JSONL is the source of truth. SRTs go to the dubbed-audio builder internally.
- Don't strip emojis or Markdown — they may help the LLM understand context and don't hurt TTS.
- Don't bake the translate-plan filter into apply — it should dub every entry in the JSONL with complete translations, full stop.

## Companion data
- Uses the same source folder layout as `classify-videos-by-language` and `rename-videos-by-language-date`.
- Reads `translate_plan.csv` (produced by ad-hoc analysis) when `--plan` is passed — otherwise walks every non-EN video.
- Output overwrites the same `_translated_en/<angle>/<file>_EN.mp4` paths the standard pipeline writes to.
