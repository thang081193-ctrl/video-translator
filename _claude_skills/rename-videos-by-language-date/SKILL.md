---
name: rename-videos-by-language-date
description: Rename video files inside per-language folders to the pattern `<LANG>_<DDMM><NN>.mp4` where LANG is a 2-letter uppercase code derived from the folder name (FR, VI, EN, KM, ...), DDMM is the download day-month extracted from the original filename, and NN is a zero-padded sequence number within each (language, date) bucket. Use when the user asks to "rename videos by language and date", "đặt tên video theo ngôn ngữ và ngày", "FR_1205xx", or similar.
---

# Rename Videos by Language + Date

Walks a root folder, renames every `.mp4` (and its companion `.txt` if present) inside each language subfolder to a short canonical name encoding the spoken language and the download date.

## Example
Input:
```
1205/
  Français/
    meta_ad_26709688688660633_HD_20260512T050015.mp4
    meta_ad_26709688688660633_HD_20260512T050015.txt
    meta_ad_876983235402736_HD_20260512T050425.mp4
    meta_ad_876983235402736_HD_20260512T050425.txt
```
Output:
```
1205/
  Français/
    FR_120501.mp4
    FR_120501.txt
    FR_120502.mp4
    FR_120502.txt
```
Day-month = `1205` (12 May), sequence restarts at `01` per (language, date) bucket. Ordering within a bucket: by the original filename's timestamp (chronological).

## Date format
- The user's convention is **DDMM** (Vietnamese), not MMDD. `12/05/26` → `1205`.
- Source of truth: regex `_(\d{8})T\d{6}` against the original filename → `YYYYMMDD` → reformat to `DDMM`.
- If the filename has no such timestamp, fall back to the file's modification time. If even that fails, skip with `SKIP-NODATE <file>`.

## Language code
Derive from the **folder name** (the canonical native-script label):

| folder | code |
| --- | --- |
| English | EN |
| Tiếng Việt | VI |
| Português | PT |
| Español | ES |
| Français | FR |
| Deutsch | DE |
| Italiano | IT |
| Русский | RU |
| 日本語 | JA |
| 한국어 | KO |
| 中文 | ZH |
| ไทย | TH |
| Indonesia | ID |
| العربية | AR |
| Türkçe | TR |
| हिन्दी | HI |
| Nederlands | NL |
| Polski | PL |
| ខ្មែរ | KM |
| മലയാളം | ML |
| မြန်မာ | MY |
| नेपाली | NE |
| Kiswahili | SW |
| தமிழ் | TA |
| اردو | UR |
| Yorùbá | YO |
| বাংলা | BN |
| فارسی | FA |
| Українська | UK |
| Melayu | MS |
| Filipino | FIL |
| Tagalog | TL |

Full table lives in `rename.py::FOLDER_TO_CODE`. Folders not in the table: log `SKIP-UNKNOWN-LANG <folder>` and leave the folder untouched.

## Hard requirements
1. **UTF-8 stdout** (`PYTHONIOENCODING=utf-8` + `io.TextIOWrapper`) — folder names contain CJK / Devanagari / Arabic.
2. **Live progress**: one log line per rename (`RENAMED <old> -> <new>`). Don't run silent.
3. **Pair the `.txt` with the `.mp4`** — same stem in, same stem out. If `.txt` is missing for an `.mp4`, just rename the `.mp4` and log `NO-TXT <file>`.
4. **Sort order = original-filename timestamp**, so the chronological order of downloads is preserved in the new sequence numbers.
5. **Sequence width = 2 digits by default**. If a bucket has >99 videos, widen automatically to 3 digits (`FR_1205001`). Never silently truncate.
6. **Collision check**: before renaming, ensure the target name doesn't already exist in the folder. If it does (e.g. partial rerun), append `_dup<n>` rather than overwriting, and log `COLLISION`.
7. **Dry-run mode**: pass `--dry-run` to print the plan without touching files. Always offer this to the user before a destructive run.
8. **Idempotent on rerun**: a video already matching `^<CODE>_\d{6,}\.mp4$` for the correct (lang, date) is left alone, but the bucket's counter still consumes that number (so already-renamed files keep their slot). Log `KEEP <file>`.

## Implementation
```bash
PYTHONIOENCODING=utf-8 "<video-translator-venv>/python.exe" -u rename.py "<root>" [--dry-run]
```

Reuse the Video Translator venv at `D:\Dev\Tools\Video Translator\.venv` (stdlib only — no extra deps).

The script:
1. For each subfolder under root, resolve to a language code via `FOLDER_TO_CODE`.
2. List `*.mp4` in the folder, parse the timestamp out of each filename.
3. Group by (code, DDMM); within each group, sort by full timestamp ascending.
4. Assign sequence numbers `01..NN` (or `001..NNN` if needed), build the target name `<CODE>_<DDMM><nn>`.
5. Rename `.mp4` + companion `.txt` (and rewrite nothing inside the `.txt` — only its filename changes).
6. Print summary: total renamed, skipped, collisions.

## What NOT to do
- Don't rename across folders — the language folder is the source of truth for the LANG code. If the user wants to re-classify, run `classify-videos-by-language` first.
- Don't infer date from the `.txt` body — the filename's `_YYYYMMDDTHHMMSS_` suffix is authoritative.
- Don't change anything inside the `.txt` file body. Only the filename.
- Don't use a separator other than `_` between code and date — the user's convention is `FR_1205xx`, no dashes.
- Don't pad with letters — sequence is pure digits.
- Don't run without `--dry-run` first if the user hasn't seen the planned mapping.
