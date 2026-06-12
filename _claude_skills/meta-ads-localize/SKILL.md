---
name: meta-ads-localize
description: >-
  End-to-end pipeline that turns a folder of raw resource videos (scraped/downloaded
  ad creatives, e.g. home-decor room-makeover Reels) into upload-ready, LOCALIZED Meta
  Ads campaigns for an IAA app. Inventories the videos, classifies them by content-format
  ANGLE + LANGUAGE, voice-gates VO vs music-only, DUBS the narrated ones into target
  languages (the dub is translated by Claude itself — NEVER Gemini), keeps the original
  BGM, appends a brand outro, then organizes everything into per-country campaign folders
  with localized primary text + headlines. Use this whenever the user drops a folder of
  source videos and wants them localized + organized for Meta Ads (FR/TR/ZA/etc.), says
  things like "làm pack ads localize cho videos", "dub mấy video này sang FR/TR rồi chia
  campaign", "build localized Meta ads from this folder", "triển khai resource videos",
  or hands a resource-video folder + a target-country list. This is the DUB-LOCALIZATION +
  by-country-campaign variant of meta-ads-reels — prefer it over meta-ads-reels whenever
  multi-language dubbing of the videos is involved.
---

# Meta Ads Localize — resource videos → localized per-country campaigns

This skill captures a pipeline that has been run end-to-end and validated. It takes a
folder of raw ad-creative videos and produces dubbed, branded, campaign-organized output
ready to paste into Meta Ads Manager. It leans on the **Video Translator** project
(`pipeline/` modules for transcribe / Demucs / Edge-TTS) and bundled scripts under
`scripts/`.

The scripts locate the Video Translator repo via `$VIDEO_TRANSLATOR_ROOT`
(default `D:/Dev/Tools/Video Translator`). System Python on this machine already has
`faster_whisper`, `edge_tts`, `demucs`, `dotenv` installed (no venv).

---

## §1 — When to use / inputs

Trigger when the user gives a **folder of source videos** and wants them localized +
organized for Meta Ads. Required inputs (ASK only if missing):

| Input | Default |
|---|---|
| Source folder | (from user, e.g. `D:/Dev/App Details/Home Decor/Video/<batch>`) |
| Target languages / countries | ask; map country→lang (FR→France, TR→Türkiye, ZA→English/South Africa) |
| Videos per campaign | **10–12** (NOT 25 — Andromeda learns faster with a tight creative set) |
| App | load `apps/<app>.json` (brand assets + value prop + geo). **NOT DecoAI-only** — if the app has no config yet, capture it first (§7). |

A country chosen for an English-tolerant market (e.g. ZA/South Africa) needs **no dub** —
reuse the English originals. Only dub for languages where the VO must change (FR, TR, …).

---

## §2 — Pipeline (run in order)

Read `references/pipeline-details.md` for the exact commands and gotchas of each step.
The high-level flow and the *why*:

0. **App context (do this FIRST).** Identify which app these creatives are for and load
   `apps/<app>.json` (brand title/sub, logo, outro video, value prop, default geo). If the
   app isn't configured yet, ASK the user for those + the Play Store/App Store URL, then
   create `apps/<app>.json` from `apps/_TEMPLATE.json`. This matters because the **outro
   video** feeds the dub step and the **value prop** is what lets you write accurate ad
   copy — a generic copy bank won't sell a specific app. This skill runs for *any* app.

1. **Inventory** — `scripts/inventory.py --root <folder>`. ffprobe every mp4: resolution,
   aspect, duration; flag incomplete downloads (`.part`, `.temp.mp4`, orphan `.m4a`,
   `.DS_Store`). You need this to spot off-spec videos (e.g. 2.5K sources that bloat to
   60 MB and should be downscaled to 1080×1920) and to quarantine junk before processing.

2. **Classify by ANGLE × LANGUAGE** — generate 4-up frame grids
   (`scripts/make_grids.py --root <folder>`), then fan out parallel vision subagents to
   tag each grid `{angle, lang}`. ANGLE is **content-format** (HELP_REDESIGN, TIPS_DODONT,
   WALKTHROUGH_3D, STORAGE_VO, OTHER — see `references/angles-and-copy.md`).
   **LANGUAGE is VISION-ONLY**: read it from on-screen text (overlay captions, burned
   subtitles, CTA chips, watermark). NEVER infer language from audio — many of these ads
   play an English song under non-English / silent visuals, so audio lies. No on-screen
   text → `visual`.

3. **Voice-gate** — `scripts/voice_gate.py --root <folder>`. Whisper-transcribe a 45 s
   sample of each video; a segment counts as real speech only if `avg_logprob > -0.5`
   (a music/song hallucination sits at −0.7…−1.0; `no_speech_prob` is NOT reliable).
   Videos with real VO keep their spoken language; **music-only / no-audio videos go to a
   `none` language folder**. This is the only reliable way to separate "has a narration we
   can translate" from "the language is baked into overlay graphics we can't re-translate".

4. **Reorg** — move into `<ANGLE>/<LANG|none>/<ANGLE>_<CODE>_<MMDD><NN>.mp4`. Keep a
   reverse-map JSON so the move is undoable.

5. **DUB the VO videos** to each target language. **The translation is done by Claude
   (this model), NEVER by the project's Gemini-flash auto-translate** — flash quality is
   poor for ad copy, and Claude preserves hook/intent and matches syllable length so the
   TTS timing stays in sync. Three-phase super-saiyan flow:
   - `scripts/extract_transcripts.py --root <folder> --src-angle STORAGE_VO --limit 12`
     → Whisper transcripts + downscale source to 1080×1920 + cache Demucs `no_vocals.wav`
     + write `_dub_cache/transcripts.json` (with empty `fr`/`tr`…).
   - **Claude fills `_dub_cache/translations.json`** — positional arrays of translations
     per stem per lang (match segment count exactly). See `references/pipeline-details.md`
     for the format + translation guidelines.
   - `scripts/apply_dub.py --root <folder> --langs fr,tr` → Edge-TTS in the target voice
     over the original Demucs-separated BGM, mux, **append the brand outro video**, output
     `<ANGLE>/<LANG>/...`.
   - **Voice Audibility (auto + audit):** the dub mixer loudness-measures both tracks and
     auto-cuts the BGM whenever the projected voice margin drops under 8 dB
     (`cfg.dub.min_voice_margin_db`, log `VOICEMIX`) — a hot source BGM can no longer drown
     the dub. Before upload, audit the deliverables:
     `python "<skills-dir>/meta-ads-prepare/qa_voice_mix.py" "<folder>" --sample 10 --whisper --expect-voice`
     (0 whisper words on a dubbed file = voice drowned/missing → FAIL). Full spec:
     meta-ads-prepare SKILL.md → "Voice Audibility QA".

6. **DO NOT auto-trim end-cards.** The scene-change detector false-positives on
   continuous-VO walkthroughs (it flags a mid-video scene cut as an "end-card" and would
   chop 15–20 s of real content). Verify visually if a competitor CTA exists. For these
   home-decor STORAGE_VO videos there is **no** competitor outro — the VO runs to the end.
   Competitor App-Store CTA overlays (on HELP/TIPS-type creatives) are handled later at the
   brand-pass step, not here.

7. **Build campaigns + split into individual ads** by ANGLE × COUNTRY. Claude writes a
   `campaigns.json` config (per campaign: videos glob + localized primary[] + headlines[] +
   country + note), then
   `scripts/build_campaigns.py --root <folder> --config campaigns.json --app "<app_name>"`:
   - hardlinks the videos into `_campaigns/<name>/` and writes `videos.txt`,
     `primary_text.txt`, `headlines.txt`, `country.txt`, `_ASSETS.md` + top-level `README.md`;
   - **splits each campaign into individual ads** — `ads.csv` per campaign where **every
     video = one ad** paired with a rotated primary text + headline, plus a master
     `all_ads.csv` across all campaigns. This is the upload unit: one row = one Meta ad
     (video + primary + headline). (Or drop Dynamic Creative onto the pooled .txt files.)
   Copy must be in the campaign's target language, grounded in the app's value prop.
   `references/angles-and-copy.md` has the angle taxonomy + a DecoAI copy style reference.

8. **Cleanup** — delete intermediates (the per-video binaries in `_dub_cache/`,
   `_junk/`, temp grid dirs, scratch reverse-map JSONs). **Keep `translations.json`** so a
   re-dub never needs re-translation. Ask before deleting unused *source* videos (those are
   irreversible).

---

## §3 — Hard rules (the expensive lessons)

- **Translation = Claude, never Gemini flash.** Encoded in the dub flow above. See the
  user memory `translate_claude_only`.
- **Language detection = vision-only**, never audio (BGM ≠ language).
- **VO videos dub; music-only / overlay-text videos go to `none`.** You cannot re-translate
  text that's baked into the video graphics with this pipeline — only the audio VO.
- **Dubbed (FR/TR/…) videos must NOT go through standard brand-pass.** Its voice-gate would
  detect the new speech and re-dub it back to *English*, destroying the localization. If
  you need dedup/fingerprint variation on dubs, use a no-TTS brand-pass variant or just the
  visual/BGM jitter.
- **Per-country BGM swap uses cleared-for-ads music only** (Meta Sound Collection / Pixabay
  / Epidemic). Real "trending" tracks are copyrighted and Meta will mute/flag them. Doing
  this also fixes the copyrighted-song problem on the `none` videos.
- **Max 12 videos (= ads) per campaign — HARD CAP.** Andromeda learns faster with a tight
  creative set; you *rotate* creative, you don't run 30 at once. Respect it two ways:
  (a) a **universal** angle (WALKTHROUGH_3D) used in several countries → **distribute** its
  videos across the per-country campaigns (slice the sorted list, e.g. 11/11/10) so all get
  used while each stays ≤12; (b) a **single-market** angle with >12 videos (HELP/TIPS) →
  take the best 12 for the live campaign, leave the rest in the source folder as a **refresh
  pool** (rotate in when CTR drops / frequency > 3). `build_campaigns.py` enforces this via
  per-campaign `limit` (default 12), `slice [start,end]`, or explicit `videos`.
- **Target each angle at the right country TIER**, matched to the creative language AND the
  app's market tier. English-baked angles (HELP/TIPS) + English StorageVO go to the *English*
  market at the app's tier — for a low-eCPM IAA market like South Africa that's **SA + its
  English-main tier peers** (Nigeria, Kenya, Ghana, Philippines, Pakistan, India), NOT a
  single small country and NOT the high-eCPM T1 anglo block. Don't dump all English angles
  into one country. Country blocks: `references/angles-and-copy.md`.

---

## §4 — What this skill does NOT do (handoff to meta-ads-prepare)

This skill stops at localized, campaign-organized videos. The **meta-ads-prepare** step (separate
skill) is what later: covers leftover creator watermarks (e.g. Chinese handles on
WALKTHROUGH_3D), removes competitor App-Store CTA overlays on HELP/TIPS, swaps BGM per
country, and adds Andromeda dedup-evasion fingerprinting. Remember the dub caveat above
when brand-passing.

---

## §5 — Scripts (bundled, under `scripts/`)

| Script | Does |
|---|---|
| `inventory.py` | ffprobe inventory + junk detection |
| `make_grids.py` | 4-up frame grids (8/35/65/92 %) for vision classification |
| `voice_gate.py` | Whisper VO-vs-music gate → JSON map |
| `extract_transcripts.py` | Whisper transcripts + 1080×1920 downscale + Demucs cache + EN-final-with-outro |
| `apply_dub.py` | Claude-translation → Edge-TTS over original BGM + mux + outro |
| `build_campaigns.py` | config-driven campaign scaffolding (hardlinks + kit files) |

All take `--root <batch folder>` and find the Video Translator repo via
`$VIDEO_TRANSLATOR_ROOT`. Run with `PYTHONIOENCODING=utf-8 python -u <script> ...`.

---

## §6 — Parallelism / cost notes

- Vision classification: fan out ~6 subagents (≈20 grids each) — they read grids and
  return JSON `{grid, angle, lang}`. Keep it vision-only.
- Whisper falls back to CPU on this machine (`cublas64_12.dll` missing) — ~25 s/video for
  `small`. Demucs ~30 s/video. Budget ~80–100 s/video for extract; dub apply ~25 s/lang
  (Demucs cached). A 12-video × 2-lang batch ≈ 30–45 min; run scripts in the background.

---

## §7 — App config (multi-app)

This skill is **not DecoAI-specific** — it runs for any IAA app. Per-app context lives in
`apps/<app>.json` (schema in `apps/_TEMPLATE.json`):

| field | used by |
|---|---|
| `app_name` | `_ASSETS.md`, `build_campaigns.py --app` |
| `brand_title`, `brand_sub` | outro / future brand-pass branding |
| `logo` | watermark + outro logo (brand-pass step) |
| `outro_video` | appended to every dub/EN final — pass as `--outro` to extract/apply |
| `play_url`, `value_prop` | ground the ad copy you write per angle/language |
| `default_geo` | country per lang code |

`apps/decoai.json` is the worked example. For a **NEW app**: read its store listing to
extract the value prop, gather the logo + a 1080×1920 outro video, write `apps/<app>.json`,
then write angle×language copy grounded in that value prop. The DecoAI copy in
`references/angles-and-copy.md` is a *style* reference, not a fixed bank — don't reuse
DecoAI's wording for a different app.

Settings (all IAA apps): Advantage+ App Promotion, Value (`AdImpression`), Highest Volume,
Advantage+ Audience + Placements, Dynamic Creative ON, CTA "Use App / Install Now".

---

## §8 — Related skills + memory

- `meta-ads-reels` — the broader pipeline (classify→angles→convert→brand-pass→ads-kit). This
  skill is its dub-localization specialization.
- `meta-ads-prepare` — dedup evasion + watermark/BGM/outro (the next step after this one).
- `classify-translate-videos`, `convert-video` — sub-capabilities folded in here.
- Memory: `translate_claude_only`, `home_decor_2805_batch`, `brand_pass_andromeda`.
