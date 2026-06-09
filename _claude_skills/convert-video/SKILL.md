---
name: convert-video
description: Convert one or more videos to Meta Ads format — Reels (1080×1920) or Feed (1080×1350). Use whenever the user says "convert video", "convert to reels", "convert to feed", "đưa video về reels", "đổi sang reels/feed", or hands you a folder of videos to upload to Meta Ads. Both presets use h264 / 30 fps / 8 Mbps. Layout rule is max-content (no safe-zone shrink).
---

# Convert video to Meta Ads format

Re-encode videos to upload-ready Meta Ads formats. Uses the Video Translator project at `D:\Dev\Tools\Video Translator` (skill is repo-aware).

## Presets

| Preset | Resolution | Aspect | fit_mode | When to use |
|---|---|---|---|---|
| `reels` | 1080×1920 | 9:16 | `blur` (fit + blur-pad short axis) | Reels, Stories placements |
| `feed` | 1080×1350 | 4:5 | `cover` (scale + center-crop short axis) | Mobile News Feed, Instagram Feed/Explore/Profile |

The Feed warning ("This video will be masked on Mobile News Feed…") fires when a 9:16 video lands in Feed placements — Feed only supports up to 4:5. Use the `feed` preset to deliver a properly-sized 4:5 file alongside the Reels file.

## The rule (do not violate)

**Maximize content. No blur padding that shrinks content.**

- Aspect-matching source → fills canvas edge to edge under either preset. **No blur, no crop.**
- Non-matching source under `reels` → fits the FULL canvas at maximum size, blur fills the unavoidable short-axis gap.
- Non-matching source under `feed` → scale to cover and center-crop. No blur strips. We accept losing the symmetric edges (Meta would crop them anyway on Feed placements).

If the user ever asks for a "safe-zone" or "uncovered-area" layout, that is a different task; ask them to confirm before changing the rule.

## Procedure

### 1. Identify scope

- **One file** → use the Video Translator web UI (POST `/api/translate` with `convert_only=true&convert_preset=reels`) or call `pipeline.convert.convert_video()` directly from a Python REPL.
- **A whole folder of language sub-folders** (typical case after `classify-translate-videos`) → use the batch script.

### 2. Run the batch script

```powershell
cd "D:\Dev\Tools\Video Translator"
python scripts\batch_convert.py "<root>" [reels|feed] [<out_dir_name>]
```

- `<root>` is the date folder (e.g. `D:\Dev\App Details\Plant Identifier\video\0905`).
- Preset (2nd arg): `reels` (default) or `feed`.
- The script walks every sub-folder whose name does NOT start with `Converted` or `Unknown`.
- Default output dir is `<root>\Converted_<preset>\<lang>\<original_name>.mp4`. Pass a 3rd argument to override.
- **Resumable**: skips files whose target already exists. To re-encode, delete the target first.

To produce both Reels and Feed versions, run twice:

```powershell
python scripts\batch_convert.py "<root>" reels   # → Converted_reels/
python scripts\batch_convert.py "<root>" feed    # → Converted_feed/
```

### 3. CPU vs GPU

`pipeline/encoder.py` auto-picks NVENC if `pipeline/gpu_state` reports a working GPU; falls back to libx264 otherwise. On CPU, expect ~10s per ~30s clip on a desktop CPU. On NVENC, ~2s per clip.

### 4. Verify

After batch completes, spot-check:

```powershell
ffprobe -v error -select_streams v:0 -show_entries stream=width,height,codec_name,r_frame_rate -of csv=p=0 "<one output file>"
```

Expect `h264,1080,1920,30/1`. The script also runs `verify_output()` per file via `convert_video()` — fatal mismatches throw before the file is kept.

## Pitfalls

- **Don't re-introduce safe-zone shrink.** The OLD reels layout reserved top 14% / bottom 35% and squeezed content into the middle 51%. Tests pinning that behaviour were replaced — if you see `_fit_in_safe_zone` or `top_reserve_pct` references, the code has regressed. Restore `_fit_to_canvas` semantics.
- **Don't switch Feed to blur-pad mode.** Feed's `fit_mode="cover"` is intentional: 9:16 sources lose top/bottom on Feed placements anyway, and blur strips on the sides for a 9:16-into-4:5 conversion would be ~161px wide, ugly, and shrink content. Cover-crop fills the canvas. Reels keeps `fit_mode="blur"` because non-9:16 sources entering a 9:16 canvas usually shouldn't lose horizontal content.
- **Don't skip Whisper-based language verification** before converting if the user asked for it. Use the sibling skill `classify-translate-videos` first; the convert step assumes language folders are already correct.
- **Don't overwrite an existing `Converted/` blindly.** Default output dir is `Converted_<preset>/` (e.g. `Converted_reels`, `Converted_feed`). If the user has an old plain `Converted/`, leave it alone — they'll decide whether to delete after comparing.
- **Don't delete the source `<lang>/` folders after conversion.** Output is a separate hierarchy; originals stay where they are so re-runs and a second preset run are both possible.
- **Don't process `.txt` siblings.** Only `.mp4` files are converted; the matching `.txt` (Meta ad metadata) stays in the source folder, untouched.

## When the user just says "convert video"

- If they hand you a folder of language sub-folders → run batch script. Default to `reels` preset unless they say otherwise; if they mention "feed", "news feed", or the Meta 4:5 warning, use `feed`.
- If they hand you a single file → call `convert_video(src, dst, REELS)` (or `FEED`) directly.
- If they want both formats → run twice, once per preset.
- If unclear → ask "thư mục nào, preset nào (reels/feed)?" and proceed.
