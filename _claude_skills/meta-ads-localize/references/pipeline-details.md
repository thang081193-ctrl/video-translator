# Pipeline details — exact commands, formats, gotchas

`ROOT` = the batch folder (e.g. `D:/Dev/App Details/Home Decor/Video/<batch>`).
`MMDD` = derive a 4-digit code from the batch (e.g. folder "home decor 2805" → `2805`).
Run everything with `PYTHONIOENCODING=utf-8 python -u`. Scripts read
`$VIDEO_TRANSLATOR_ROOT` (default `D:/Dev/Tools/Video Translator`) for the `pipeline` package.

## 1. Inventory
```
python scripts/inventory.py --root "<ROOT>"
```
Prints per-video WxH / aspect / duration + a class (`9:16 OK`, `WIDE`, `taller`), summary
counts, and a junk list. Aspect 0.5625 = 9:16. Note any source bigger than 1080×1920 (e.g.
1440×2560) — those get downscaled during extract; flag the 2.5K ones.

## 2. Classify (ANGLE × LANGUAGE)
```
python scripts/make_grids.py --root "<ROOT>"      # writes grids to $TEMP/<batch>_grids/
```
Then fan out ~6 subagents, each given ~20 grid paths + the angle taxonomy
(`references/angles-and-copy.md`). Each returns a JSON array
`[{"grid","angle","lang","desc"}]`. Rules for the subagents:
- ANGLE = content-format, pick one of the taxonomy labels.
- LANGUAGE from on-screen text ONLY (overlay/subtitle/CTA/watermark). EN, ZH, etc.; a tiny
  creator watermark is not content language → if no real text, `visual`.
- Each grid is a 2×2 of frames at 8/35/65/92 % (top-left = earliest).

Collect tags. Most batches are angle-homogeneous per source subfolder, so a rule-based
assignment + a couple of cross-folder exceptions is usually enough. Build a `MOVES` plan and
reorg into `<ANGLE>/<LANG>/<ANGLE>_<CODE>_<MMDD><NN>.mp4`. Write a reverse-map JSON.

## 3. Voice-gate
```
python scripts/voice_gate.py --root "<ROOT>" --model tiny
```
Writes `_vo_gate.json`: per video `{has_vo, vo_lang, speech_s, ...}`. Gate = segments with
`text` and `avg_logprob > -0.5`, total speech ≥ max(1.5 s, 5 % of sample). After this,
relabel the language folder: VO videos keep `EN` (or detected lang), music-only/no-audio →
`none`. A no-audio video errors in the gate (no wav) — treat as `none`.

## 4. Extract transcripts (dub phase 1)
```
python scripts/extract_transcripts.py --root "<ROOT>" --src-angle STORAGE_VO \
    --outro "<OUTRO_VIDEO>" --limit 12
```
For the first `--limit` videos in `<ROOT>/<src-angle>/EN/`:
- downscale → `_dub_cache/<stem>/main.mp4` (1080×1920, original audio, no outro)
- Demucs → `_dub_cache/<stem>/no_vocals.wav`
- Whisper → segments
- EN final = main + outro → overwrites `<src-angle>/EN/<file>` (ZA-ready)
- writes `_dub_cache/transcripts.json`
Idempotent: a stem with an existing `main.mp4` is reused (no double-outro). Moves the
beyond-limit EN sources to `<src-angle>/_unused_en/`. NO end-card trim (see SKILL §2.6).

## 5. Claude fills translations.json
Create `_dub_cache/translations.json`. Keys = stems; per stem, one array per target lang,
**positional (array index = segment id), length == segment count**:
```json
{
  "STORAGE_VO_EN_280501": {
    "fr": ["seg0 fr", "seg1 fr", ...],
    "tr": ["seg0 tr", "seg1 tr", ...]
  }
}
```
Validate counts before applying (apply_dub warns on mismatch). Translation guidelines:
1. **Translate the hook, not the words** — keep ad intent, idiom, emotional punch.
2. **Match length/syllable count** — TTS speed-fits to the segment's start/end; a wordy
   literal translation gets compressed into gibberish. Stay close to source length.
3. Keep numbers, brand names, emojis. Natural register for the target language.
4. Don't fix source typos/fillers — timing was tuned for them.

## 6. Apply dub (phase 2)
```
python scripts/apply_dub.py --root "<ROOT>" --langs fr,tr
```
Reads transcripts.json + translations.json, builds Edge-TTS over the cached `no_vocals.wav`
(`audio_mode="keep_original_bgm"`, `pre_separated_no_vocals_path=...`), muxes over
`main.mp4`, appends the outro, writes `<src-angle>/<LANG>/<ANGLE>_<LANG>_<MMDD><NN>.mp4`.
Skips a (stem,lang) whose translations are incomplete.

**Critical mixer gotcha:** call `build_dubbed_audio(... audio_mode="keep_original_bgm",
pre_separated_no_vocals_path=<no_vocals>)`. The DEFAULT `custom_bgm` mode loops the BGM with
`-stream_loop -1` + `amix=duration=longest` → infinite → ffmpeg hangs. (A fix for the
default mode is tracked separately; the scripts here already use keep_original_bgm.)

## 7. Build campaigns
Write `<ROOT>/campaigns.json` (Claude authors this per batch). **Max 12 videos/campaign** —
control which videos via `limit` (first N, default 12), `slice` [start,end] of the sorted
glob (to DISTRIBUTE a universal angle across countries), or explicit `videos`. `country` is
the tier-matched block (English-baked angles → English-emerging block, not one country).
Schema:
```json
[
  {"name":"C1_FR_StorageVO","glob":"STORAGE_VO/FR/*.mp4","lang":"Français",
   "country":"France","limit":12,"note":"...","primary":["..."],"headlines":["..."]},
  {"name":"C2_FR_Walkthrough","glob":"WALKTHROUGH_3D/none/*.mp4","lang":"Français",
   "country":"France","slice":[0,11],"primary":["..."],"headlines":["..."]},
  {"name":"C5_EN_StorageVO","glob":"STORAGE_VO/EN/*.mp4","lang":"English","limit":12,
   "country":"South Africa, Nigeria, Kenya, Ghana, Philippines, Pakistan, India",
   "primary":["..."],"headlines":["..."]}
]
```
A universal angle in N countries: give each country campaign a distinct `slice` (e.g.
[0,11]/[11,22]/[22,32]) so all videos are used, each campaign ≤12. Single-market angles
with >12 videos: `limit:12`, the rest stay in the source folder as a refresh pool.
Then (`--app` = `app_name` from `apps/<app>.json`):
```
python scripts/build_campaigns.py --root "<ROOT>" --config campaigns.json \
    --app "AI Home Design: DecoAI"
```
Hardlinks the glob'd videos into `_campaigns/<name>/` and writes `videos.txt`,
`primary_text.txt`, `headlines.txt`, `country.txt`, `_ASSETS.md`, plus a top-level
`README.md`. **Also splits into individual ads:** `ads.csv` per campaign (each video = one
ad row: `ad_name,video,primary_text,headline`, copy rotated through the pools) + a master
`all_ads.csv` across all campaigns (utf-8-sig BOM, Excel-friendly). Split by ANGLE × COUNTRY. Universal `none`/visual angles (e.g. WALKTHROUGH_3D)
can appear under every country; baked-EN-text angles (HELP/TIPS) only under EN markets.

## 8. Cleanup
Delete `_dub_cache/<stem>/` binaries (keep `_dub_cache/*.json`), `_junk/`, `$TEMP` grid
dirs, scratch reverse-map JSONs. Confirm with the user before deleting unused source videos.
