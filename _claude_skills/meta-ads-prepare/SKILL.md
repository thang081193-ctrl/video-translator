---
name: meta-ads-prepare
description: Prepare a folder of source videos as ready-to-ship Meta/TikTok ad creatives — Reels 1080×1920 upscale with Andromeda dedup evasion. Workflow has three pillars (1) Voice/BGM routing — classify voiced vs music-only sources upfront and route deliberately; (2) Audio handling three ways — TTS re-dub, KEEP original voice over new BGM, or music-only passthrough/replace from a royalty-free pool; (3) Freeze-to-EOF end-card trim that removes competitor outros without over-trimming testimonials. Also strips baked-in side-blur padding, fills full canvas with watermark + outro (PNG card OR a supplied outro video). Each call produces a unique fingerprint. Use when the user asks to "prepare these for Meta ads", "Meta Ads Prepare", "brand-pass these videos", "Reels brand pass", "make these ad-ready for Meta/TikTok with my brand", or wants to push a batch of source ads through the Andromeda-evasion pipeline.
---

# Meta Ads Prepare

Batch wrapper around `pipeline.brand_pass.brand_pass_video` from the Video Translator project. Takes a folder of source videos (any aspect, with or without baked-in blur padding) and produces Reels-ready 1080×1920 outputs with brand watermark + outro card. Each run yields a unique fingerprint via per-file jittered parameters.

> Previously named **brand-pass**. Same pipeline; renamed to **Meta Ads Prepare** to reflect the full ad-prep workflow (routing + audio + endcard), not just the brand overlay.

## Three pillars of the workflow

1. **Voice/BGM routing (pre-step)** — classify each source as voiced vs music-only with `detect_voice.py` BEFORE batching, then route deliberately. The pipeline's auto-decide silently re-dubs ANY voiced file with a male TTS voice, which ruins UGC/testimonials. See "Voice/BGM routing — classify BEFORE batching".
2. **Audio handling — three modes** — (a) TTS re-dub, (b) **keep original voice** over new BGM (`keep_original_voice=True`, Demucs isolates the vocals stem — no robot voice), (c) music-only passthrough/replace (`transcript=""`). See "Speech handling".
3. **End-card trim — freeze-to-EOF** — removes the competitor's static outro card via a freeze segment that runs to EOF, NOT scene-detect. Catches soft fades into a card without over-trimming a testimonial that ends on a low-motion shot. See "End-card detection".

## When to use
- Re-brand a batch of ads (any aspect) into 9:16 Reels.
- Andromeda dedup score < 0.5 on same-source repeats (each call yields a different fingerprint).
- User has a brand logo PNG + outro title/subtitle.

## Do NOT use when
- Source videos need translation to non-English first — run the Video Translator pipeline, then brand-pass the dubbed output.
- User wants the TTS re-dub in a non-English language — the TTS path forces English. (To keep the original spoken audio untouched, use `keep_original_voice=True` instead — see "Speech handling".)

## Voice/BGM routing — classify BEFORE batching

Brand-pass auto-decides voice-vs-music per file, but for a campaign batch you should classify UPFRONT and route deliberately — the default re-dubs ANY voiced file with a male Edge-TTS voice, which ruins UGC/testimonials (a robot voice over a real mom talking-head).

1. Run `detect_voice.py` (in `split-campaigns` skill) over the source folder → `voice_manifest.csv` (lang,file,has_voice,speech_chars,transcript). It reuses the exact brand_pass speech-gate so it matches what brand-pass will actually do.
2. Split into VOICE vs BGM-only groups; cross-reference the angle/theme split.
3. **ASK the user, per theme, which voice** (or whether to KEEP the original voice). Never silently default to male TTS.
4. BGM-only → brand-pass with `--bgm-pool` (music-only path). VOICE → either TTS re-dub or `keep_original_voice=True`.

Caveat: the gate is conservative — a testimonial buried under loud BGM can be mis-flagged `has_voice=no` (low avg_logprob), then mis-routed to music-only (its real voice gets replaced). Spot-check the manifest against the angle split; for any UGC/testimonial set, prefer keeping the source audio (no BGM swap) over a swap that kills the voice.

## Per-video pipeline (in order)

1. **End-card auto-trim** (`--trim-endcard`) — **freeze-detect primary**, scene-detect fallback. Finds the static outro card the video ENDS on (robust to soft fades that scene-detect misses), with a transition guard so a held talking-head shot isn't mistaken for a card. See "End-card detection".
2. **Speech detection** — Whisper-small on source audio, per-segment filter `text.strip() AND avg_logprob > -0.5`, total speech ≥ `max(1.5s, 5% of video)`. Decides voice path vs music-only path. See "Speech handling" for why this threshold.
3. **Audio path A — TTS re-dub (default, voice present)**: Demucs htdemucs separates source BGM → Edge TTS reads transcript on top of BGM ducked at `bgm_vol` (0.65–0.80 jittered).
   **Audio path A' — Keep original voice** (`keep_original_voice=True`): Demucs isolates the source *vocals* stem and mixes the original talking-head voice over new BGM (`--bgm-pool` track or the source's own no_vocals stem) — NO TTS. Use for UGC/testimonials where a robot voice would ruin authenticity.
   **Audio path B — Music-only**: skip Demucs + TTS, passthrough source audio at 100% (or replace with `--bgm-pool` track).
4. **Side-blur detection** — cv2 Sobel per-column edge density finds the sharp content region inside any baked-in blur padding. Verified with Laplacian variance (sides must be ≤40% as sharp as center) to reject false positives on native low-detail content.
5. **Effective-aspect branching** — compute aspect AFTER side-blur strip:
   - Within 5% of 9:16 (0.5625) → **Branch A**: pre-crop + zoom 1.03–1.06× + crop fill 1080×1920 (no bands).
   - Else → **Branch B**: blur-pad bg (source scaled-cover + boxblur) + content fit-within full canvas (no safe-zone trim).
6. **Color LUT** — saturation 1.12–1.18, contrast 1.07–1.13, gamma 0.93–0.97, hue 5–11°.
7. **Watermark** — PNG logo at Reels safe-zone corner, opacity 0.55–0.65, position jittered ±30/±15 px.
8. **Outro** — either a generated card (1.3–1.7s tail, brand title + subtitle + optional logo on rotated dark-grey bg) OR, if `--outro-video <mp4>` is given, the supplied outro clip is appended (its real duration is probed and used). Prefer a designed outro video for polished campaigns.
9. **Encode** — CRF 19–21, preset rotated (fast/medium), `-map_metadata -1` + fake creation_time within last 7 days.

## Usage

```bash
"<video-translator>/.venv/Scripts/python.exe" -u "<skills-dir>/meta-ads-prepare/run.py" \
  --src-root  "<input folder>" \
  --dst-root  "<output folder>" \
  --watermark "<logo.png>" \
  --outro-title "DecoAI" \
  --outro-subtitle "Free AI Home Design" \
  [--outro-video "<outro.mp4>"] [--outro-logo "<logo.png>"] \
  [--brand-bg "<1080x1920 brand_bg.png>"] \
  [--trim-endcard] \
  [--bgm-pool "<folder>"] [--bgm-mode by-mood|random] \
  [--workers 4] [--limit 0] [--seed-base 20260607]
```

- The wrapper assumes the Video Translator repo at `D:/Dev/Tools/Video Translator` (override with env `VIDEO_TRANSLATOR_ROOT`).
- `--src-root` may be flat (`*.mp4` direct children) or nested by language (`<root>/<lang>/*.mp4`). Outputs preserve structure under `--dst-root`.
- Skip-if-exists: dst files >100 KB are skipped, so batches are resumable.
- `--seed-base N` makes the run deterministic; omit (or pass 0) for fresh randomness each file.

### Important — folder convention

`collect_jobs()` skips any subfolder under `--src-root` whose name starts with `_` (e.g. `_branded`, `_branded_v2`, `_tmp`). This prevents nested-output folders being re-processed as inputs on a second run. Don't name a real input subfolder with a leading underscore.

## Speech handling — why `avg_logprob > -0.5`

Whisper run with `language="en"` HALLUCINATES short text on music-only ad sources (e.g. "Thank you.", "Jimmy is buying", "Jini is my anxiety"). To prevent the TTS path firing on those:

| Signal | Music hallucination | Real brief hook |
|---|---|---|
| `no_speech_prob` | 0.66–0.78 | 0.73–0.77 — SAME RANGE, unreliable |
| `avg_logprob` | **-0.7 to -1.0** | **-0.2 to -0.3** — clear split |

Use `avg_logprob > -0.5` (well between the two clusters). `no_speech_prob` is NOT a useful filter — it fires high on any music-dominant content, including real short hooks.

When speech is gated out, brand-pass switches to music-only path: skip Demucs + TTS, passthrough source audio at 100% volume so BGM matches original loudness. (Earlier versions ducked the BGM at 0.4 even when no voice was present → quieter than source. Fixed.)

### Keeping the original voice (`keep_original_voice=True`)

For UGC / testimonials, re-dubbing with TTS destroys authenticity. Pass `keep_original_voice=True` (a `brand_pass_video` kwarg — there's no run.py CLI flag, so call it from a small per-file reprocess script, see below) to:

1. Demucs htdemucs separate the source → isolate the **vocals** stem.
2. Mix the original vocals (vol 1.0) over new BGM — `bgm_replace_path` track if given, else the source's own no_vocals stem. NO TTS, no Whisper transcript needed for audio.

`keep_voice` only engages when the speech-gate also detects voice (`has_voice`). If a testimonial is buried under loud BGM the gate returns empty → keep_voice won't engage → it falls to music-only and a `bgm_replace_path` would REPLACE the whole track, killing the voice. For those, the safe move is to NOT swap BGM (omit `bgm_replace_path`) so the source audio (voice + its music) passes through at 100%.

Per-file routing is done with a small script rather than the batch CLI (the CLI is one-mode-for-all). Pattern:

```python
from pipeline.brand_pass import brand_pass_video
brand_pass_video(input_path=src, output_path=dst,
    watermark_image=WM, outro_video=OUTRO,
    outro_title="Chatify", outro_subtitle="AI Baby Photo Studio",
    trim_endcard=True,
    bgm_replace_path=TRACK,        # omit to keep source music
    keep_original_voice=True,      # keep talking-head voice over new BGM
    random_seed=SEED)
# music-only override: pass transcript="" to force-skip Whisper + TTS entirely
```

## End-card detection — freeze-to-EOF (`--trim-endcard`)

Competitor sources often end on a static app/brand card (catalog screen, logo, CTA). The trimmer removes it so your own outro is the only end-card.

**The load-bearing signal is a freeze segment that runs to EOF**, NOT a scene-change near the tail. A static card always freezes to the end; a scene-change alone fires on ordinary content cuts (e.g. a UGC testimonial cutting to its low-motion closing shot) and must NOT trigger a trim.

- `_scan_freeze` runs `ffmpeg freezedetect=n=0.003:d=0.4`, pairs freeze_start/end events, and keeps the freeze that reaches (within 0.6s of) EOF. Its start = the card start.
- Guards: ignore the tail if it's shorter than `min_tail_s` (0.3s, no real card) or longer than `max_tail_pct` (0.55) of the video (a mostly-static source — trimming would gut content).
- If nothing freezes to EOF → return None → **no trim** (the video ends on motion = real content).
- Returns the freeze start directly. Do NOT back up to a nearby scene-change — that risks eating real content in the 1–1.5s before the card.

**Why freeze-detect over scene-detect (the old approach):** scene-detect missed soft fades INTO a static card (no hard cut to detect) → competitor outro left in (the original under-trim complaint). It also over-trimmed testimonials by mistaking the content cut into a low-motion closing shot for an outro. Freeze-detect fixes both: it catches the static card after a soft fade, and it leaves motion-ending testimonials alone.

Validated on the 57-video Chatify baby batch: 54 TRIM (median 1.3s, max 3.1s tail removed), 3 PASS — the 1 UGC testimonial that ends on its closing shot + 2 short gallery clips with no static tail.

## Side-blur stripping — two-stage detection

Many ad sources are originally 1:1 or 4:5 content padded to 9:16 by adding a horizontally-blurred extension of the same content on left+right. ffmpeg cropdetect catches dark/solid bars but misses blur. Brand-pass adds a custom detector:

1. **Edge density** (cv2 Sobel-x) per column, averaged across 5 sampled frames. Find left/right where smoothed density rises above 30% of column-max. If sharp region < 92% of source width → candidate.
2. **Laplacian variance verify** — sample one mid-video frame, compute Lap variance of left-blur region vs center. Real blur has `side_var / center_var ≤ 0.40`. Above that → false positive (just natural low-detail content like sky/walls) → skip.

After stripping, the cropped content's effective aspect determines layout branch. For native 9:16 sources with no padding, both stages reject → no crop applied. Caught EN_2405_01-style false positives that the edge-density-only detector triggered on.

## BGM replacement (`--bgm-pool`)

Source videos often use copyrighted music that Meta/TikTok fingerprint-flag. Use `--bgm-pool` to swap in royalty-free tracks:

```
<bgm-pool>/
  cluster_map.csv          # file,cluster mapping (from BGM audit)
  A_cinematic/track1.mp3   # subfolder per cluster
  B_indiepop/track1.mp3
  C_lofi/track1.mp3
  D_ambient/track1.mp3
```

- `--bgm-mode by-mood` (default): look up source filename in `cluster_map.csv` → random pick from `<pool>/<cluster>/`. Best match to source feel.
- `--bgm-mode random`: random pick from any track under `<pool>/` (ignores map).
- Multiple tracks per cluster subfolder → random pick adds per-video diversity.
- Tracks shorter than video are auto-looped (`-stream_loop -1`).

When `--bgm-pool` is set, brand-pass skips Demucs entirely (saves ~10 s/video). The replacement plays at 100% volume in music-only sources, or ducked at `bgm_vol` under new TTS in voice sources.

To generate `cluster_map.csv` for a new batch, run an audit (Demucs → Whisper-on-BGM → librosa tempo/energy/brightness → cluster). Pixabay is the recommended source for royalty-free tracks (free, no attribution, commercial use OK).

## Brand-bg PNG (legacy `--brand-bg`)

Optional 1080×1920 PNG canvas for non-9:16 sources. When provided, replaces the blur-pad bg with a static brand-designed image (smaller content area but baked-in brand zones).

- Exact 1080×1920, otherwise FileNotFoundError or visual cutoff.
- Top brand zone:    y=0–265
- Middle video zone: y=265–1655 **must be transparent or empty**
- Bottom brand zone: y=1655–1920

Default (no flag) = blur-pad bg, which gives larger content area.

## Output naming

Output files inherit source filename verbatim. Run a rename pass afterwards if a canonical name like `EN_240501.mp4` (no underscore between date+seq) is desired — see `rename-videos-by-language-date` skill, or for flat folders use a one-liner:

```bash
cd <dst-root> && for f in EN_2405_*.mp4; do mv "$f" "${f/EN_2405_/EN_2405}"; done
```

## Verifying a run

1. **Per-file check** — for each cluster of source aspects (9:16 native, 4:5, 1:1), eyeball 1 output frame to confirm:
   - Native 9:16 source → full canvas, no false-positive crop (check Lap verify worked).
   - Side-blur source → blur stripped, content fills full canvas (Branch A) or has blur-pad top/bot (Branch B).
   - Watermark visible in top-right corner (Reels safe zone).
   - Outro card readable at video tail.
2. **Audio spot-check** — play 1 file from each speech class:
   - Voice file (real hook) → English TTS reads the transcript over ducked BGM.
   - Music-only file → BGM at source-equivalent loudness, no random voice insertions.
3. **File size** — branded outputs are typically 1.5–3× source (1080p re-encode of often-720p source).

## Common pitfalls / how to recover

- **Wrong dimensions output** (content tiny in middle, big purple bands) — using an old branch with solid-band layout. Confirm `pipeline/brand_pass.py` has the blur-pad layout (no top/bot drawtext bands).
- **TTS voice on music-only file** — voice gate too loose. Verify gate is `avg_logprob > -0.5`, not `-1.0` or no_speech_prob-based.
- **Side-blur stripped on a native 9:16 source** — Laplacian verify missing. Confirm `_detect_side_blur` includes the cv2.Laplacian variance check before returning the crop region.
- **52 jobs instead of 26** — `_branded_*` folder ended up inside `--src-root` and got scanned as input. Confirm `collect_jobs` excludes `_*` folders. Move dst outside src-root if rerun is recursive-unsafe.
- **`tts.mp3` ffmpeg mix failure** — empty transcript reached TTS step. Confirm `_transcribe_video` returns `""` when gate fails AND the audio flow has the music-only branch (passthrough or silent placeholder).
- **GPU "cublas64_12.dll" errors** — Whisper falls back to CPU automatically. Slow but works. Don't try to fix unless GPU was previously working.
- **End-card trim ate real content (UGC testimonial)** — old scene-detect logic mistook the content cut into a low-motion closing shot for an outro. Fixed: trim now requires a freeze-to-EOF. If a testimonial still gets trimmed, confirm `_detect_endcard_start` calls only `_scan_freeze` (no scene fallback) and that the closing shot really isn't a held static frame.
- **Robot voice over a real talking-head** — a testimonial got TTS-redubbed. Either the voice gate mis-flagged it, or `keep_original_voice` wasn't passed. For UGC, route via `detect_voice` first and use `keep_original_voice=True` (or keep source audio with no BGM swap).

## Brand-Pass Skill Upgrades

Two companion skills extend the brand-pass pipeline for standalone use:

### `gen-outro` — Outro card designer
Preview or regenerate the branded outro card (1080×1920 PNG) without running the full video pipeline.
- Themes: `baby` (pastel pink/lavender, chibi, hearts+stars), `tech` (dark purple), `minimal` (clean grey)
- Required inputs: logo PNG path + theme choice
- Output: static PNG for review before committing to a full batch
- Path: `C:/Users/Thang/.claude/skills/gen-outro/gen_outro.py`

```bash
python gen_outro.py --logo "Logo.png" --theme baby --title "Chatify" --subtitle "Your AI Companion" --out preview.png
```

### `trim-endcard` — Standalone competitor outro trimmer
Run just the endcard trim step on a raw folder — no watermark, no re-encode, no TTS.
- Uses threshold 0.08 (catches soft fades, not just hard cuts)
- Takes `min()` of scene changes in last 30% → cuts from FIRST outro card (handles multi-card outros)
- `[TRIM]` / `[PASS]` / `[SKIP]` per-file status + summary of total tail seconds removed
- Path: `C:/Users/Thang/.claude/skills/trim-endcard/trim_endcard.py`

```bash
python trim_endcard.py --src "English/" --dst "English_trimmed/" --dry-run   # preview first
python trim_endcard.py --src "English/" --dst "English_trimmed/" --workers 4
```

Use `trim-endcard` when you want to clean up a raw library *before* brand-passing, or to trim without the full branding overhead.

> NOTE: the standalone `trim-endcard` skill still uses **scene-change** detection. The `--trim-endcard` flag inside `meta-ads-prepare` has moved to **freeze-to-EOF** detection (see "End-card detection" above), which is more accurate — it catches soft fades and won't over-trim motion-ending testimonials. If results differ, the in-pipeline freeze logic is the newer one.

### `detect_voice` — voice/BGM classifier (in `split-campaigns`)
Classify each source as VOICE vs BGM-only BEFORE brand-passing, so you can route deliberately (see "Voice/BGM routing").
- CLI: `python detect_voice.py <src_root> [--out voice_manifest.csv]`
- Reuses the exact `_transcribe_video` speech-gate from brand_pass → matches what brand-pass will do.
- Output: `voice_manifest.csv` (lang,file,has_voice,speech_chars,transcript).
- Path: `C:/Users/Thang/.claude/skills/split-campaigns/detect_voice.py`

## Dependencies

In addition to the Video Translator project's venv, this skill requires:
- `cv2` (`opencv-python`) — side-blur detection. Install: `pip install opencv-python` in `<repo>/.venv`.
- `librosa` (only for the BGM audit step, not the brand-pass run itself).
- ffmpeg + ffprobe on PATH (already required by the project).
