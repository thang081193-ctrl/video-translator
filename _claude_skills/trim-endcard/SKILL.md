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

## Detection algorithm — reverse frame-matching v2

The card the video ENDS on is the ground truth, so the detector compares the tail against the final frame directly (`detect_endcard_v2`, mirrors `pipeline/brand_pass.py::_detect_endcard_start_v2` — keep in sync):

```
window   = min(14s, 45% of duration), sampled at 4 fps, grayscale 96×170
card     = frame vs FINAL frame: mean|diff| ≤ 8 AND ≤15% pixels moved >25
           (tolerates pulsing/animated CTAs that freeze/scene detectors miss)
plateau  = ≥0.7s AND ≥45% consecutive pairs perfectly still (diff <1.0)
           (a card holds still between pulses; real content moves EVERY frame)
multi-card = re-anchor reference per plateau, walk up to 3 cards back
cut      = content→card boundary refined at 12 fps − 0.1s pre-roll (eats fade-in)
refuse   = card fills the whole window (boundary not visible — don't gut a
           static source), or sub-1s tail across a gentle boundary (settling shot)
```

Multi-card outros ("TRY NOW!" → "Download Now!!!") are cut from the FIRST card, same as before — but by plateau walking instead of `min(scene changes)`, so an ordinary content cut near the tail no longer fires.

This replaces the old scene-change scan (threshold 0.08, earliest change in last 30%) which both **under-trimmed** (no event on soft fades into an already-similar card; nothing at all on animated cards) and **over-trimmed** (any ordinary content cut near the tail fired). The scene scan remains only as the numpy-less fallback inside `detect_endcard_start`.

Validated: 10 synthetic cases (`tests/test_endcard_bgm.py` in the Video Translator repo) + 8 real brand-passed outputs — 7/8 found the known ~3.1s appended outro within ±0.2s, 1 conservative refusal.

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
