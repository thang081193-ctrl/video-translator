"""detect_voice.py — classify each source video as VOICE vs BGM-only using the
exact Whisper speech-gate from pipeline.brand_pass (_transcribe_video), so the
campaign-split step can route voiced clips to a re-dub path and music-only clips
to a BGM-swap path.

Usage:
    python detect_voice.py <src_root> [--out voice_manifest.csv]

<src_root> = flat folder of *.mp4 or one level of <lang>/*.mp4.
Output CSV columns: lang,file,has_voice,speech_chars,transcript
"""
import argparse, csv, os, sys, glob
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

REPO = Path(os.environ.get("VIDEO_TRANSLATOR_ROOT", r"D:/Dev/Tools/Video Translator"))
sys.path.insert(0, str(REPO))
from pipeline.brand_pass import _transcribe_video  # noqa: E402


def collect(src_root: Path):
    vids = []
    flat = sorted(src_root.glob("*.mp4"))
    if flat:
        for m in flat:
            vids.append(("", m))
    for sub in sorted(src_root.iterdir()):
        if sub.is_dir() and not sub.name.startswith("_"):
            for m in sorted(sub.glob("*.mp4")):
                vids.append((sub.name, m))
    return vids


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src_root")
    ap.add_argument("--out", default="voice_manifest.csv")
    a = ap.parse_args()

    src_root = Path(a.src_root).resolve()
    vids = collect(src_root)
    out_path = src_root / a.out
    print(f"[detect] {len(vids)} videos under {src_root}", flush=True)

    rows = []
    voice_n = 0
    for i, (lang, mp4) in enumerate(vids, 1):
        try:
            text = _transcribe_video(str(mp4))
        except Exception as e:
            text = ""
            print(f"  ERR {mp4.name}: {e}", flush=True)
        has_voice = bool(text and text.strip())
        voice_n += has_voice
        rows.append({
            "lang": lang,
            "file": mp4.name,
            "has_voice": "yes" if has_voice else "no",
            "speech_chars": len(text),
            "transcript": text[:300],
        })
        print(f"[{i}/{len(vids)}] {'VOICE' if has_voice else 'bgm  '} {mp4.name}", flush=True)

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["lang", "file", "has_voice", "speech_chars", "transcript"])
        w.writeheader()
        w.writerows(rows)
    print(f"\n[detect] done: {voice_n} VOICE / {len(vids)-voice_n} bgm-only → {out_path}", flush=True)


if __name__ == "__main__":
    main()
