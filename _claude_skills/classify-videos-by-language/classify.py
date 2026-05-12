"""Detect spoken language in videos and reorganize into per-language folders.

Usage:
  PYTHONIOENCODING=utf-8 python -u classify.py <root_folder> [--model small]

Walks <root>/*.mp4 and <root>/*/*.mp4, detects language via faster-whisper on a
15-second audio sample, then moves each video (and any sibling .txt) to the
matching language subfolder. Rewrites the `Language:` field of the .txt to
match the new folder.

Emits one log line per video for live progress (line-buffered, UTF-8 safe).
"""
import sys
import io

# UTF-8 stdout/stderr — folder names contain CJK / Vietnamese / Portuguese.
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace", line_buffering=True)

import argparse
import re
import shutil
import subprocess
import tempfile
import time
from collections import Counter
from pathlib import Path

LANG_FOLDERS = {
    "en": "English",
    "vi": "Tiếng Việt",
    "pt": "Português",
    "es": "Español",
    "fr": "Français",
    "de": "Deutsch",
    "it": "Italiano",
    "ru": "Русский",
    "ja": "日本語",
    "ko": "한국어",
    "zh": "中文",
    "th": "ไทย",
    "id": "Indonesia",
    "ar": "العربية",
    "tr": "Türkçe",
    "hi": "हिन्दी",
    "nl": "Nederlands",
    "pl": "Polski",
    "km": "ខ្មែរ",
    "ml": "മലയാളം",
    "my": "မြန်မာ",
    "ne": "नेपाली",
    "sw": "Kiswahili",
    "ta": "தமிழ்",
    "ur": "اردو",
    "yo": "Yorùbá",
    "bn": "বাংলা",
    "fa": "فارسی",
    "uk": "Українська",
    "ms": "Melayu",
    "fil": "Filipino",
    "tl": "Tagalog",
    "sv": "Svenska",
    "no": "Norsk",
    "da": "Dansk",
    "fi": "Suomi",
    "el": "Ελληνικά",
    "he": "עברית",
    "cs": "Čeština",
    "ro": "Română",
    "hu": "Magyar",
    "bg": "Български",
    "sr": "Српски",
    "hr": "Hrvatski",
    "sk": "Slovenčina",
    "sl": "Slovenščina",
    "lt": "Lietuvių",
    "lv": "Latviešu",
    "et": "Eesti",
}


def lang_to_folder(code):
    if not code:
        return "Unknown"
    return LANG_FOLDERS.get(code, f"Other_{code}")


def extract_audio(video, out_wav, duration=15):
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-ss", "2", "-t", str(duration),
        "-i", str(video),
        "-vn", "-ac", "1", "-ar", "16000", "-f", "wav",
        str(out_wav),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=60)
        return out_wav.exists() and out_wav.stat().st_size > 1000
    except Exception as e:
        print(f"    ffmpeg-fail: {e}", flush=True)
        return False


def detect_lang(model, video):
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        wav = Path(f.name)
    try:
        if not extract_audio(video, wav):
            return None
        segments, info = model.transcribe(
            str(wav),
            beam_size=1,
            best_of=1,
            vad_filter=True,
            language=None,
        )
        _ = list(segments)  # force info population
        return info.language
    except Exception as e:
        print(f"    detect-fail: {e}", flush=True)
        return None
    finally:
        try:
            wav.unlink()
        except Exception:
            pass


def update_txt_language(txt_path, new_lang):
    try:
        text = txt_path.read_text(encoding="utf-8", errors="ignore")
        if re.search(r"^Language:", text, re.MULTILINE):
            text = re.sub(r"^Language:\s*.*$", f"Language: {new_lang}", text, count=1, flags=re.MULTILINE)
        else:
            text = f"Language: {new_lang}\n" + text
        txt_path.write_text(text, encoding="utf-8")
    except Exception as e:
        print(f"    txt-update-fail: {e}", flush=True)


def load_model(model_size):
    from faster_whisper import WhisperModel
    print(f"Loading Whisper '{model_size}' on CPU (int8) ...", flush=True)
    m = WhisperModel(model_size, device="cpu", compute_type="int8")
    print("  model loaded", flush=True)
    return m


def collect_videos(root):
    out = []
    # Direct children
    for mp4 in root.glob("*.mp4"):
        out.append(mp4)
    # One-level subfolders
    for sub in root.iterdir():
        if sub.is_dir():
            for mp4 in sub.glob("*.mp4"):
                out.append(mp4)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root", help="Parent folder containing videos / language subfolders")
    ap.add_argument("--model", default="small", help="Whisper model size (default: small)")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    if not root.exists():
        print(f"ERR root not found: {root}", flush=True)
        sys.exit(1)

    videos = collect_videos(root)
    print(f"Found {len(videos)} videos under {root}", flush=True)
    if not videos:
        return

    model = load_model(args.model)

    plan = []  # (video, current_folder, target_folder, lang_code)
    t0 = time.time()
    for idx, mp4 in enumerate(videos, 1):
        current = mp4.parent.name if mp4.parent != root else "(root)"
        elapsed = time.time() - t0
        eta = (elapsed / idx) * (len(videos) - idx) if idx else 0
        lang_code = detect_lang(model, mp4)
        target = lang_to_folder(lang_code)
        print(
            f"[{idx}/{len(videos)}] elapsed={elapsed:5.1f}s eta={eta:5.1f}s "
            f"{mp4.name}  current={current}  detected={lang_code}  target={target}",
            flush=True,
        )
        plan.append((mp4, current, target, lang_code))

    print("\n=== Applying moves ===", flush=True)
    moved = 0
    for mp4, current, target, code in plan:
        target_dir = root / target
        if current == target:
            txt = mp4.with_suffix(".txt")
            if txt.exists() and code:
                update_txt_language(txt, target)
            continue
        target_dir.mkdir(exist_ok=True)
        try:
            new_mp4 = target_dir / mp4.name
            shutil.move(str(mp4), str(new_mp4))
            txt = mp4.with_suffix(".txt")
            if txt.exists():
                new_txt = target_dir / txt.name
                shutil.move(str(txt), str(new_txt))
                if code:
                    update_txt_language(new_txt, target)
            print(f"  MOVED {current} -> {target}: {mp4.name}", flush=True)
            moved += 1
        except Exception as e:
            print(f"  MOVE-FAIL {mp4.name}: {e}", flush=True)

    # Summary
    counts = Counter(target for _, _, target, _ in plan)
    fails = [mp4.name for mp4, _, _, code in plan if code is None]
    print(f"\n=== Done. Total={len(plan)}  Moved={moved}  DetectFails={len(fails)} ===", flush=True)
    for folder, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {folder}: {n}", flush=True)
    if fails:
        print(f"\nDetection failures:", flush=True)
        for name in fails:
            print(f"  {name}", flush=True)


if __name__ == "__main__":
    main()
