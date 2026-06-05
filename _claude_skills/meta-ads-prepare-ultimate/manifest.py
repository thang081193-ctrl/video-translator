"""manifest.py — the backbone of meta-ads-prepare-ultimate.

ONE Whisper pass per video produces the master manifest that every downstream
step reuses (organize / dub / brandpass / package). No step ever re-runs Whisper.

Per video the scan stores:
  - duration, whisper_lang (auto-detected)
  - has_voice  (the brand_pass speech-gate: avg_logprob > -0.5, speech >= max(1.5s, 5%))
  - transcript (gated, native-language joined text)
  - segments   [{id,start,end,text,translation}]  (native text; translation filled by Opus)

Opus then fills (by editing manifest.json directly):
  language, language_folder, lang_code, angle, hook, headlines[], primary_texts[],
  bgm_cluster, and segments[].translation for has_voice videos that need dubbing.

Downstream steps fill: organized_path, renamed, dubbed_path, output_path.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from pathlib import Path

# Same gate constants as pipeline.brand_pass._transcribe_video — keep in sync.
LOGPROB_GATE = -0.5
MIN_SPEECH_ABS = 1.5
MIN_SPEECH_PCT = 0.05


def manifest_path(src_root: Path) -> Path:
    return src_root / "_ultimate" / "manifest.json"


def load_manifest(src_root: Path) -> dict:
    p = manifest_path(src_root)
    if p.exists():
        return _migrate(json.loads(p.read_text(encoding="utf-8")))
    return {"src_root": str(src_root), "videos": []}


def _migrate(data: dict) -> dict:
    """Normalize older single-language manifests to the multi-language schema.

    Old segments carried a single `translation: str`; new ones carry
    `translations: {iso: text}`. Pre-Opus the old value was always empty, so we
    just drop it and seed an empty dict. Also seeds `dubbed_outputs`/`outputs`.
    """
    for v in data.get("videos", []):
        for s in v.get("segments", []):
            if "translations" not in s:
                old = s.pop("translation", None)
                # Pre-Opus translation was "" — nothing to preserve.
                s["translations"] = {} if not old else {"_legacy": old}
        # Old single-target fields -> dicts (only if present and not migrated).
        if "dubbed_path" in v and "dubbed_outputs" not in v:
            v["dubbed_outputs"] = {}
        if "output_path" in v and "outputs" not in v:
            v["outputs"] = {}
    return data


def save_manifest(src_root: Path, data: dict) -> None:
    p = manifest_path(src_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    # Atomic write: never leave a half-written manifest if two writers overlap.
    tmp = p.with_suffix(f".tmp{os.getpid()}")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, p)


def merge_save(src_root: Path, my_videos: list[dict], fields: tuple[str, ...]) -> dict:
    """Reload the on-disk manifest and merge the given per-video FIELDS from our
    in-memory videos, then save atomically.

    dub (`dubbed_outputs`) and the per-vertical brandpass (`outputs`) each only
    own a slice of the manifest. Holding the whole dict in memory and writing it
    back lets a concurrent sibling run (e.g. the other vertical) clobber the
    fields we didn't touch. Reloading + merging just-our-fields makes those runs
    safe to overlap. Dict fields are unioned; scalar fields overwrite.
    """
    disk = load_manifest(src_root)
    dby = index_by_id(disk)
    for v in my_videos:
        d = dby.get(v["id"])
        if not d:
            continue
        for f in fields:
            val = v.get(f)
            if isinstance(val, dict):
                if val:
                    d.setdefault(f, {}).update(val)
            elif val is not None:
                d[f] = val
    save_manifest(src_root, disk)
    return disk


def index_by_id(data: dict) -> dict[str, dict]:
    return {v["id"]: v for v in data.get("videos", [])}


def video_id(mp4: Path) -> str:
    """Stable id for a source video (original stem; collisions get parent tag)."""
    return mp4.stem


def collect_videos(src_root: Path) -> list[Path]:
    """Flat *.mp4 children + one level of subfolders. Skips _* work folders."""
    vids: list[Path] = []
    vids += sorted(src_root.glob("*.mp4"))
    for sub in sorted(src_root.iterdir()):
        if sub.is_dir() and not sub.name.startswith("_"):
            vids += sorted(sub.glob("*.mp4"))
    return vids


def ffprobe_duration(video: Path) -> float:
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(video)],
            capture_output=True, text=True, timeout=30,
        )
        return round(float(r.stdout.strip()), 3)
    except Exception:
        return 0.0


def extract_wav(video: Path, out_wav: Path) -> bool:
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-i", str(video),
             "-vn", "-ac", "1", "-ar", "16000", "-f", "wav", str(out_wav)],
            check=True, capture_output=True, timeout=300,
        )
        return out_wav.exists() and out_wav.stat().st_size > 1000
    except Exception:
        return False


def transcribe_one(model, video: Path) -> dict:
    """Single Whisper pass (auto language) + speech gate.

    Returns {whisper_lang, has_voice, transcript, segments[]}.
    segments keep the NATIVE-language text so Opus can translate them later.
    """
    dur = ffprobe_duration(video)
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        wav = Path(f.name)
    try:
        if not extract_wav(video, wav):
            return {"whisper_lang": None, "has_voice": False,
                    "transcript": "", "segments": [], "duration": dur}
        seg_gen, info = model.transcribe(
            str(wav), beam_size=1, best_of=1, vad_filter=True, language=None,
        )
        raw = list(seg_gen)
        # Speech gate — identical logic to brand_pass._transcribe_video.
        kept = [s for s in raw if (s.text or "").strip() and s.avg_logprob > LOGPROB_GATE]
        speech_dur = sum(s.end - s.start for s in kept)
        min_speech = max(MIN_SPEECH_ABS, dur * MIN_SPEECH_PCT)
        has_voice = speech_dur >= min_speech
        segments = []
        if has_voice:
            for s in kept:
                segments.append({
                    "id": len(segments),
                    "start": round(s.start, 3),
                    "end": round(s.end, 3),
                    "text": (s.text or "").strip(),
                    "translations": {},
                })
        transcript = " ".join(s["text"] for s in segments).strip()
        return {
            "whisper_lang": info.language,
            "has_voice": has_voice,
            "transcript": transcript,
            "segments": segments,
            "duration": dur,
        }
    finally:
        try:
            wav.unlink()
        except Exception:
            pass


def load_whisper(model_size: str = "small"):
    from faster_whisper import WhisperModel
    print(f"[whisper] loading '{model_size}' (CPU int8) ...", flush=True)
    m = WhisperModel(model_size, device="cpu", compute_type="int8")
    print("[whisper] ready", flush=True)
    return m


# Fields filled by Opus (never overwritten by a re-scan).
#   vertical       hair|home|skip — which app/vertical this video belongs to
#   language/...   confirmed source language (Opus reads transcript, not folder)
#   copy           {iso: {headlines:[], primary_texts:[]}} — localized per target
#   outro_variant  man|woman (hair talking-head videos) — drives outro routing
OPUS_FIELDS = ("vertical", "language", "language_folder", "lang_code",
               "angle", "hook", "copy", "headlines", "primary_texts",
               "bgm_cluster", "outro_variant")


def scan(src_root: Path, model_size: str = "small") -> dict:
    """Build/refresh the manifest. Idempotent: re-transcribes only NEW videos,
    preserves Opus-filled fields and any existing translations."""
    data = load_manifest(src_root)
    existing = index_by_id(data)
    videos = collect_videos(src_root)
    print(f"[scan] {len(videos)} videos under {src_root}", flush=True)

    todo = []
    for mp4 in videos:
        vid = video_id(mp4)
        prev = existing.get(vid)
        # Re-transcribe only if new or the source path/size changed.
        if prev and prev.get("transcript_done") and Path(prev.get("src_path", "")).exists():
            continue
        todo.append(mp4)

    if not todo:
        print("[scan] nothing new to transcribe; manifest up to date", flush=True)
        save_manifest(src_root, data)
        return data

    model = load_whisper(model_size)
    t0 = time.time()
    for i, mp4 in enumerate(todo, 1):
        vid = video_id(mp4)
        elapsed = time.time() - t0
        eta = (elapsed / i) * (len(todo) - i) if i > 1 else 0
        res = transcribe_one(model, mp4)
        prev = existing.get(vid, {})
        entry = {
            "id": vid,
            "src_path": str(mp4),
            "orig_name": mp4.name,
            "duration": res["duration"],
            "whisper_lang": res["whisper_lang"],
            "has_voice": res["has_voice"],
            "transcript": res["transcript"],
            "segments": res["segments"],
            "transcript_done": True,
        }
        # Preserve Opus-filled fields + prior translations across re-scans.
        for k in OPUS_FIELDS:
            if prev.get(k):
                entry[k] = prev[k]
        if prev.get("segments"):
            prev_tr = {s["id"]: s.get("translations", {}) for s in prev["segments"]}
            for s in entry["segments"]:
                if not s["translations"]:
                    s["translations"] = dict(prev_tr.get(s["id"], {}))
        # Carry forward downstream dicts so re-scan never drops dub/output state.
        for k in ("dubbed_outputs", "outputs", "renamed", "organized_path"):
            if prev.get(k) and not entry.get(k):
                entry[k] = prev[k]
        existing[vid] = entry
        tag = "VOICE" if res["has_voice"] else "bgm  "
        print(f"[{i}/{len(todo)}] {tag} lang={res['whisper_lang']} "
              f"elapsed={elapsed:.0f}s eta={eta:.0f}s  {mp4.name}", flush=True)

    data["videos"] = [existing[v] for v in sorted(existing)]
    save_manifest(src_root, data)
    voice_n = sum(1 for v in data["videos"] if v["has_voice"])
    print(f"\n[scan] done: {voice_n} VOICE / {len(data['videos'])-voice_n} bgm-only "
          f"→ {manifest_path(src_root)}", flush=True)
    return data
