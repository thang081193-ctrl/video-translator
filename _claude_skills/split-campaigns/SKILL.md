---
name: split-campaigns
description: Two pre-processing helpers for splitting an ad-video library into campaigns BEFORE a meta-ads-prepare run. (1) detect_voice.py classifies every source video as VOICE vs BGM-only using the exact brand_pass Whisper speech-gate -> voice_manifest.csv, so voiced clips route to a re-dub path and music-only clips route to a BGM-swap path. (2) prep_campaign_split.py builds a labeled keyframe contact sheet per video + a manifest of the ad-library Primary Text / Headline, so Claude can VIEW the clips and cluster them into creative angles. Use when the user asks to "split into campaigns", "classify voice vs music", "route voiced vs BGM videos", "tach angle/campaign", "voice gate the folder", or needs the voice/BGM routing + angle-split pre-step that feeds meta-ads-prepare.
---

# Split Campaigns

Two standalone helper scripts for the steps that come BEFORE `meta-ads-prepare`: deciding how each source video should be processed (re-dub vs BGM-swap) and grouping the library into creative angles. Both run on the local Video Translator `.venv` / system Python (ffmpeg + PIL + faster-whisper).

Scripts locate the Video Translator repo via `$VIDEO_TRANSLATOR_ROOT` (default `D:/Dev/Tools/Video Translator`).

## When to use
- Before a `meta-ads-prepare` batch, to route each clip deliberately: voiced -> TTS re-dub or keep-original-voice; music-only -> BGM passthrough / `--bgm-pool` swap.
- To cluster a mixed library into creative angles for per-campaign organization.
- When the user says "voice-gate the folder", "split into campaigns/angles", "tach voice vs nhac".

## Do NOT use when
- You just need the full prep/dedup pipeline -> that's `meta-ads-prepare`.
- You need language detection from on-screen text -> that's `classify-translate-videos` / vision tagging. This voice gate is AUDIO-based and only says VOICE vs BGM, NOT which language (BGM is not language).

## Tool 1 — `detect_voice.py` (VOICE vs BGM-only gate)

Reuses `pipeline.brand_pass._transcribe_video` — the SAME speech-gate `meta-ads-prepare` uses (`avg_logprob > -0.5`) — so the routing matches what the prepare step will actually do.

```bash
python "<skills-dir>/split-campaigns/detect_voice.py" "<src_root>" [--out voice_manifest.csv]
```

- `<src_root>` = a flat folder of `*.mp4`, OR a folder with one level of `<lang>/*.mp4` subfolders.
- Output: `<src_root>/voice_manifest.csv` with columns `lang,file,has_voice,speech_chars,transcript`.
- `has_voice=yes` -> real narration (route to re-dub / keep-original-voice). `has_voice=no` -> music or no audio (route to BGM passthrough or `--bgm-pool` swap).
- Whisper runs on CPU here (~25 s/video for the gate); run in the background for big folders.

## Tool 2 — `prep_campaign_split.py` (angle-split contact sheets)

Lets Claude (in chat, via vision) SEE each video and cluster by creative angle — no external API, ffmpeg + PIL only.

```bash
python "<skills-dir>/split-campaigns/prep_campaign_split.py" "<root>" \
  [--frames 6] [--cols 3] [--width 360] [--out _campaign_prep] [--limit 0]
```

- `<root>` = folder with `<lang>/` subfolders of `*.mp4` (optional sibling `.txt` ad-library metadata).
- For each video: extracts `--frames` keyframes evenly across `[5%, 95%]`, tiles them into one labeled contact-sheet `.jpg`, and parses the `.txt` for Primary Text + Headline/CTA.
- Output: `<root>/_campaign_prep/<lang>__<base>.jpg` (one sheet per video) + `<root>/_campaign_prep/manifest.md`.
- Next: Claude reads the sheets + manifest, assigns each video an angle, then build per-campaign folders (see `meta-ads-reels` / `meta-ads-localize` for the campaign scaffolding).

## Typical flow
1. `detect_voice.py` -> `voice_manifest.csv` (route voiced vs music-only).
2. `prep_campaign_split.py` -> contact sheets + `manifest.md`.
3. Claude clusters videos into angles from the sheets.
4. Hand off to `meta-ads-prepare` (per-clip prep/dedup) + campaign organization.

## Dependencies
- `ffmpeg` + `ffprobe` on PATH.
- `Pillow` (contact sheets) and `faster-whisper` (voice gate, via the Video Translator repo).
- `$VIDEO_TRANSLATOR_ROOT` pointing at the repo (default `D:/Dev/Tools/Video Translator`).
