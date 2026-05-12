---
name: analyze-ad-angles
description: Analyze a folder of ad videos and group them into reusable creative angles for Google/Meta/TikTok ads. For each video, extracts the spoken transcript and 3 keyframes, then asks Gemini 2.5 Flash to label the angle, hook line, persona, format, and best-fit platform. Consolidates per-video labels into 8–12 canonical angles maintained across daily runs. Outputs two CSVs (per-video + angle×language pivot). Use when the user asks to "group videos by angle", "analyze ad angles", "phân loại video theo angle", or wants a creative-brief taxonomy for campaign planning.
---

# Analyze Ad Angles

Daily-ops pipeline for turning a folder of competitor ad videos into a structured taxonomy of creative angles you can pitch into Google Ads, Meta Ads, and TikTok Ads campaigns.

## Inputs
- **root**: absolute path to the parent folder (the same root used by `classify-videos-by-language`). Subfolders are language buckets; mp4 files sit inside them. Optional sibling `.txt` is the ad-library metadata.

## Outputs (written into `<root>/`)
- `analysis.csv` — one row per video: `file, language, angle, confidence, hook_line, core_promise, format, persona, best_platform, notes`
- `angles_x_languages.csv` — pivot count matrix (rows = angle, cols = language).
- `<root>/_angle_cache/<sha256>.json` — per-video cache so daily reruns only process new files.

## Maintained taxonomy
- `~/.claude/skills/analyze-ad-angles/taxonomy.json` holds the canonical 8–12 angles with `{name, description, signal_keywords}`.
- **First run on an empty taxonomy**: discover angles from data (consolidate per-video labels via Gemini).
- **Subsequent runs**: existing taxonomy is loaded into the per-video prompt so labels stay consistent day-to-day. If ≥5 low-confidence videos share a new theme, suggest a new angle and append to the file (logged, not silent — the user sees it).

## Hard requirements
1. **UTF-8 stdout** and `python -u` — non-Latin filenames will crash a cp1252 stdout.
2. **Live progress** — one log line per video stage (transcribe → keyframes → gemini → cache-write). Cap noise: log only summary per stage, not raw API output.
3. **Cache aggressively** — key = sha256 of `(absolute_path, file_size, mtime_ns)`. A daily rerun must NOT re-call Gemini on already-analyzed videos.
4. **Key rotation** — use all `GEMINI_API_KEYS` in `<video-translator>/.env` round-robin. On 429/RESOURCE_EXHAUSTED, mark the key cool-down for 60s and retry on the next key. Free tier is ~10 RPM per key, so 6 keys ≈ 60 RPM.
5. **Concurrency** — process Gemini calls with `min(len(keys), 6)` parallel workers. Transcription is CPU-bound and serial.
6. **No `.txt` mutation** — this skill reads `.txt` for context but never writes to it.
7. **Idempotent re-runs** — if `analysis.csv` already exists, it's overwritten cleanly using the union of cached results + any newly analyzed videos.

## Two-pass clustering (first-run only)
First run (no usable taxonomy yet):
1. **Pass 1** — per video, ask Gemini with frames+transcript: return a free-form `angle_hypothesis` (3–5 words) plus structured fields.
2. **Consolidation** — single Gemini call: take ALL hypothesis strings, group into 8–12 canonical angles. Output JSON `{canonical_name: [hypothesis_strings_in_cluster]}`.
3. **Mapping pass** — for each video, look up its hypothesis in the consolidation map → canonical angle. Save to taxonomy.

Subsequent runs:
- Pass 1 prompt now includes the existing taxonomy ("Pick the best-fit angle from this list; only invent a new one if none fit"). Confidence drops below 0.6 → mark for review.

## Per-video Gemini prompt shape
The script sends:
- 3 keyframes as inline image parts (jpeg, ~640px wide).
- Transcript (first 60s, truncated to 1.5kB).
- Folder language (informational — for the LLM to know what language the transcript is in).
- A schema in the system prompt asking for strict JSON:
  ```json
  {
    "angle_hypothesis": "before/after hair transformation",
    "hook_line": "exact verbatim opening hook from transcript",
    "core_promise": "single sentence — what the ad promises",
    "format": "before-after | screen-demo | talking-head | montage | trend-remix | testimonial | other",
    "persona": "primary target persona, 2-4 words",
    "best_platform": "tiktok | meta | google | universal",
    "confidence": 0.0,
    "notes": "1 short sentence — distinct selling point or twist"
  }
  ```

## Implementation
```bash
PYTHONIOENCODING=utf-8 "<video-translator>/.venv/Scripts/python.exe" -u analyze.py "<root>"
```

Reuse the Video Translator venv (`google.genai`, `faster-whisper`, ffmpeg already installed).

The script:
1. Walks `<root>/<lang-folder>/*.mp4`.
2. For each video: load cache; if miss → transcribe → keyframes → Gemini call → write cache.
3. After all per-video done: if taxonomy is empty/stale, run consolidation pass to (re)build it.
4. Re-label every video with canonical angle (cheap text-only Gemini call, one batch).
5. Write `analysis.csv` and `angles_x_languages.csv`.
6. Print a one-screen summary: total videos, per-angle counts, low-confidence list (for manual review).

## What NOT to do
- Don't send full video bytes to Gemini — 3 keyframes is enough signal and costs ~5× less.
- Don't transcribe more than ~60s of audio for analysis — the hook lives in the first 15s anyway.
- Don't re-cluster the taxonomy from scratch every day. Stable angles week-over-week are the whole point.
- Don't fail the whole batch if one video errors — log `ERR <file>: <reason>` and continue. CSV row gets `angle=ERROR`.
- Don't ship without a `--dry-run` mode for the user to preview taxonomy changes before they go live.
- Don't hardcode the Gemini model name — read `GEMINI_MODEL` env (default `gemini-2.5-flash`).
