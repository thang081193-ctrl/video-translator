---
name: meta-ads-prepare-ultimate
description: End-to-end pipeline that turns a folder of source ad videos into upload-ready Meta/TikTok creatives in one pass — classify by language, detect voice vs music, translate (optional), resize to 1080×1920 9:16, change fingerprint for dedup evasion, swap BGM, write localized headlines + primary texts, and QA-gate the output. Built around ONE shared manifest so Whisper runs only once and every later step reuses it (no wasted transcription). The LLM-reasoning steps — language ID, ad-angle, ad copy, translation — are done by Opus (this chat), NOT Gemini. Use when the user asks to "Meta Ads Prepare Ultimate", "prepare these videos for Meta ads end to end", "run the ultimate ads pipeline", "chuẩn bị video chạy Meta ads từ đầu đến cuối", or wants the full classify→translate→fingerprint→copy workflow on a batch.
---

# Meta Ads Prepare — Ultimate

One orchestrator, one shared manifest, six steps. Takes a folder of raw source ad
videos and produces upload-ready Meta/TikTok Reels (1080×1920, unique fingerprint
each) **plus** the creative assets (headlines, primary texts, BGM, country list)
to launch the campaign.

This supersedes running `classify-videos-by-language` + `analyze-ad-angles` +
`super-saiyan-translate` + `meta-ads-prepare` + the rename skills separately. The
big win: **Whisper transcribes each video exactly once** (the `scan` step). Every
later step reads the manifest instead of re-transcribing. Voice/BGM, language,
angle, ad copy, translation, BGM cluster — all carried in one `manifest.json`.

> The LLM-reasoning steps (language detection, angle, ad copy, translation) are
> done by **Opus — the model in this chat** — by editing `manifest.json`. NOT
> Gemini. (User decision 2026-06-03.)

## Architecture

```
<src>/_ultimate/manifest.json     # the backbone — one entry per video
run.py                            # orchestrator with 6 subcommands
  manifest.py                     # Whisper-once scan + manifest IO + voice gate
  langmaps.py                     # ISO↔folder↔CODE maps (shared)
  countries.py                    # T1+T2 target-country tiers
```

Per-video manifest entry, and **which step fills each field**:

| field | filled by | meaning |
|---|---|---|
| `duration, whisper_lang, has_voice, transcript, segments` | `scan` (Whisper ×1) | voice gate = avg_logprob > -0.5, speech ≥ max(1.5s, 5%) |
| `vertical` | **Opus** | `hair` / `home` / `skip` — which app/vertical this video belongs to (the source folder mixes verticals) |
| `language, language_folder, lang_code` | **Opus** | confirmed source language (Opus reads transcript, NOT the folder label) |
| `angle, hook, bgm_cluster` | **Opus** | creative angle + which BGM mood cluster fits |
| `copy` | **Opus** | `{iso: {headlines[], primary_texts[]}}` — localized copy **per target language** |
| `outro_variant` | **Opus** | `man` / `woman` for hair talking-heads (from the `framegrab` frame) → drives outro routing |
| `segments[].translations` | **Opus** | `{iso: text}` — one translation per target language, for `has_voice` videos |
| `renamed, organized_path` | `organize` | `CODE_DDMMNN.mp4` + source-lang location |
| `dubbed_outputs` | `dub` | `{iso: path}` — one dubbed mp4 per target language, in `<src>/<lang_folder>/` (voice videos only) |
| `outputs` | `brandpass` | `{iso: path}` for voice (one per language) or `{"_": path}` for music-only (language-agnostic) |

## The 6 steps

Run via the project venv. `<PY>` = `D:/Dev/Tools/Video Translator/.venv/Scripts/python.exe`,
`<SK>` = `C:/Users/Thang/.claude/skills/meta-ads-prepare-ultimate`.

### Step 0 — ASK the user upfront (before touching anything)
Always collect these first; they drive the whole run:
1. **Source folder** (flat `*.mp4` or one level of subfolders).
2. **Target locations / countries** the ads will run in → drives BGM mood + copy localization + the country list. Default to the T1+T2 core (see `countries.py`) if unspecified.
3. **Translate to which language?** (or keep original / English only). Only `has_voice` videos get translated.
4. **Brand assets**: logo PNG (watermark + outro), outro title + subtitle, optional outro video, optional royalty-free BGM pool.

### Step 1 — `scan` (Whisper runs ONCE here, never again)
```bash
PYTHONIOENCODING=utf-8 "<PY>" -u "<SK>/run.py" scan --src "<folder>" [--whisper small]
```
Builds `manifest.json`: per video → transcript (native lang), `has_voice` (the
brand_pass speech-gate), `whisper_lang`, segments, duration. Idempotent — re-runs
only transcribe NEW videos and preserve Opus-filled fields.

### Step 2 — Opus fills the manifest (vertical + language + copy + translations + BGM)
Read `manifest.json`. For **every** video set:
- `vertical`: `hair` / `home` / `skip` — classify which app/vertical the video belongs to (the source folder mixes verticals; `skip` = neither / unusable).
- `language` (ISO, e.g. `vi`), `language_folder` (native script — see `langmaps.ISO_TO_FOLDER`, e.g. `Tiếng Việt`), `lang_code` (e.g. `VI`). **Determine language from the `transcript` content, NOT from the source folder name and NOT blindly from `whisper_lang`** — the folder/auto labels miss a lot. Trust your reading of the text.
- `angle` (canonical creative angle), `hook` (the one-line hook).
- `copy`: `{iso: {headlines:[...], primary_texts:[...]}}` — **localized copy in every target language** of this video's vertical (Step 0). Headlines ≤40 chars (Meta limit); front-load the hook in primary_texts. Localize, don't literal-translate.
- `bgm_cluster` (which `--bgm-pool` subfolder mood fits this video + its market, e.g. `B_indiepop`).

For `has_voice` videos: fill `segments[].translations` = `{iso: text}` with **one translation per target language** (super-saiyan quality — preserve ad-hook punch, don't machine-translate). A target lang equal to the source language is skipped automatically (the original already carries that voice).

For hair talking-head videos, run `framegrab` first (below), look at the frames, and set `outro_variant` = `man` / `woman` so brandpass picks the matching outro.

Save the file. Then check progress:
```bash
"<PY>" "<SK>/run.py" status --src "<folder>"
```

### Step 2b — `framegrab` (man/woman outro tagging, hair videos)
```bash
PYTHONIOENCODING=utf-8 "<PY>" -u "<SK>/run.py" framegrab --src "<folder>" --vertical hair --voice-only
```
Extracts a frame at ~50% of each (voice) video into `<src>/_ultimate/_frames/<id>.jpg`. Opus then views the frames and sets each video's `outro_variant` = `man` / `woman`. Brandpass routes `--outro-man` / `--outro-woman` accordingly.

### Step 3 — `organize` (move to language folders + rename)
```bash
PYTHONIOENCODING=utf-8 "<PY>" -u "<SK>/run.py" organize --src "<folder>" [--dry-run]
```
Moves each video into `<folder>/<language_folder>/` and renames to
`CODE_DDMMNN.mp4` (date from the original filename's `_YYYYMMDDT...` stamp, else
mtime; sequence per language+date bucket). Always `--dry-run` first to eyeball the
mapping. Carries `has_voice` forward.

### Step 4 — `dub` (voice videos only, multi-language, uses Opus translations)
```bash
PYTHONIOENCODING=utf-8 "<PY>" -u "<SK>/run.py" dub --src "<folder>" \
    --target-langs it,fr,pt [--voice <edge-voice>] [--workers 2]
```
Localizes every `has_voice` video into **each** target language. For a target lang
that equals the video's source language no dub is produced — the original already
carries that voice and is recorded in `dubbed_outputs[lang]`. For every other lang
(translations all filled) Demucs isolates the BGM and Edge-TTS reads the translation
over it, writing `<src>/<lang_folder>/<LANG_CODE>_DDMMNN.mp4` (e.g. an EN source →
`Italiano/IT_040601.mp4`, `Français/FR_040601.mp4`, `Português/PT_040601.mp4`). The
DDMMNN suffix is reused so all language variants pair up by sequence. Pass the
**union** of both verticals' target langs in one run — a video only gets dubbed into
the langs it actually has translations for, so hair (it/fr/pt) and home (fr/id/pt)
both resolve from one `--target-langs it,fr,pt,id` call. BGM-only videos are skipped
(no dub needed). Skip this step entirely if not translating.

### Step 5 — `brandpass` (per-vertical, resize 9:16 + fingerprint + watermark/outro + BGM swap)
Run **once per vertical** (different `--dst` + brand assets + outro per app):
```bash
PYTHONIOENCODING=utf-8 "<PY>" -u "<SK>/run.py" brandpass --src "<folder>" --dst "<hair_out>" \
    --vertical hair --target-langs it,fr,pt \
    --watermark "<logo.png>" --outro-man "<man.mp4>" --outro-woman "<woman.mp4>" \
    [--trim-endcard] [--bgm-pool "<pool>"] [--workers 4] [--seed-base 0]
```
Only processes videos whose `vertical` matches `--vertical` (`skip` videos are always
excluded). Each **voice** video produces **one output per dubbed language**
(`<dst>/<lang_folder>/<LANG_CODE>_DDMMNN.mp4`); each **music-only** video is
language-agnostic and produces **one** output in its source-lang folder (it runs in
all of that vertical's markets). `brand_pass_video` **resizes/crops to exactly
1080×1920 9:16**, strips side-blur, color-LUT jitter, watermark, outro, freeze-to-EOF
end-card trim, fake metadata — **unique fingerprint per file** (Andromeda dedup
evasion). Outro routing: `--outro-man`/`--outro-woman` selected by the video's
`outro_variant`; `--outro-video` is the generic fallback. Results recorded in
`outputs` (`{iso: path}` voice, `{"_": path}` music).

Audio routing is driven by the manifest, NOT re-detected:
- `has_voice=True` → `keep_original_voice=True` (keeps the dubbed/original talking-head voice; no robot re-dub), BGM swapped from the pool cluster.
- `has_voice=False` → music-only, BGM swapped (or source BGM passthrough if no pool).

**Critical efficiency:** the manifest `transcript` is passed into `brand_pass_video(transcript=...)`, so **brand-pass does NOT run Whisper again** (brand_pass.py skips its internal transcription when `transcript` is provided).

### Step 6 — `package` (per-vertical creative assets + countries + QA gate + license check)
Run **once per vertical**, matching the brandpass `--dst`:
```bash
"<PY>" "<SK>/run.py" package --src "<folder>" --dst "<hair_out>" \
    --vertical hair --target-langs it,fr,pt \
    [--bgm-pool "<pool>"] [--east-eu] [--extra-countries "Taiwan,Singapore"]
```
Writes into `<dst>`:
- `creative_assets.csv` — **one row per (video, market language)**: file × language × vertical × angle × hook × localized headlines × localized primary_texts × bgm_cluster. Voice videos get one row per dubbed lang; music-only videos get one row per target market lang (same file). Group into ad sets by angle.
- `countries.txt` — T1+T2 targeting list (add `--east-eu` / `--extra-countries`).
- `qa_report.csv` — per output (every language variant): dimensions==1080×1920, has-audio, duration>1 → PASS/FAIL. Exits non-zero if any FAIL.
- **BGM license check** — warns if any music-only video kept SOURCE BGM (Meta copyright-flag risk; swap to a royalty-free Pixabay pool).

## Waste eliminated vs. the old multi-skill flow
- Whisper runs **once** (scan), not 3–4× (classify + detect_voice + translate-extract + brandpass each used to transcribe).
- Voice/BGM detected in the same scan pass; carried as `has_voice` → brandpass reads it instead of re-detecting.
- **Opus** does language ID + angle + copy + translation (no Gemini, no Whisper langid).
- Only `has_voice` videos are translated/dubbed.

## Notes & pitfalls
- **CPU Whisper** is the default (this machine's CUDA fails inference — see memory). `small` is the speed/accuracy sweet spot.
- `_*` folders under `--src` are skipped by the scanner, so `_ultimate/` outputs never get re-ingested.
- If brand-pass robot-voices a talking-head: confirm `has_voice=True` in the manifest (the scan gate may mis-flag a testimonial buried under loud BGM — set it manually).
- Headlines must be ≤40 chars (Meta truncates). Primary texts have more room but front-load the hook.
- TW/SG are excluded from the default country core (need address verification) — add with `--extra-countries`.
- Dependencies: project venv + `cv2` (side-blur), ffmpeg/ffprobe on PATH. Same as `meta-ads-prepare`.

## Relationship to the older skills
`meta-ads-prepare` (single brand-pass batch) still exists and is fine for a quick
re-brand of already-organized videos. Use **ultimate** for a full campaign run from
raw competitor downloads. The standalone `gen-outro` and `trim-endcard` skills
remain useful as preview/standalone tools.
