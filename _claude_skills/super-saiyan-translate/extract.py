"""Phase 1: extract Whisper transcripts and write a JSONL ready for human/LLM translation.

Usage:
  PYTHONIOENCODING=utf-8 python -u extract.py <root> [--plan <csv>] [--whisper small|medium]
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace", line_buffering=True)

import argparse
import csv
import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

PROJECT = Path(r"D:/Dev/Tools/Video Translator")

FOLDER_TO_ISO = {
    "English": "en", "Tiếng Việt": "vi", "Português": "pt", "Español": "es",
    "Français": "fr", "Deutsch": "de", "Italiano": "it", "Русский": "ru",
    "日本語": "ja", "한국어": "ko", "中文": "zh", "ไทย": "th",
    "Indonesia": "id", "العربية": "ar", "Türkçe": "tr", "हिन्दी": "hi",
    "Nederlands": "nl", "Polski": "pl", "ខ្មែរ": "km", "മലയാളം": "ml",
    "မြန်မာ": "my", "नेपाली": "ne", "Kiswahili": "sw", "தமிழ்": "ta",
    "اردو": "ur", "Yorùbá": "yo",
}


def safe_angle_slug(angle: str) -> str:
    s = re.sub(r"[^\w\s\-/]", "", angle).strip().replace("/", "_").replace(" ", "_")
    return s


def get_duration(video: Path) -> float:
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(video)],
            capture_output=True, text=True, timeout=30,
        )
        return float(r.stdout.strip())
    except Exception:
        return 0.0


def extract_audio_to_wav(video: Path, out_wav: Path) -> bool:
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-i", str(video),
             "-vn", "-ac", "1", "-ar", "16000", "-f", "wav", str(out_wav)],
            check=True, capture_output=True, timeout=300,
        )
        return out_wav.exists() and out_wav.stat().st_size > 1000
    except Exception as e:
        print(f"    audio-fail {video.name}: {e}", flush=True)
        return False


def transcribe(model, video: Path, cache_file: Path) -> tuple[list[dict], str | None]:
    """Whisper full transcribe, cached as JSON."""
    if cache_file.exists():
        try:
            data = json.loads(cache_file.read_text(encoding="utf-8"))
            return data["segments"], data.get("language")
        except Exception:
            pass
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        wav = Path(f.name)
    try:
        if not extract_audio_to_wav(video, wav):
            return [], None
        segments_gen, info = model.transcribe(
            str(wav), beam_size=1, best_of=1, vad_filter=True, language=None,
        )
        segs = []
        for s in segments_gen:
            txt = (s.text or "").strip()
            if not txt:
                continue
            segs.append({"id": len(segs), "start": round(s.start, 3),
                         "end": round(s.end, 3), "text": txt, "translation_en": ""})
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(
            json.dumps({"language": info.language, "segments": segs}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return segs, info.language
    except Exception as e:
        print(f"    transcribe-fail {video.name}: {e}", flush=True)
        return [], None
    finally:
        try:
            wav.unlink()
        except Exception:
            pass


def load_plan(plan_csv: Path) -> dict[str, dict]:
    """Returns {filename: {angle, language}}."""
    out = {}
    if not plan_csv.exists():
        return out
    with plan_csv.open(encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            out[r["file"]] = r
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--plan", help="translate_plan.csv — if set, only extract for these files",
                    default="")
    ap.add_argument("--whisper", default="small", choices=["tiny", "base", "small", "medium", "large-v3"])
    args = ap.parse_args()

    root = Path(args.root).resolve()
    if not root.exists():
        print(f"ERR root not found: {root}", flush=True)
        sys.exit(1)

    out_dir = root / "_super_saiyan"
    out_dir.mkdir(exist_ok=True)
    cache_dir = out_dir / "_whisper_cache"
    cache_dir.mkdir(exist_ok=True)
    jsonl_path = out_dir / "translations.jsonl"

    # Load existing JSONL for idempotent merge
    existing: dict[str, dict] = {}
    if jsonl_path.exists():
        for line in jsonl_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                existing[d["file"]] = d
            except Exception:
                pass
    print(f"[init] existing entries: {len(existing)}", flush=True)

    # Decide which files to process
    plan = load_plan(Path(args.plan)) if args.plan else {}
    work: list[tuple[Path, str, str]] = []  # (video, language_folder, angle)
    if plan:
        for file, row in plan.items():
            lang_folder = row["language"]
            if lang_folder == "English":
                continue
            mp4 = root / lang_folder / file
            if not mp4.exists():
                print(f"  SKIP missing {mp4}", flush=True)
                continue
            work.append((mp4, lang_folder, row.get("angle", "")))
    else:
        for sub in root.iterdir():
            if not sub.is_dir() or sub.name.startswith("_") or sub.name == "English":
                continue
            for mp4 in sorted(sub.glob("*.mp4")):
                work.append((mp4, sub.name, ""))

    print(f"[init] {len(work)} non-EN videos to transcribe", flush=True)

    # Load Whisper
    from faster_whisper import WhisperModel
    print(f"[whisper] loading '{args.whisper}' (CPU int8) ...", flush=True)
    model = WhisperModel(args.whisper, device="cpu", compute_type="int8")
    print("[whisper] ready", flush=True)

    t0 = time.time()
    new_or_updated = 0
    for idx, (mp4, lang_folder, angle) in enumerate(work, 1):
        iso = FOLDER_TO_ISO.get(lang_folder)
        cache = cache_dir / f"{mp4.stem}.{args.whisper}.{iso or 'auto'}.transcript.json"
        elapsed = time.time() - t0
        eta = (elapsed / idx) * (len(work) - idx) if idx > 1 else 0
        print(f"[{idx}/{len(work)}] elapsed={elapsed:.0f}s eta={eta:.0f}s  {lang_folder}/{mp4.name}", flush=True)
        segs, det_lang = transcribe(model, mp4, cache)
        if not segs:
            print(f"    no segments — skipping", flush=True)
            continue

        # Merge with existing — preserve any pre-filled translation_en
        prev = existing.get(mp4.name)
        if prev:
            prev_map = {s["id"]: s.get("translation_en", "") for s in prev.get("segments", [])}
            for s in segs:
                if not s["translation_en"]:
                    s["translation_en"] = prev_map.get(s["id"], "")

        entry = {
            "file": mp4.name,
            "source_path": str(mp4),
            "language_folder": lang_folder,
            "source_lang": iso or det_lang,
            "detected_lang": det_lang,
            "angle": angle,
            "out_path": str(root / "_translated_en" / safe_angle_slug(angle or lang_folder) / f"{mp4.stem}_EN.mp4"),
            "video_duration": get_duration(mp4),
            "segments": segs,
        }
        existing[mp4.name] = entry
        new_or_updated += 1

    # Write JSONL
    with jsonl_path.open("w", encoding="utf-8") as f:
        for file in sorted(existing):
            f.write(json.dumps(existing[file], ensure_ascii=False) + "\n")

    print(f"\n=== Done. {new_or_updated} entries updated; {len(existing)} total in {jsonl_path}", flush=True)
    incomplete = [d["file"] for d in existing.values()
                  if any(not s.get("translation_en", "").strip() for s in d["segments"])]
    print(f"Entries needing translation: {len(incomplete)}", flush=True)
    print(f"Time: {time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
