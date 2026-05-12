---
name: rename-language-folders
description: Rename language subfolders to their native-script names (e.g. `Other_km` → `ខ្មែរ`, `vi` → `Tiếng Việt`, `Khmer` → `ខ្មែរ`), and rewrite the `Language:` line inside any sibling `.txt` metadata file. Use when the user asks to "rename folders to proper language names", "đặt tên folder đúng chuẩn tiếng", "fix Other_xx folder names", or any time language folders are labeled with ISO codes / `Other_*` / English names instead of native script.
---

# Rename Language Folders to Native Script

Walks a root folder, renames each subfolder whose name matches a known ISO-639-1 code, an `Other_<code>` placeholder, or a common English language name, into the native-script label for that language. Also rewrites the `Language:` line inside every `<stem>.txt` companion file so metadata matches the new folder name.

## When to use
- Output of `classify-videos-by-language` produced `Other_<code>` folders for codes outside its mapping.
- Folders are labeled with ISO codes (`en`, `vi`, `km`) and the user wants native script.
- Folders are labeled in English (`Khmer`, `Tamil`) and the user wants native script (`ខ្មែរ`, `தமிழ்`).
- User asks any variant of "đặt tên folder đúng chuẩn tiếng" / "rename to proper language names".

## Mapping (target = native-script label)
The full table lives in `rename.py::NATIVE`. Each entry maps every known alias of a language (ISO code, `Other_<code>` form, common English name, common autonym variants) to the canonical native-script folder name. Examples:

- `en`, `english`, `English` → `English` (keep — already standard)
- `vi`, `vietnamese`, `Vietnamese` → `Tiếng Việt`
- `km`, `Other_km`, `Khmer`, `khmer` → `ខ្មែរ`
- `ml`, `Other_ml`, `Malayalam`, `malayalam` → `മലയാളം`
- `ta`, `Other_ta`, `Tamil`, `tamil` → `தமிழ்`

Adding a new language: append to `NATIVE` in `rename.py`. Aliases are matched case-insensitively against the folder basename only.

## Hard requirements
1. **UTF-8 stdout** (`PYTHONIOENCODING=utf-8` + `io.TextIOWrapper`) — native names contain CJK, Devanagari, Arabic, etc.
2. **Live progress**: one log line per rename (`RENAMED <old> -> <new>`) and per `.txt` fix (`FIXED <path>`). Don't run silent.
3. **Idempotent**: if `<new>` folder already exists, MERGE — move contents in, then delete old folder. Never overwrite an existing file inside the target; abort the move for that file and log `SKIP-EXISTS <file>`.
4. **Update `Language:` line in `.txt`**: only the first `Language:` line; preserve the rest of the file byte-for-byte. If no `Language:` line exists, prepend one.
5. **Skip if name unchanged** — don't touch folders that already match a native label.
6. **Walk only one level deep** under the root. Don't recurse into nested folders.

## Implementation
```bash
PYTHONIOENCODING=utf-8 "<video-translator-venv>/python.exe" -u rename.py "<root>"
```

Re-use the Video Translator venv at `D:\Dev\Tools\Video Translator\.venv` (no extra deps needed — stdlib only).

The script:
1. Lists `<root>/<sub>/` folders.
2. Normalizes each folder name through the alias table → target native name.
3. If different: rename (or merge if target exists).
4. After all renames: walks all `.txt` files under each final folder and rewrites the `Language:` line.
5. Prints summary: `Renamed=N  MergedInto=M  TxtFixed=K`.

## Companion skill
This is typically run right after `classify-videos-by-language`. The classify skill already writes native names for known codes; this rename skill is the cleanup pass for unmapped codes (`Other_<code>`) and for legacy folders left over from other tools.

## What NOT to do
- Don't rename folders that aren't in the alias table — leave them alone with a `SKIP-UNKNOWN <name>` log line and let the user decide.
- Don't recurse deeper than one level — videos sit directly inside the language folder.
- Don't rewrite the entire `.txt` file — only the `Language:` line. The rest of the file (Library ID, source URL, ad copy) must stay intact.
- Don't delete folders that still contain files (the merge step should move everything out first; if anything remains, log and skip).
