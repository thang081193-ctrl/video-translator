"""Rename language subfolders to native-script names; rewrite Language: lines in .txt.

Usage:
  PYTHONIOENCODING=utf-8 python -u rename.py <root_folder>

For each <root>/<sub>/ folder whose name is a known ISO code, Other_<code>, or
common English/autonym alias, rename it to the canonical native-script label.
Merges into existing target folders rather than overwriting. Then rewrites the
first `Language:` line in every <stem>.txt to match the new folder.

Emits one log line per action (line-buffered, UTF-8 safe).
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace", line_buffering=True)

import argparse
import re
import shutil
from pathlib import Path

# Each tuple: (canonical_native_name, [aliases...]). Aliases are matched case-insensitively.
NATIVE = [
    ("English",    ["en", "english"]),
    ("Tiếng Việt", ["vi", "vietnamese", "tieng viet", "tieng-viet"]),
    ("Português",  ["pt", "portuguese", "portugues"]),
    ("Español",    ["es", "spanish", "espanol"]),
    ("Français",   ["fr", "french", "francais"]),
    ("Deutsch",    ["de", "german"]),
    ("Italiano",   ["it", "italian"]),
    ("Русский",    ["ru", "russian"]),
    ("日本語",      ["ja", "japanese", "nihongo"]),
    ("한국어",      ["ko", "korean", "hangugeo"]),
    ("中文",        ["zh", "chinese", "mandarin", "zhongwen"]),
    ("ไทย",         ["th", "thai"]),
    ("Indonesia",  ["id", "indonesian", "bahasa indonesia"]),
    ("العربية",    ["ar", "arabic"]),
    ("Türkçe",     ["tr", "turkish", "turkce"]),
    ("हिन्दी",      ["hi", "hindi"]),
    ("Nederlands", ["nl", "dutch"]),
    ("Polski",     ["pl", "polish"]),
    ("ខ្មែរ",        ["km", "khmer", "cambodian"]),
    ("മലയാളം",    ["ml", "malayalam"]),
    ("မြန်မာ",     ["my", "burmese", "myanmar"]),
    ("नेपाली",      ["ne", "nepali"]),
    ("Kiswahili",  ["sw", "swahili"]),
    ("தமிழ்",      ["ta", "tamil"]),
    ("తెలుగు",      ["te", "telugu"]),
    ("اردو",        ["ur", "urdu"]),
    ("Yorùbá",     ["yo", "yoruba"]),
    ("বাংলা",       ["bn", "bengali", "bangla"]),
    ("فارسی",      ["fa", "persian", "farsi"]),
    ("Українська", ["uk", "ukrainian"]),
    ("Melayu",     ["ms", "malay"]),
    ("Filipino",   ["fil", "filipino"]),
    ("Tagalog",    ["tl", "tagalog"]),
    ("Svenska",    ["sv", "swedish"]),
    ("Norsk",      ["no", "norwegian", "nb", "nn"]),
    ("Dansk",      ["da", "danish"]),
    ("Suomi",      ["fi", "finnish"]),
    ("Ελληνικά",   ["el", "greek"]),
    ("עברית",      ["he", "hebrew", "iw"]),
    ("Čeština",    ["cs", "czech"]),
    ("Română",     ["ro", "romanian"]),
    ("Magyar",     ["hu", "hungarian"]),
    ("Български",  ["bg", "bulgarian"]),
    ("Српски",     ["sr", "serbian"]),
    ("Hrvatski",   ["hr", "croatian"]),
    ("Slovenčina", ["sk", "slovak"]),
    ("Slovenščina",["sl", "slovenian"]),
    ("Lietuvių",   ["lt", "lithuanian"]),
    ("Latviešu",   ["lv", "latvian"]),
    ("Eesti",      ["et", "estonian"]),
]

# Build alias -> canonical map (lowercased aliases). The canonical name itself is always an alias.
ALIAS_MAP = {}
for canonical, aliases in NATIVE:
    ALIAS_MAP[canonical.casefold()] = canonical
    for a in aliases:
        ALIAS_MAP[a.casefold()] = canonical
        ALIAS_MAP[f"other_{a}".casefold()] = canonical


def resolve_target(folder_name: str) -> str | None:
    return ALIAS_MAP.get(folder_name.casefold())


def update_txt_language(txt: Path, lang_name: str) -> bool:
    try:
        text = txt.read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        print(f"    txt-read-fail {txt}: {e}", flush=True)
        return False
    if re.search(r"^Language:", text, re.MULTILINE):
        new_text = re.sub(r"^Language:\s*.*$", f"Language: {lang_name}", text, count=1, flags=re.MULTILINE)
    else:
        new_text = f"Language: {lang_name}\n" + text
    if new_text == text:
        return False
    try:
        txt.write_text(new_text, encoding="utf-8")
        return True
    except Exception as e:
        print(f"    txt-write-fail {txt}: {e}", flush=True)
        return False


def merge_or_rename(src: Path, dst: Path) -> tuple[int, int]:
    """Move src -> dst. If dst exists, merge contents in. Returns (moved, skipped_exists)."""
    if not dst.exists():
        src.rename(dst)
        print(f"RENAMED {src.name} -> {dst.name}", flush=True)
        return (0, 0)
    moved = 0
    skipped = 0
    for child in list(src.iterdir()):
        target = dst / child.name
        if target.exists():
            print(f"  SKIP-EXISTS {dst.name}/{child.name}", flush=True)
            skipped += 1
            continue
        shutil.move(str(child), str(target))
        moved += 1
    remaining = list(src.iterdir())
    if not remaining:
        src.rmdir()
        print(f"MERGED {src.name} -> {dst.name} (moved {moved})", flush=True)
    else:
        print(f"PARTIAL-MERGE {src.name} -> {dst.name} (moved {moved}, {len(remaining)} files left)", flush=True)
    return (moved, skipped)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root", help="Parent folder containing language subfolders")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    if not root.exists():
        print(f"ERR root not found: {root}", flush=True)
        sys.exit(1)

    subs = [p for p in root.iterdir() if p.is_dir()]
    print(f"Found {len(subs)} subfolders under {root}", flush=True)

    renamed = 0
    merged_into = 0
    skipped_exists = 0
    final_folders: list[Path] = []

    for sub in subs:
        target_name = resolve_target(sub.name)
        if target_name is None:
            print(f"SKIP-UNKNOWN {sub.name}", flush=True)
            final_folders.append(sub)
            continue
        if target_name == sub.name:
            print(f"OK {sub.name}", flush=True)
            final_folders.append(sub)
            continue
        target_dir = root / target_name
        existed_before = target_dir.exists()
        moved, sk = merge_or_rename(sub, target_dir)
        skipped_exists += sk
        if existed_before:
            merged_into += 1
        else:
            renamed += 1
        if target_dir not in final_folders:
            final_folders.append(target_dir)

    # Rewrite Language: lines
    print("\n=== Updating Language: lines ===", flush=True)
    txt_fixed = 0
    for folder in final_folders:
        if not folder.exists():
            continue
        lang_label = folder.name  # canonical native name now
        for txt in folder.glob("*.txt"):
            if update_txt_language(txt, lang_label):
                print(f"  FIXED {folder.name}/{txt.name}", flush=True)
                txt_fixed += 1

    print(
        f"\n=== Done. Renamed={renamed}  MergedInto={merged_into}  "
        f"SkippedExists={skipped_exists}  TxtFixed={txt_fixed} ===",
        flush=True,
    )


if __name__ == "__main__":
    main()
