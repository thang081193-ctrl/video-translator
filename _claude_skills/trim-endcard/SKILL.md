---
name: trim-endcard
description: Detect and trim competitor outro/end-card frames from the tail of ad video files. Walks a folder of .mp4s, finds the earliest scene change in the last 30% of each video (threshold 0.08 to catch soft fades), and outputs trimmed copies. Handles multi-card outros (e.g. "TRY NOW!" card → "Download Now" card) by cutting at the first card. Files with no detectable endcard are copied as-is. Use when the user asks to "cắt outro đối thủ", "trim competitor endcard", "remove the last N seconds of competitor branding", or wants to clean up raw ad library videos before brand-passing.
---

# Trim Endcard

Standalone ffmpeg-based competitor end-card trimmer. Detects static/simple outro cards appended to ad videos and stream-copies a trimmed version (no re-encode, fast).

**Part of the brand-pass skill upgrade set** — see also: `gen-outro` (outro card designer). The same detection logic is built into `meta-ads-prepare` via `--trim-endcard`; this skill lets you run the trim step standalone on a raw library before any other processing.

## When to use
- Cleaning raw ad library downloads before brand-passing.
- Previewing how many videos have competitor outros (use `--dry-run`).
- Trimming a folder without needing to re-brand (no watermark / outro card needed).

## Do NOT use when
- You're running brand-pass — use `--trim-endcard` flag there instead (it's already integrated).
- Videos have a wanted CTA card at the end that you want to keep.

## Detection algorithm

```
threshold = 0.08   (catches hard cuts AND soft fades, unlike 0.35 which misses fades)
window    = last (1 - min_drop_pct) of video  [default: last 30%]
cut point = min(valid scene changes in window) [first card, not last]
```

Why `min()` not `max()`:
> A competitor outro often has **multiple cards** (e.g. "TRY NOW!" → "Download Now!!!"). `max()` would only cut the transition *between* cards, leaving the first card in. `min()` cuts from the very first outro card.

Why threshold 0.08 (not 0.35):
> Competitor outros frequently fade in softly (no hard cut). 0.35 misses these. 0.08 catches fades while the `window` filter eliminates false positives from scene cuts early in the video.

## Usage

```bash
python "C:/Users/Thang/.claude/skills/trim-endcard/trim_endcard.py" \
  --src  "<source folder>"    \
  --dst  "<output folder>"    \
  [--inplace]                 \
  [--min-drop-pct 0.7]        \
  [--min-tail-s 0.3]          \
  [--workers 4]               \
  [--limit 0]                 \
  [--dry-run]
```

| Flag | Default | Notes |
|---|---|---|
| `--src` | required | Flat folder of .mp4 files |
| `--dst` | `<src>_trimmed/` | Output folder (created automatically) |
| `--inplace` | off | Overwrite originals (safe — uses temp file + rename) |
| `--min-drop-pct` | 0.7 | Cut point must be ≥ this fraction into the video (0.7 = last 30%) |
| `--min-tail-s` | 0.3 | Minimum detectable tail length in seconds |
| `--workers` | 4 | Parallel workers (detection is CPU/IO-bound, not GPU) |
| `--limit` | 0 | Process only first N files (0 = all) |
| `--dry-run` | off | Report what would be trimmed without writing any files |

## Output

Each file gets one of three labels:

- **`[TRIM]`** — endcard detected, output is trimmed copy. Reports cut timestamp + removed seconds.
- **`[PASS]`** — no endcard found, output is stream-copy of original (same quality, no re-encode).
- **`[SKIP]`** — output already exists (>10 KB). Safe to re-run on partial batches.

Final summary line:
```
DONE in 42.3s — trimmed=18  passthrough=65  skipped=0  errors=0  total_tail_removed=47.2s
```

## Dry-run first

Always preview with `--dry-run` before a destructive `--inplace` run:

```bash
python trim_endcard.py --src "C:/Users/Thang/Downloads/MetaAdLibrary/English" --dry-run
```

## Dependencies

- **ffmpeg + ffprobe** on PATH (no Python dependencies beyond stdlib)
