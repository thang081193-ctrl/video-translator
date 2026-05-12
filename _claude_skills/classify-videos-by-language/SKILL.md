---
name: classify-videos-by-language
description: Detect the spoken language in video files (via Whisper on extracted audio) and reorganize them into per-language subfolders. Use when the user asks to "phân loại video theo ngôn ngữ", "classify videos by language", "detect language of these videos", or wants to fix mis-labeled language folders. Updates the `Language:` field inside companion `.txt` files when present. Reports progress live to stdout (one line per video).
---

# Classify Videos by Language

Detect the spoken language of each video using `faster-whisper`, then move the video (and any sibling `.txt` metadata file with the same stem) into a per-language subfolder under the target root.

## When to use
- User has a folder of videos and wants them grouped by language.
- A previous classification (e.g. from Facebook ad-library metadata) is unreliable and the user wants to reclassify using the actual audio.
- An `Unknown` language folder needs to be split out into real languages.

## Inputs
- **root**: absolute path to the parent folder. Subfolders (e.g. `English`, `Unknown`) hold the videos. If the root contains videos directly (no subfolders), treat them as input too.
- Optional: model size (`tiny`/`base`/`small`/`medium`). Default `small` — best speed/accuracy on CPU. Use `medium` only if `small` mislabels.

## Hard requirements (do not skip)
1. **Progress logging**: stdout must emit a line per video as `[N/TOTAL] <file> -> <lang_code> -> <folder>`. Use `python -u` and `PYTHONIOENCODING=utf-8`. Never run buffered, never run silent.
2. **UTF-8 stdout**: folder names may contain CJK/Vietnamese/Portuguese characters — wrap `sys.stdout`/`sys.stderr` in a UTF-8 `TextIOWrapper` at the top of the script, or set `PYTHONIOENCODING=utf-8`. Otherwise Windows `cp1252` will crash with `UnicodeEncodeError`.
3. **Device selection**: try `cuda` only if `cublas64_12.dll` is importable; otherwise fall back to `cpu` with `compute_type="int8"`. On this machine CUDA model loads but inference fails — default to **CPU**.
4. **Audio extraction**: use `ffmpeg -ss 2 -t 15 -vn -ac 1 -ar 16000 -f wav` to grab a 15-second mono 16kHz clip starting 2s in (skip silent intro). 15s is enough for language ID and 2× faster than 30s.
5. **VAD on**: pass `vad_filter=True` to skip music-only intros that confuse detection.
6. **Companion `.txt` handling**: if `<stem>.txt` exists, move it alongside the video AND rewrite the `Language:` line to the new folder name (the human-readable label, not the ISO code).
7. **Idempotent moves**: skip moving when current folder == target folder, but still rewrite the `.txt` so it matches reality.

## Folder name mapping (ISO 639-1 → folder)
Use these human-readable folder names so they match what humans expect:

The mapping covers all common Whisper-detected codes. See `classify.py::LANG_FOLDERS` for the full table — includes en/vi/pt/es/fr/de/it/ru/ja/ko/zh/th/id/ar/tr/hi/nl/pl/km/ml/my/ne/sw/ta/ur/yo/bn/fa/uk/ms/fil/sv/no/da/fi/el/he/cs/ro/hu/bg/sr/hr/sk/sl/lt/lv/et and writes the folder name in that language's native script (e.g. `ខ្មែរ` for Khmer, `மலையாளம்` for Malayalam).

Any code not in the table → folder `Other_<code>`. When you see such a folder, add it to the table in this skill and rerun. Detection failure → keep in `Unknown`.

## Implementation
Run the bundled script `classify.py` with the target folder as the first arg:

```bash
PYTHONIOENCODING=utf-8 "<video-translator-venv>/python.exe" -u classify.py "<root>"
```

The script:
1. Walks `<root>/<subfolder>/*.mp4` (and `<root>/*.mp4`).
2. For each video: extracts 15s audio → runs Whisper language detect → maps to folder.
3. Prints `[N/TOTAL] <file> currently=<X> detected=<code> target=<Y>` immediately (line-buffered).
4. After all detections, applies moves in a second pass and prints `MOVED <file>: <from> -> <to>` for each.
5. Final summary: counts per detected language + list of detection failures.

The script and its dependencies (`faster-whisper`, `ffmpeg` on PATH) live in the Video Translator project at `D:\Dev\Tools\Video Translator\.venv`. Re-use that venv — do not create a new one.

## Confirming results
After the run finishes:
1. Print per-folder counts: `ls <root>/<folder> | grep -c .mp4`.
2. Spot-check 2–3 moved videos by reading the rewritten `.txt` to confirm `Language:` matches the new folder.
3. Report total moves, detection failures, and any new `Other_*` folders created.

## What NOT to do
- Don't trust the `Language:` field in the source `.txt` — the user's whole reason for running this is that field is unreliable.
- Don't use a tighter VAD threshold than default — it filters out quiet speech.
- Don't run on GPU without verifying inference works (load success ≠ inference success).
- Don't classify based on filename or ad-copy text — only on extracted audio.
- Don't run silently — every video must emit a log line before moving on.
