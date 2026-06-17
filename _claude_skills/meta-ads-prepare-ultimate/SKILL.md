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
run.py                            # orchestrator with 10 subcommands
  manifest.py                     # Whisper-once scan + manifest IO + voice gate
  langmaps.py                     # ISO↔folder↔CODE maps (shared)
  countries.py                    # T1+T2 target-country tiers
  bgm_suggest.py                  # trend-aware BGM advisor (location×language×content)
  retrim_endcards.py              # strip outro/competitor end-cards by graphic-card (dom3) detection
  voiceover.py                    # AI voiceover (Edge-TTS) for BGM-only clips -> localized VOICED ads
  signature.py                    # hidden mp4 tag = "already processed" (skip on re-runs)
app.json                          # PER-APP config (app_name, cta, usp, voices) — app-agnostic
vo_bank_<vertical>.json           # per-vertical VO scripts {lang:{angle:{short,long}}}, {app} placeholder
```

Per-video manifest entry, and **which step fills each field**:

| field | filled by | meaning |
|---|---|---|
| `duration, whisper_lang, has_voice, transcript, segments` | `scan` (Whisper ×1) | voice gate = avg_logprob > -0.5, speech ≥ max(1.5s, 5%) |
| `vertical` | **Opus** | `hair` / `home` / `skip` — which app/vertical this video belongs to (the source folder mixes verticals) |
| `language, language_folder, lang_code` | **Opus** | VOICED → spoken language (read transcript, NOT folder). BGM-only → `language_folder="_music"`, `lang_code="MU"` |
| `bgm_only, market_hint` | **Opus** | `has_voice=False` → `bgm_only=true` + `market_hint` = the txt/on-screen market (reference only; the clip itself is language-agnostic) |
| `angle, hook, bgm_cluster` | **Opus** | creative angle + which BGM mood cluster fits |
| `copy` | **Opus** | `{iso: {headlines[], primary_texts[]}}` — localized copy (voiced: spoken lang; BGM-only: per-market at campaign time) |
| `outro_variant` | **Opus** | `man` / `woman` for hair talking-heads (from the `framegrab` frame) → drives outro routing |
| `segments[].translations` | **Opus** | `{iso: text}` — one translation per target language, for `has_voice` videos |
| `renamed, organized_path` | `organize` | VOICED → `<lang>/CODE_DDMMNN.mp4`; BGM-only → `_music/MU_DDMMNN.mp4` |
| `dubbed_outputs` | `dub` | `{iso: path}` — one dubbed mp4 per target language, in `<src>/<lang_folder>/` (voice videos only) |
| `outputs` | `brandpass` | `{iso: path}` for voice (one per language, in `<lang>/`) or `{"_": path}` for BGM-only (in `_music/`) |

## The 6 steps

Run via the project Python. `<PY>` = `D:/Dev/Tools/Video Translator/.venv/Scripts/python.exe`
(office PC) **OR** `C:/Users/Thang Dep Dai/AppData/Local/Programs/Python/Python313/python.exe`
(home PC — no `.venv`; py313 already has faster-whisper/torch/demucs/edge-tts/cv2).
`<SK>` = `C:/Users/Thang Dep Dai/.claude/skills/meta-ads-prepare-ultimate`.

### Step 0 — ASK the user upfront (before touching anything)
Always collect these first; they drive the whole run:
1. **Source folder** (flat `*.mp4` or one level of subfolders).
2. **Target locations / countries** the ads will run in → drives BGM mood + copy localization + the country list. Default to the T1+T2 core (see `countries.py`) if unspecified.
3. **Translate to which language?** (or keep original / English only). Only `has_voice` videos get translated.
4. **Brand assets**: logo PNG (watermark + outro), outro title + subtitle, optional outro video, optional royalty-free BGM pool.

### Step 1 — `scan` (Whisper runs ONCE here, never again)
```bash
PYTHONIOENCODING=utf-8 "<PY>" -u "<SK>/run.py" scan --src "<folder>" [--whisper small] [--skip-processed]
```
Builds `manifest.json`: per video → transcript (native lang), `has_voice` (the
brand_pass speech-gate), `whisper_lang`, segments, duration. Idempotent — re-runs
only transcribe NEW videos and preserve Opus-filled fields.

**`--skip-processed`**: ignore any mp4 that already carries a pipeline signature
(a hidden mp4 `comment` tag stamped by a prior run — see *Processed-file
signature* below). Use it when a folder is partly/fully finished already so the
scan never re-Whispers / re-processes deliverables that are done. Files without a
tag are scanned as usual.

### Step 2 — Opus fills the manifest (vertical + language + copy + translations + BGM)

> ⚠️ **CARDINAL RULE — NEVER trust the source's pre-sorted split.** The source language
> folders (`Deutsch/`, `English/`, `Unknown/`, …) and any pre-existing language/vertical
> labels were sorted by an upstream tool that makes **REAL mistakes**: clips land in the
> wrong language folder, `Unknown/` hides perfectly identifiable languages, and even a
> "single-app" scrape can contain a different app. Treat the folder split as an
> **UNRELIABLE HINT, never ground truth.** In this step, RE-READ every video's
> `transcript` top-to-bottom (and a `framegrab` frame when the text is thin/ambiguous)
> and re-derive `language` + `vertical` from the **actual content**, ignoring which folder
> it came from. The `.txt` PRIMARY TEXT sidecar (if present) is corroborating evidence
> only — it too can be mislabeled, so cross-check, don't blindly adopt it.

Read `manifest.json`. For **every** video set:
- `vertical`: `hair` / `home` / `skip` (or the app's own vertical name, e.g. `plant`) — classify which app/vertical the video belongs to **from the content, NOT the folder** (the source folder mixes verticals; `skip` = unrelated app / unusable).
- **VOICED vs BGM-only routing — split by `has_voice` (the most important call):**
  - `has_voice=True` → **language-SPECIFIC** (the voiceover IS in a language). Set `language` (ISO from the `transcript` CONTENT — NOT the folder, NOT blindly `whisper_lang`, see Cardinal Rule), `language_folder` (native script, `langmaps.ISO_TO_FOLDER`), `lang_code`. → organizes into `<language_folder>/CODE_DDMMNN.mp4`.
  - `has_voice=False` → **language-AGNOSTIC** (no voiceover → the SAME clip runs in any market by just swapping the BGM). Set `language_folder="_music"`, `lang_code="MU"`, `bgm_only=true`, and `market_hint=<the txt/on-screen market, reference only>`. Do **NOT** assign a real language. → organizes into `_music/MU_DDMMNN.mp4`. ⛔ **NEVER dump voiceless videos into a language folder (esp. defaulting to English) — that's the old bug that buried reusable BGM clips and made country-scaling impossible** (you'd have to re-read every file to find them again).
- `angle` (canonical creative angle), `hook` (the one-line hook).
- `copy`: `{iso: {headlines:[...], primary_texts:[...]}}` — for **VOICED** videos, localized copy in the spoken language (+ any dub targets). For **BGM-only** videos copy is OPTIONAL here — it's generated per target market at campaign-build time (the clip is reusable), so leave it light. Headlines ≤40 chars (Meta limit); front-load the hook. Localize, don't literal-translate.
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

### Step 2c — `bgm-suggest` (trend-aware BGM advisor: location × language × content)
Run AFTER Step 2 is filled (needs `language` + `angle` + `has_voice`), and BEFORE the
user downloads BGM / before `brandpass --bgm-pool`. Tells the user exactly which
trending royalty-free tracks to drop into each `--bgm-pool` cluster folder.
```bash
PYTHONIOENCODING=utf-8 "<PY>" "<SK>/run.py" bgm-suggest --src "<folder>" \
    [--countries "US,FR,BR,SA"] [--write]
```
Reasons over the THREE axes the brief asks for, tuned to the **2026 short-form-ad meta**
and the app's audience (plant / home-wellness IAA → cozy/calm/aesthetic; it deliberately
steers AWAY from aggressive EDM / trap / dramatic orchestral):

1. **Location** → regional music trend. Each video's region comes from its OWN language's
   native market (anglo / west-eu / south-eu / nordics / east-eu / latam / mena / east-asia /
   sea). `--countries` annotates campaign scope in the shopping list (per-video region still
   follows language — correct for the keep-original-language flow).
2. **Language** → the default region + (with `has_voice`) whether the track must be a
   low-energy **instrumental bed** (ducked under VO, no vocal samples) vs a **music-only hero**
   (trendier/hookier OK).
3. **Content** → the creative `angle` maps to an energy bucket (calm / uplift / tension /
   modern) → one of the four pool clusters (`C_lofi_chill` / `B_uplifting` / `A_calm_nature` /
   `D_corporate`) + a BPM range.

Writes into `<src>/_ultimate/`:
- `bgm_suggestions.csv` — per video: file × language × angle × region × cluster × mood × bpm ×
  Pixabay queries × trend note.
- `bgm_shopping_list.md` — the deduped **download guide**: per cluster folder, grouped by region,
  with ready-to-click Pixabay search links (free / no-attribution / commercial OK).

`--write` also stamps the refined `bgm_cluster` back onto each manifest entry (so `brandpass
--bgm-pool --bgm-mode by-mood` routes each video to the matching cluster). Standalone one-shot:
`"<PY>" "<SK>/bgm_suggest.py" one --language fr --angle care-hack --countries FR [--voice]`.

### Step 3 — `organize` (split voiced-by-language vs BGM-only, move + rename)
```bash
PYTHONIOENCODING=utf-8 "<PY>" -u "<SK>/run.py" organize --src "<folder>" [--dry-run]
```
Moves each video into `<folder>/<language_folder>/` and renames to `<CODE>_DDMMNN.mp4`
(date from the filename's `_YYYYMMDDT...` stamp, else mtime; global sequence per date
bucket). Because Opus set `language_folder` per the voiced/BGM split in Step 2, this
automatically lands **VOICED** videos in their language folder (`English/EN_040601.mp4`)
and **BGM-only** videos in the language-agnostic group (`_music/MU_040603.mp4`) — no
extra flags. Always `--dry-run` first. Carries `has_voice` forward.

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

### Step 4b — `voiceover` (OPTIONAL — AI voiceover for BGM-only clips → localized VOICED ads)
Run AFTER brandpass (it adds VO to the finished BGM_UNIVERSAL clips). Turns the
language-AGNOSTIC B-roll into engaging **voiced** ads per language — a voiceover pitch
converts far better for IAA than music alone. The BGM-only masters are KEPT (reusable);
this just derives voiced versions.
```bash
PYTHONIOENCODING=utf-8 "<PY>" -u "<SK>/run.py" voiceover --src "<folder>" --dst "<dst>" \
    --app-config "<app.json>" --vo-bank "<vo_bank_plant.json>" \
    --target-langs en,es,fr,pt,de,ar,hi,zh,ru,id [--vertical plant] [--limit N] [--concurrency N]
```
Per BGM-only clip × target language:
1. **Script** = `vo_bank[lang][angle][short|long]` (`short` for clips <22s, `long` for ≥22s),
   with `{app}` / `{cta}` filled from `app.json`. **Opus authors `vo_bank`** (app-pitch, B-roll
   style — sells the app, doesn't narrate the specific visual, so one script fits every clip of
   that angle). Headlines of a script ≈ hook → USP → CTA, sized to the duration tier.
2. **Edge-TTS** with the **NATIVE voice per language**, from `app.json` → `vo.voices[lang]` (a LIST
   per language, rotated within the language for variety — e.g. `ar`→Saudi/Egyptian, `hi`→Indian,
   `bn`→Bangla, `es`→ES/MX). EN uses the 4 expressive multilingual voices (Ava/Emma/Andrew/Brian);
   any unmapped lang falls back to those 4. **Native voices avoid the American accent** the
   multilingual voices give to AR/HI/BN. Each call **retries + rotates to the next voice** on a
   transient Edge "no audio" blip so a flaky call never leaves a gap. Rate from `vo.rate` (default +6%).
3. **Fit**: if the TTS is longer than the clip, `atempo` up to 1.18×; the `vo_bank` short/long tiers
   keep this minimal.
4. **Mix — measured voice-first**: VO normalized to −16 LUFS, clip BGM placed 12 dB under the VO
   (both loudness-measured via ffmpeg loudnorm; the old 0.30/1.7 blind ratios remain only as the
   fallback when measurement fails), `amix normalize=0` + limiter, VO delayed 0.3s.
   No Demucs (the clip has no existing voice). Video is stream-copied (fast).
   **Parallel + polite:** jobs run at bounded concurrency (`--concurrency`, default ≈ half the CPU
   cores) with each ffmpeg at `-threads 1` and **below-normal priority**, so a full render (1500+
   clips) finishes in ~1h without starving foreground apps. Re-running is idempotent (same names).
→ writes `<dst>/VOICED_<Language>/<angle>/<LANG>-VO_DDMMNN.mp4` (the `-VO_` prefix distinguishes
generated voiceover ads from natively-voiced `<LANG>_DDMMNN`). Records `vo_outputs{lang:path}`.
**App-agnostic:** swap `app.json` (any app) + the matching `vo_bank_<vertical>.json` (plant/hair/…) —
same engine. Voice quality ceiling: Edge multilingual is good + free; ElevenLabs is the paid
near-human upgrade (32 langs) for winning angles.

### Step 5 — `brandpass` (per-vertical, resize 9:16 + fingerprint + watermark/outro + BGM swap)
Run **once per vertical** (different `--dst` + brand assets + outro per app):
```bash
PYTHONIOENCODING=utf-8 "<PY>" -u "<SK>/run.py" brandpass --src "<folder>" --dst "<hair_out>" \
    --vertical hair --target-langs it,fr,pt \
    --watermark "<logo.png>" --outro-man "<man.mp4>" --outro-woman "<woman.mp4>" \
    [--trim-endcard] [--bgm-pool "<pool>"] [--workers 4] [--seed-base 0]
```
Only processes videos whose `vertical` matches `--vertical` (`skip` videos are always
excluded). **Output is written directly as a Meta CAMPAIGN TREE** (folder = campaign,
angle subfolder = ad set, .mp4 = ads):
- **VOICED** videos → `<dst>/VOICED_<Language>/<angle>/<LANG_CODE>_DDMMNN.mp4` (one per
  dubbed language; a per-language campaign that targets that language's countries).
- **BGM-only** videos → `<dst>/BGM_UNIVERSAL/<angle>/MU_DDMMNN.mp4` (one universal
  campaign, language-agnostic — runs in every market, scale by swapping BGM).

`<Language>` in the CAMPAIGN tree = the recognizable **English** name (`langmaps.iso_to_english`,
e.g. `VOICED_Hindi`/`VOICED_Arabic`/`VOICED_Bengali`) — NOT the native-script autonym. (The
SOURCE working tree at `--src` still uses autonym `<language_folder>/`, line ~92; only the deliverable
campaign folders are English, since autonyms like हिन्दी/العربية are hard to read for upload. The
`voiceover` step writes the same English `VOICED_<Language>/` folders, and `LANG_COUNTRIES` in the
README map is keyed by these English names.)

`brand_pass_video` **resizes/crops to exactly
1080×1920 9:16**, strips side-blur, color-LUT jitter, watermark, outro, freeze-to-EOF
end-card trim, fake metadata — **unique fingerprint per file** (Andromeda dedup
evasion). Outro routing: `--outro-man`/`--outro-woman` selected by the video's
`outro_variant`; `--outro-video` is the generic fallback. Results recorded in
`outputs` (`{iso: path}` voice, `{"_": path}` music).

Audio routing is driven by the manifest, NOT re-detected:
- `has_voice=True` → `keep_original_voice=True` (keeps the dubbed/original talking-head voice; no robot re-dub), BGM swapped from the pool cluster.
- `has_voice=False` → music-only, BGM swapped (or source BGM passthrough if no pool).

**Critical efficiency:** the manifest `transcript` is passed into `brand_pass_video(transcript=...)`, so **brand-pass does NOT run Whisper again** (brand_pass.py skips its internal transcription when `transcript` is provided).

**BGM smart start + end-card v2 (automatic):** every `--bgm-pool` replacement opens on the track's best sustained section instead of a quiet intro (`BGMSTART` log line), and `--trim-endcard` uses reverse frame-matching v2 (animated-card aware, multi-card, 12 fps refine) — details in meta-ads-prepare SKILL.md.

**Voice Audibility QA (automatic):** every voiced render uses the measured voice-first mix — voice normalized to −16 LUFS, BGM 11–14 dB below the voice, post-mix LUFS gate (`DegradedError` instead of shipping a drowned mix). Each file logs one `VOICEMIX … MEASURED` line — grep the run log to audit a batch. NEVER pass `bgm_volume=` (legacy blind mix, ungated). After rendering, audit the deliverables: `python "<skills-dir>/meta-ads-prepare/qa_voice_mix.py" "<dst>" --sample 10 --whisper --expect-voice` on the `VOICED_*` folders. Full spec: meta-ads-prepare SKILL.md → "Voice Audibility QA".

### Step 6 — `package` (per-vertical creative assets + countries + QA gate + license check)
Run **once per vertical**, matching the brandpass `--dst`:
```bash
"<PY>" "<SK>/run.py" package --src "<folder>" --dst "<hair_out>" \
    --vertical hair --target-langs it,fr,pt \
    [--bgm-pool "<pool>"] [--east-eu] [--extra-countries "Taiwan,Singapore"]
```
Writes into `<dst>`:
- `creative_assets.csv` — **VOICED videos only**, one row per (video, spoken language): file × language × vertical × angle × hook × localized headlines × localized primary_texts × bgm_cluster. These are language-specific. Group into ad sets by angle.
- `bgm_only_assets.csv` — **BGM-only videos** (the `_music/` group), one row each: file × angle × hook × bgm_cluster × `market_hint` × reuse-note. Language-AGNOSTIC — the same clip ships to every country; copy is written per market at campaign time.
- `countries.txt` — T1+T2 targeting list (add `--east-eu` / `--extra-countries`).
- `qa_report.csv` — per output (voiced + BGM-only): dimensions==1080×1920, has-audio, duration>1 → PASS/FAIL. Exits non-zero if any FAIL. For voice audibility (LUFS + whisper speech check) additionally run `qa_voice_mix.py --whisper --expect-voice` from the meta-ads-prepare skill on the `VOICED_*` folders.
- `00_CAMPAIGNS_README.txt` — **visual map of the campaign tree** (each campaign + its angle ad-sets + ad counts + which countries to target + Meta setup + the BGM-swap scaling note). This is the human-readable index — open it instead of squinting at CSVs.
- **BGM license check** — warns if any BGM-only video kept SOURCE BGM (Meta copyright-flag risk; swap to a royalty-free Pixabay pool).

> **Need to offload the GPU-heavy phases to a rented Vast.ai GPU** (local PC can't
> run, or you don't want Whisper/Demucs/render lagging it)? That's the
> **`vast-meta-ultimate`** skill — the local-extended variant that runs scan / dub /
> voiceover / brandpass on Vast, keeps translate $0 in-chat, and auto-destroys the
> instance when done. This skill (`meta-ads-prepare-ultimate`) stays **fully local**.

## Processed-file signature (recognize done work, skip on re-runs)

Every full run **stamps a hidden mp4 `comment` tag** into each output (and source)
so a later run can tell "this is already finished" and skip it — no double
watermark, no wasted Whisper hours, no re-encode (the tag is written by
**stream-copy + atomic temp-replace**, so a finished deliverable is byte-identical
video, it just gains a provenance atom). Lives in `signature.py`.

**Tag format** (pipe-delimited, in the container `comment` atom):
```
<APP>_PROCESSED|batch=<b>|brandpassed=yes|tool=meta-ads-ultimate|date=YYYY-MM-DD|<extra>
```
Read it back with: `ffprobe -v error -show_entries format_tags=comment -of default=nw=1:nk=1 <file>`.
Recognition is loose + backward-compatible: a file counts as processed if the
comment contains `PROCESSED` **or** `tool=meta-ads-ultimate` (so legacy
`DECOAI_PROCESSED|...` tags still match).

**Automatic** — `brandpass` stamps every output + source at the end of its run
(disable with `--no-sign`). **`scan --skip-processed`** then ignores any file that
already carries the tag, so re-running the pipeline on a partly-finished folder
only touches the new files.

**Manual `signature` subcommand** — for folders processed outside this skill
(e.g. already brand-passed elsewhere) or to audit a folder:
```bash
# check: report how many files are already tagged + show an example tag + list untagged
"<PY>" "<SK>/run.py" signature --src "<folder>"
# mark: stamp every mp4 under <folder> as processed
"<PY>" "<SK>/run.py" signature --src "<folder>" --mark [--app decoai] [--batch 2805] [--note "step=bgmswap"]
```
Use `--mark` when deliverables were finished by an earlier/external pass so future
`scan --skip-processed` runs recognize and skip them.

## Waste eliminated vs. the old multi-skill flow
- Whisper runs **once** (scan), not 3–4× (classify + detect_voice + translate-extract + brandpass each used to transcribe).
- Voice/BGM detected in the same scan pass; carried as `has_voice` → brandpass reads it instead of re-detecting.
- **Opus** does language ID + angle + copy + translation (no Gemini, no Whisper langid).
- Only `has_voice` videos are translated/dubbed.

## Scaling to a new country (why the voiced / BGM-only split matters)

The whole point of separating `_music/` from the language folders is cheap country
scaling. Two paths, by group:

- **BGM-only videos (`_music/`, the bulk)** — language-agnostic. To launch in a new
  country: **just swap the BGM** to that country's trending track and re-fingerprint —
  NO re-classify, NO re-dub, NO Whisper. Re-run brandpass on `_music/` with a new
  `--bgm-pool` (the new market's trend pool, see `bgm-suggest`):
  ```bash
  "<PY>" run.py brandpass --src "<folder>" --dst "<new_country_out>" \
      --vertical <v> --bgm-pool "<NEW_country_bgm_pool>" --watermark ... --outro-video ...
  ```
  Because they live in one folder you can grab the entire reusable set instantly — you
  never have to re-read files to find "which ones were music-only" (the old pain).
- **Voiced videos (`<language>/`)** — language-specific. A new country that speaks a
  NEW language needs a real dub: add the lang to Step 2 translations → `dub` → brandpass.
  A new country that speaks an existing language (e.g. Austria↔Germany) reuses the
  existing `Deutsch/` outputs as-is.

So a typical "add Brazil" = reuse all `_music/` (swap to BR-trending BGM) + reuse
`Português/` voiced outputs. "Add Japan" = reuse `_music/` (JP BGM) + dub voiced→ja.

## Notes & pitfalls
- **CPU Whisper** is the default (this machine's CUDA fails inference — see memory). `small` is the speed/accuracy sweet spot.
- `_*` folders under `--src` are skipped by the scanner, so `_ultimate/` outputs never get re-ingested.
- If brand-pass robot-voices a talking-head: confirm `has_voice=True` in the manifest (the scan gate may mis-flag a testimonial buried under loud BGM — set it manually).
- Headlines must be ≤40 chars (Meta truncates). Primary texts have more room but front-load the hook.
- TW/SG are excluded from the default country core (need address verification) — add with `--extra-countries`.
- **Outro / end-card gotcha:** `brand_pass_video` ALWAYS appends a generated outro card by default (a pink "Download Now" card) even with NO outro flags; and competitor end-cards are often animated, so the built-in `--trim-endcard` (freezedetect) misses them. **Fix:** after brandpass run **`"<PY>" retrim_endcards.py all "<dst>"`** — it strips BOTH (detects flat graphic cards by top-3 quantized-colour coverage, dom3 ≥ 0.55, bridging the competitor↔generated gap; max-trim guard 62% so graphic-promo-only ads aren't gutted). To then add the REAL outro: concat an outro mp4 (give it silent stereo audio, normalize both streams to 30fps). Root cause (outro default-on) is a deferred `brand_pass.py` fix (touches 337 tests).
- Dependencies: project Python (py313 here) + `cv2` + `Pillow` (retrim card detect), ffmpeg/ffprobe on PATH. Same as `meta-ads-prepare`.

## Relationship to the older skills
`meta-ads-prepare` (single brand-pass batch) still exists and is fine for a quick
re-brand of already-organized videos. Use **ultimate** for a full campaign run from
raw competitor downloads. The standalone `gen-outro` and `trim-endcard` skills
remain useful as preview/standalone tools.
