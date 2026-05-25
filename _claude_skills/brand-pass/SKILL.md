---
name: brand-pass
description: Apply the V4c brand-pass transform to a folder of source videos — Reels 1080×1920 upscale with Andromeda dedup evasion. Strips baked-in side-blur padding, fills full canvas with watermark + outro card, re-dubs only files with real speech, passes through original BGM (or replaces from a royalty-free pool) so audio matches source loudness. Each call produces a unique fingerprint. Use when the user asks to "brand-pass these videos", "Reels brand pass", "make these ad-ready for Meta/TikTok with my brand", or wants to push a batch of source ads through the Andromeda-evasion pipeline.
---

# Brand-Pass

Batch wrapper around `pipeline.brand_pass.brand_pass_video` from the Video Translator project. Takes a folder of source videos (any aspect, with or without baked-in blur padding) and produces Reels-ready 1080×1920 outputs with brand watermark + outro card. Each run yields a unique fingerprint via per-file jittered parameters.

## When to use
- Re-brand a batch of ads (any aspect) into 9:16 Reels.
- Andromeda dedup score < 0.5 on same-source repeats (each call yields a different fingerprint).
- User has a brand logo PNG + outro title/subtitle.

## Do NOT use when
- Source videos need translation to non-English first — run the Video Translator pipeline, then brand-pass the dubbed output.
- User wants to keep original spoken language as voiceover — brand-pass forces English (see "Speech handling" below).

## Per-video pipeline (in order)

1. **End-card auto-trim** (`--trim-endcard`) — scene-detect + cut the last static scene from source tail (competitor app-card screens).
2. **Speech detection** — Whisper-small on source audio, per-segment filter `text.strip() AND avg_logprob > -0.5`, total speech ≥ `max(1.5s, 5% of video)`. Decides voice path vs music-only path. See "Speech handling" for why this threshold.
3. **Audio path A — Voice present**: Demucs htdemucs separates source BGM → Edge TTS reads transcript on top of BGM ducked at `bgm_vol` (0.65–0.80 jittered).
   **Audio path B — Music-only**: skip Demucs + TTS, passthrough source audio at 100% (or replace with `--bgm-pool` track).
4. **Side-blur detection** — cv2 Sobel per-column edge density finds the sharp content region inside any baked-in blur padding. Verified with Laplacian variance (sides must be ≤40% as sharp as center) to reject false positives on native low-detail content.
5. **Effective-aspect branching** — compute aspect AFTER side-blur strip:
   - Within 5% of 9:16 (0.5625) → **Branch A**: pre-crop + zoom 1.03–1.06× + crop fill 1080×1920 (no bands).
   - Else → **Branch B**: blur-pad bg (source scaled-cover + boxblur) + content fit-within full canvas (no safe-zone trim).
6. **Color LUT** — saturation 1.12–1.18, contrast 1.07–1.13, gamma 0.93–0.97, hue 5–11°.
7. **Watermark** — PNG logo at Reels safe-zone corner, opacity 0.55–0.65, position jittered ±30/±15 px.
8. **Outro card** — 1.3–1.7s tail, brand title + subtitle + optional logo, on rotated dark-grey bg.
9. **Encode** — CRF 19–21, preset rotated (fast/medium), `-map_metadata -1` + fake creation_time within last 7 days.

## Usage

```bash
"<video-translator>/.venv/Scripts/python.exe" -u "<skills-dir>/brand-pass/run.py" \
  --src-root  "<input folder>" \
  --dst-root  "<output folder>" \
  --watermark "<logo.png>" \
  --outro-title "DecoAI" \
  --outro-subtitle "Free AI Home Design" \
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

## Dependencies

In addition to the Video Translator project's venv, this skill requires:
- `cv2` (`opencv-python`) — side-blur detection. Install: `pip install opencv-python` in `<repo>/.venv`.
- `librosa` (only for the BGM audit step, not the brand-pass run itself).
- ffmpeg + ffprobe on PATH (already required by the project).
