"""Rename video files to <LANG>_<DDMM><NN>.mp4 inside per-language folders.

Usage:
  PYTHONIOENCODING=utf-8 python -u rename.py <root> [--dry-run]

Walks <root>/<lang-folder>/*.mp4, derives LANG from the folder name, DDMM from
the timestamp embedded in the original filename, and assigns a 2-digit (or
3-digit when needed) sequence number per (lang, date) bucket.

Renames the .mp4 and any companion .txt (same stem) atomically. The .txt body
is not modified — only its filename.
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace", line_buffering=True)

import argparse
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path

FOLDER_TO_CODE = {
    "English": "EN",
    "Tiếng Việt": "VI",
    "Português": "PT",
    "Español": "ES",
    "Français": "FR",
    "Deutsch": "DE",
    "Italiano": "IT",
    "Русский": "RU",
    "日本語": "JA",
    "한국어": "KO",
    "中文": "ZH",
    "ไทย": "TH",
    "Indonesia": "ID",
    "العربية": "AR",
    "Türkçe": "TR",
    "हिन्दी": "HI",
    "Nederlands": "NL",
    "Polski": "PL",
    "ខ្មែរ": "KM",
    "മലയാളം": "ML",
    "မြန်မာ": "MY",
    "नेपाली": "NE",
    "Kiswahili": "SW",
    "தமிழ்": "TA",
    "اردو": "UR",
    "Yorùbá": "YO",
    "বাংলা": "BN",
    "فارسی": "FA",
    "Українська": "UK",
    "Melayu": "MS",
    "Filipino": "FIL",
    "Tagalog": "TL",
    "Svenska": "SV",
    "Norsk": "NO",
    "Dansk": "DA",
    "Suomi": "FI",
    "Ελληνικά": "EL",
    "עברית": "HE",
    "Čeština": "CS",
    "Română": "RO",
    "Magyar": "HU",
    "Български": "BG",
    "Српски": "SR",
    "Hrvatski": "HR",
    "Slovenčina": "SK",
    "Slovenščina": "SL",
    "Lietuvių": "LT",
    "Latviešu": "LV",
    "Eesti": "ET",
}

# Match _YYYYMMDDTHHMMSS in filename. Returns datetime or None.
TS_RE = re.compile(r"_(\d{8})T(\d{6})")


def parse_ts(filename: str) -> datetime | None:
    m = TS_RE.search(filename)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
    except ValueError:
        return None


def get_date_for_video(mp4: Path) -> datetime | None:
    ts = parse_ts(mp4.name)
    if ts:
        return ts
    try:
        return datetime.fromtimestamp(mp4.stat().st_mtime)
    except Exception:
        return None


def ddmm(dt: datetime) -> str:
    return dt.strftime("%d%m")


RENAMED_RE = re.compile(r"^([A-Z]{2,3})_(\d{6,})$")


def already_renamed(stem: str, code: str, ddmm_str: str) -> int | None:
    """If stem matches <CODE>_<DDMM><nn>, return nn; else None."""
    m = RENAMED_RE.match(stem)
    if not m:
        return None
    if m.group(1) != code:
        return None
    digits = m.group(2)
    if not digits.startswith(ddmm_str):
        return None
    tail = digits[len(ddmm_str):]
    if not tail.isdigit():
        return None
    return int(tail)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    if not root.exists():
        print(f"ERR root not found: {root}", flush=True)
        sys.exit(1)

    total_renamed = 0
    total_kept = 0
    total_skipped = 0
    total_collisions = 0

    for sub in sorted(root.iterdir()):
        if not sub.is_dir():
            continue
        code = FOLDER_TO_CODE.get(sub.name)
        if not code:
            print(f"SKIP-UNKNOWN-LANG {sub.name}", flush=True)
            total_skipped += sum(1 for _ in sub.glob("*.mp4"))
            continue

        # Bucket videos by DDMM
        buckets: dict[str, list[tuple[datetime, Path]]] = defaultdict(list)
        for mp4 in sub.glob("*.mp4"):
            dt = get_date_for_video(mp4)
            if dt is None:
                print(f"  SKIP-NODATE {sub.name}/{mp4.name}", flush=True)
                total_skipped += 1
                continue
            buckets[ddmm(dt)].append((dt, mp4))

        for ddmm_str, entries in sorted(buckets.items()):
            entries.sort(key=lambda x: (x[0], x[1].name))

            # Pre-pass: gather already-renamed numbers to reserve them.
            reserved: set[int] = set()
            keep_map: dict[Path, int] = {}
            for dt, mp4 in entries:
                n = already_renamed(mp4.stem, code, ddmm_str)
                if n is not None:
                    reserved.add(n)
                    keep_map[mp4] = n

            width = 2 if len(entries) <= 99 else 3
            # If any reserved number doesn't fit width, widen.
            if reserved and max(reserved) >= 10**width:
                width = max(width, len(str(max(reserved))))

            # Assign new numbers to non-reserved videos.
            taken = set(reserved)
            new_assignments: list[tuple[Path, int]] = []
            counter = 1
            for dt, mp4 in entries:
                if mp4 in keep_map:
                    continue
                while counter in taken:
                    counter += 1
                new_assignments.append((mp4, counter))
                taken.add(counter)
                counter += 1

            # Apply: kept first (just log), then renames.
            for mp4, n in keep_map.items():
                print(f"  KEEP {sub.name}/{mp4.name} (already #{n:0{width}d})", flush=True)
                total_kept += 1

            for mp4, n in new_assignments:
                target_stem = f"{code}_{ddmm_str}{n:0{width}d}"
                target_mp4 = sub / f"{target_stem}.mp4"
                target_txt = sub / f"{target_stem}.txt"
                if target_mp4.exists():
                    # Collision: append _dup<k>
                    k = 1
                    while (sub / f"{target_stem}_dup{k}.mp4").exists():
                        k += 1
                    print(f"  COLLISION {target_mp4.name} already exists -> using _dup{k}", flush=True)
                    target_stem = f"{target_stem}_dup{k}"
                    target_mp4 = sub / f"{target_stem}.mp4"
                    target_txt = sub / f"{target_stem}.txt"
                    total_collisions += 1

                src_txt = mp4.with_suffix(".txt")
                action = "DRY-RUN" if args.dry_run else "RENAMED"
                print(f"  {action} {sub.name}/{mp4.name} -> {target_mp4.name}", flush=True)
                if not src_txt.exists():
                    print(f"    NO-TXT {mp4.name}", flush=True)

                if not args.dry_run:
                    mp4.rename(target_mp4)
                    if src_txt.exists():
                        src_txt.rename(target_txt)
                total_renamed += 1

    print(
        f"\n=== Done. Renamed={total_renamed} Kept={total_kept} "
        f"Skipped={total_skipped} Collisions={total_collisions} "
        f"{'(dry-run)' if args.dry_run else ''} ===",
        flush=True,
    )


if __name__ == "__main__":
    main()
