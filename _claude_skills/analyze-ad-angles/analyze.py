"""Analyze ad videos and group them into reusable creative angles.

Usage:
  PYTHONIOENCODING=utf-8 python -u analyze.py <root>

Reads <root>/<lang>/*.mp4, extracts transcript + 3 keyframes, asks Gemini 2.5
Flash to label each video, consolidates into 8-12 canonical angles, writes
analysis.csv and angles_x_languages.csv into <root>.

Caches per-video JSON in <root>/_angle_cache/. Maintains a global taxonomy at
~/.claude/skills/analyze-ad-angles/taxonomy.json (reused across daily runs).
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace", line_buffering=True)

import argparse
import csv
import hashlib
import itertools
import json
import os
import re
import subprocess
import tempfile
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# ----------------------------- Config ---------------------------------------

PROJECT_ROOT = Path(r"D:\Dev\Tools\Video Translator")
ENV_FILE = PROJECT_ROOT / ".env"
TAXONOMY_FILE = Path(r"C:\Users\Thang\.claude\skills\analyze-ad-angles\taxonomy.json")

WHISPER_MODEL = "small"
GEMINI_MODELS = [
    m.strip() for m in os.environ.get(
        "GEMINI_MODELS",
        "gemini-2.5-flash,gemini-2.5-flash-lite,gemini-2.0-flash,gemini-2.0-flash-lite,gemini-flash-latest,gemini-flash-lite-latest,gemini-2.5-pro"
    ).split(",") if m.strip()
]
CACHE_DIRNAME = "_angle_cache"

PER_VIDEO_SYSTEM = """You are a senior performance marketer analyzing short-form video ads
for campaign-brief planning (Google Ads / Meta Ads / TikTok Ads).

Your job is to identify the CREATIVE ANGLE of each ad. An angle is NOT the product
category — it is the specific HOOK, EMOTION, and PROMISE the ad uses to stop the
scroll and drive a click. Two ads for the same app can use completely different
angles.

DEFINITION OF ANGLE
- 3-6 word noun phrase naming the HOOK or PROMISE.
- Focused on WHY a viewer stops scrolling — curiosity, transformation reveal,
  FOMO, fantasy, social proof, pain solved, trend bandwagon, value/cost.
- Independent of the product. "Before/After Transformation" is an angle.
  "AI Photo Editor" is a product category — that is NOT an angle.

GOOD ANGLE EXAMPLES (use one of these or a similar hook-style label)
- "Before/After Transformation Reveal"
- "See Your Future Self"
- "Age Progression Reveal"
- "Match Outfit with Partner"
- "Couple/Romance Aesthetic"
- "Family Photo Reunion Edit"
- "Become Anime/Cartoon Character"
- "Pet → Human Transformation"
- "Hairstyle Makeover Try-On"
- "AI Makeup Personal Diagnosis"
- "Wedding/Glow-up Reveal"
- "Old Photo Restoration Reveal"
- "CapCut Trend Template Tutorial"
- "Viral Trend Recreation"
- "UGC Reaction / POV Hook"
- "Testimonial of Real Result"
- "Curiosity Gap — What Would I Look Like If…"
- "Easy in 3 Steps Promise"
- "Free / Unlimited Generations"
- "1-Tap Magic Demo"
- "AI Knows You Better Than You"
- "Surprise / Shock Reveal"
- "Aspirational Aesthetic Lifestyle"

BAD ANGLE LABELS (do NOT output these — they are product categories)
- "AI Photo Editor"
- "AI Photo Generation App"
- "AI Photo Transformation App"
- "Photo Editing Tutorial"
- "AI Beauty App"
- "Mobile Photo App"

Return STRICT JSON only — no prose, no markdown fences:

{
  "angle_hypothesis": "3-6 word hook-style angle (NOT a product category)",
  "angle_reason": "1 sentence — why this is the angle, what emotion/hook it triggers",
  "hook_line": "verbatim opening hook from transcript (or '' if none)",
  "core_promise": "one short sentence — what the viewer is promised",
  "emotional_driver": "curiosity | aspiration | fomo | social-proof | pain-relief | playfulness | nostalgia | romance | empowerment | trend | value",
  "format": "before-after | screen-demo | talking-head | montage | trend-remix | testimonial | ugc-style | demo-walkthrough | other",
  "persona": "2-5 word primary target persona",
  "best_platform": "tiktok | meta | google | universal",
  "confidence": 0.0,
  "notes": "1 short sentence on the distinct creative twist or differentiator"
}

confidence is in [0,1] reflecting how sure you are about the angle label.
"""

PER_VIDEO_SYSTEM_WITH_TAXONOMY = PER_VIDEO_SYSTEM + """

You are given an existing taxonomy of canonical angles below. You MUST choose
angle_hypothesis from this list verbatim when one fits. Only invent a new label
(and lower confidence to <0.6) when no canonical angle fits.

TAXONOMY:
{taxonomy_block}
"""

CONSOLIDATE_SYSTEM = """You consolidate raw creative-angle labels for ad videos into a canonical
taxonomy for campaign-brief planning.

CRITICAL: An "angle" is the HOOK / EMOTION / PROMISE the ad uses to stop the
scroll, NOT the product category. Two ads for the same app can have different
angles. Do NOT collapse different hooks into one cluster just because they
share a product feature.

Input: a list of free-form angle labels.
Task: cluster them into 10-15 canonical creative angles. Each canonical angle MUST:
- Be a 3-6 word noun phrase naming the HOOK or PROMISE (not the product).
- Be actionable as a distinct creative brief — different opening shot,
  different copy, different emotional trigger.
- Group at least 3 input labels.
- NOT be a product category like "AI Photo Editor" or "Photo Editing App".

VALID CANONICAL ANGLES (model after these):
- "Before/After Transformation Reveal"
- "See Your Future Self"
- "Age Progression Reveal"
- "Couple/Partner Match Aesthetic"
- "Family/Group Photo Reunion"
- "Become Anime / Cartoon Character"
- "Hairstyle Makeover Try-On"
- "AI Makeup Personal Diagnosis"
- "Wedding / Glow-up Reveal"
- "Old Photo Restoration"
- "CapCut Trend Template Tutorial"
- "Viral Trend Recreation"
- "UGC Reaction / POV Hook"
- "Curiosity Gap Hook"
- "Easy 3-Step Promise"
- "Free / Unlimited Value"
- "1-Tap Magic Demo"
- "Aspirational Aesthetic Lifestyle"
- "Surprise / Shock Reveal"

INVALID (DO NOT EMIT):
- "AI Photo Editor", "AI Photo Generator", "Photo Editing App",
  "AI Beauty App", "Mobile Photo Tutorial" — these are products, not angles.

Return STRICT JSON only — no prose, no markdown:

{
  "angles": [
    {
      "name": "canonical angle (hook-style, 3-6 words)",
      "description": "1 sentence — the hook this angle uses and why it works",
      "signal_keywords": ["3-8 phrases that signal this angle"]
    }
  ],
  "mapping": {
    "<raw input label>": "<canonical angle name>"
  }
}

Every input label MUST appear as a key in `mapping`. Use the exact input strings.
"""

# ----------------------------- Env loading ----------------------------------

def load_env():
    if not ENV_FILE.exists():
        return
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


# ----------------------------- Whisper --------------------------------------

_whisper_lock = threading.Lock()
_whisper_model = None


def get_whisper():
    global _whisper_model
    with _whisper_lock:
        if _whisper_model is None:
            from faster_whisper import WhisperModel
            print(f"[whisper] loading {WHISPER_MODEL} (CPU int8) ...", flush=True)
            _whisper_model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
            print("[whisper] ready", flush=True)
    return _whisper_model


def extract_audio(video: Path, out_wav: Path, duration: int = 60) -> bool:
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-ss", "0", "-t", str(duration),
        "-i", str(video),
        "-vn", "-ac", "1", "-ar", "16000", "-f", "wav",
        str(out_wav),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=120)
        return out_wav.exists() and out_wav.stat().st_size > 1000
    except Exception as e:
        print(f"  audio-fail {video.name}: {e}", flush=True)
        return False


def transcribe(video: Path) -> tuple[str, str | None]:
    """Returns (transcript_text, detected_lang)."""
    model = get_whisper()
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        wav = Path(f.name)
    try:
        if not extract_audio(video, wav):
            return ("", None)
        segments, info = model.transcribe(
            str(wav),
            beam_size=1,
            best_of=1,
            vad_filter=True,
            language=None,
        )
        text = " ".join(s.text.strip() for s in segments if s.text)
        return (text.strip(), info.language)
    except Exception as e:
        print(f"  transcribe-fail {video.name}: {e}", flush=True)
        return ("", None)
    finally:
        try:
            wav.unlink()
        except Exception:
            pass


# ----------------------------- Keyframes ------------------------------------

def extract_keyframes(video: Path, out_dir: Path) -> list[Path]:
    """Extract 3 jpeg keyframes at ~10%, 50%, 90% of duration, scaled to 640w."""
    # Get duration
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(video)],
            capture_output=True, text=True, timeout=30,
        )
        dur = float(r.stdout.strip())
    except Exception:
        dur = 15.0
    times = [max(0.5, dur * f) for f in (0.10, 0.50, 0.90)]
    out_paths = []
    for i, t in enumerate(times):
        out = out_dir / f"frame_{i}.jpg"
        cmd = [
            "ffmpeg", "-y", "-v", "error",
            "-ss", f"{t:.2f}", "-i", str(video),
            "-frames:v", "1",
            "-vf", "scale=640:-2",
            "-q:v", "4",
            str(out),
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=30)
            if out.exists() and out.stat().st_size > 500:
                out_paths.append(out)
        except Exception as e:
            print(f"  frame-fail {video.name} t={t:.1f}: {e}", flush=True)
    return out_paths


# ----------------------------- Gemini client + key rotation -----------------

class GeminiRotator:
    def __init__(self, keys: list[str], models: list[str]):
        self.keys = keys
        self.models = models
        # cool_until[(key, model)] = epoch seconds
        self.cool_until: dict[tuple[str, str], float] = {(k, m): 0.0 for k in keys for m in models}
        self.idx = 0
        self.lock = threading.Lock()
        self._clients: dict[str, object] = {}

    def _next_combo(self) -> tuple[str, str]:
        """Return (key, model) — try all combos, prefer ones not in cooldown."""
        with self.lock:
            now = time.time()
            combos = [(k, m) for k in self.keys for m in self.models]
            n = len(combos)
            for _ in range(n):
                k, m = combos[self.idx % n]
                self.idx += 1
                if self.cool_until[(k, m)] <= now:
                    return (k, m)
            # All cooling — pick soonest-available
            best = min(combos, key=lambda c: self.cool_until[c])
            wait = max(0, self.cool_until[best] - now)
            if wait > 0:
                time.sleep(wait + 0.5)
            return best

    def _client(self, key: str):
        if key not in self._clients:
            from google import genai
            self._clients[key] = genai.Client(api_key=key)
        return self._clients[key]

    def generate(self, contents, system_instruction: str, max_retries: int = 12):
        """contents is list of Part-likes per google.genai."""
        from google.genai import types
        last_err = None
        for attempt in range(max_retries):
            key, model = self._next_combo()
            client = self._client(key)
            try:
                resp = client.models.generate_content(
                    model=model,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        temperature=0.2,
                        response_mime_type="application/json",
                        system_instruction=system_instruction,
                    ),
                )
                return resp.text
            except Exception as e:
                msg = str(e)
                if "RESOURCE_EXHAUSTED" in msg or "429" in msg:
                    # Cool down just this (key, model) combo. Other models stay hot.
                    self.cool_until[(key, model)] = time.time() + 60
                    last_err = e
                    continue
                if "404" in msg or "NOT_FOUND" in msg or "is not found" in msg:
                    # Model not available on this key/region — banish for the run.
                    self.cool_until[(key, model)] = time.time() + 86400
                    last_err = e
                    continue
                if "API_KEY_INVALID" in msg or "API key expired" in msg or "API key not valid" in msg:
                    # Banish ALL models for this key — it's the key, not the model.
                    for m in self.models:
                        self.cool_until[(key, m)] = time.time() + 86400
                    last_err = e
                    continue
                if "500" in msg or "503" in msg or "UNAVAILABLE" in msg:
                    time.sleep(min(2 ** attempt, 8))
                    last_err = e
                    continue
                raise
        raise RuntimeError(f"Gemini exhausted retries across {len(self.keys)}x{len(self.models)} combos: {last_err}")


# ----------------------------- Per-video pipeline ---------------------------

def cache_key(video: Path) -> str:
    st = video.stat()
    raw = f"{video.resolve()}|{st.st_size}|{st.st_mtime_ns}".encode()
    return hashlib.sha256(raw).hexdigest()[:24]


def analyze_one(
    video: Path,
    lang_folder: str,
    rotator: GeminiRotator,
    cache_dir: Path,
    taxonomy_block: str | None,
) -> dict:
    key = cache_key(video)
    cache_file = cache_dir / f"{key}.json"
    if cache_file.exists():
        try:
            return json.loads(cache_file.read_text(encoding="utf-8"))
        except Exception:
            pass

    print(f"  [analyze] {lang_folder}/{video.name}", flush=True)
    transcript, det_lang = transcribe(video)
    transcript_snippet = (transcript or "")[:1500]

    with tempfile.TemporaryDirectory() as td:
        frames = extract_keyframes(video, Path(td))
        if not frames:
            result = {
                "file": video.name,
                "language_folder": lang_folder,
                "transcript_lang": det_lang,
                "transcript_snippet": transcript_snippet,
                "error": "no_keyframes",
                "angle_hypothesis": "ERROR",
                "hook_line": "",
                "core_promise": "",
                "format": "other",
                "persona": "",
                "best_platform": "universal",
                "confidence": 0.0,
                "notes": "keyframe extraction failed",
            }
            cache_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            return result

        from google.genai import types
        parts = [
            types.Part.from_bytes(data=p.read_bytes(), mime_type="image/jpeg")
            for p in frames
        ]
        text_prompt = (
            f"Language folder: {lang_folder}\n"
            f"Detected transcript language: {det_lang or 'unknown'}\n\n"
            f"TRANSCRIPT (first 60s, may be truncated):\n{transcript_snippet}\n"
        )
        parts.append(types.Part.from_text(text=text_prompt))

        sys_prompt = (
            PER_VIDEO_SYSTEM_WITH_TAXONOMY.replace("{taxonomy_block}", taxonomy_block)
            if taxonomy_block else PER_VIDEO_SYSTEM
        )

        is_error = False
        try:
            raw = rotator.generate(parts, sys_prompt)
            data = json.loads(raw)
        except Exception as e:
            print(f"  ERR {video.name}: {e}", flush=True)
            is_error = True
            data = {
                "angle_hypothesis": "ERROR",
                "hook_line": "",
                "core_promise": "",
                "format": "other",
                "persona": "",
                "best_platform": "universal",
                "confidence": 0.0,
                "notes": f"gemini failure: {e}",
            }

    result = {
        "file": video.name,
        "language_folder": lang_folder,
        "transcript_lang": det_lang,
        "transcript_snippet": transcript_snippet,
        **data,
    }
    # Only cache successful results — ERROR must be retried on next run
    if not is_error:
        cache_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


# ----------------------------- Consolidation --------------------------------

def consolidate(hypotheses: list[str], rotator: GeminiRotator) -> dict:
    from google.genai import types
    uniq = sorted(set(h for h in hypotheses if h and h != "ERROR"))
    payload = json.dumps({"labels": uniq}, ensure_ascii=False, indent=2)
    parts = [types.Part.from_text(text=payload)]
    print(f"[consolidate] sending {len(uniq)} unique labels to Gemini ...", flush=True)
    raw = rotator.generate(parts, CONSOLIDATE_SYSTEM)
    return json.loads(raw)


def taxonomy_block(taxonomy: dict) -> str:
    lines = []
    for a in taxonomy.get("angles", []):
        kw = ", ".join(a.get("signal_keywords", []) or [])
        lines.append(f"- {a['name']}: {a.get('description','')} (keywords: {kw})")
    return "\n".join(lines)


# ----------------------------- CSV output -----------------------------------

CSV_COLS = [
    "file", "language", "angle", "confidence", "hook_line",
    "core_promise", "format", "persona", "best_platform", "notes",
]


def write_csvs(root: Path, rows: list[dict]):
    out1 = root / "analysis.csv"
    with out1.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in CSV_COLS})
    print(f"[csv] wrote {out1} ({len(rows)} rows)", flush=True)

    angles = sorted({r["angle"] for r in rows})
    langs = sorted({r["language"] for r in rows})
    matrix = {a: Counter() for a in angles}
    for r in rows:
        matrix[r["angle"]][r["language"]] += 1
    out2 = root / "angles_x_languages.csv"
    with out2.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["angle"] + langs + ["TOTAL"])
        for a in angles:
            row = [a] + [matrix[a].get(l, 0) for l in langs]
            row.append(sum(matrix[a].values()))
            w.writerow(row)
    print(f"[csv] wrote {out2} ({len(angles)} angles × {len(langs)} languages)", flush=True)


# ----------------------------- Main -----------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--rebuild-taxonomy", action="store_true",
                    help="Force re-consolidation even if taxonomy.json exists")
    ap.add_argument("--consolidate-only", action="store_true",
                    help="Skip per-video analysis; use existing cache only. Runs consolidation + CSV.")
    ap.add_argument("--max-workers", type=int, default=6)
    args = ap.parse_args()

    load_env()
    root = Path(args.root).resolve()
    if not root.exists():
        print(f"ERR root not found: {root}", flush=True)
        sys.exit(1)

    keys_raw = os.environ.get("GEMINI_API_KEYS", "")
    keys = [k.strip() for k in keys_raw.split(",") if k.strip()]
    if not keys:
        print("ERR no GEMINI_API_KEYS in env", flush=True)
        sys.exit(1)
    print(f"[init] {len(keys)} Gemini keys × {len(GEMINI_MODELS)} models = {len(keys)*len(GEMINI_MODELS)} combos", flush=True)
    print(f"[init] model fallback chain: {GEMINI_MODELS}", flush=True)

    cache_dir = root / CACHE_DIRNAME
    cache_dir.mkdir(exist_ok=True)

    # Collect videos
    videos: list[tuple[Path, str]] = []
    for sub in root.iterdir():
        if not sub.is_dir() or sub.name.startswith("_"):
            continue
        for mp4 in sub.glob("*.mp4"):
            videos.append((mp4, sub.name))
    videos.sort(key=lambda x: (x[1], x[0].name))
    print(f"[init] found {len(videos)} videos under {root}", flush=True)
    if not videos:
        return

    # Load existing taxonomy
    taxonomy = {}
    if TAXONOMY_FILE.exists() and not args.rebuild_taxonomy:
        try:
            taxonomy = json.loads(TAXONOMY_FILE.read_text(encoding="utf-8"))
            print(f"[init] loaded taxonomy with {len(taxonomy.get('angles',[]))} angles", flush=True)
        except Exception:
            taxonomy = {}
    tax_block = taxonomy_block(taxonomy) if taxonomy.get("angles") else None

    rotator = GeminiRotator(keys, GEMINI_MODELS)

    # Pre-warm whisper before threads kick in
    get_whisper()

    results: list[dict] = []
    t0 = time.time()
    if args.consolidate_only:
        print("[run] consolidate-only mode: loading cache ...", flush=True)
        loaded = 0
        for mp4, lang in videos:
            cf = cache_dir / f"{cache_key(mp4)}.json"
            if cf.exists():
                try:
                    results.append(json.loads(cf.read_text(encoding="utf-8")))
                    loaded += 1
                except Exception:
                    pass
        print(f"[run] loaded {loaded}/{len(videos)} from cache", flush=True)
    else:
        workers = min(args.max_workers, len(keys))
        print(f"[run] analyzing with {workers} workers ...", flush=True)
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(analyze_one, mp4, lang, rotator, cache_dir, tax_block): (mp4, lang)
                    for mp4, lang in videos}
            done = 0
            for fut in as_completed(futs):
                mp4, lang = futs[fut]
                try:
                    r = fut.result()
                    results.append(r)
                except Exception as e:
                    print(f"  FATAL {mp4.name}: {e}", flush=True)
                    results.append({
                        "file": mp4.name, "language_folder": lang,
                        "angle_hypothesis": "ERROR", "confidence": 0.0,
                        "hook_line": "", "core_promise": "", "format": "other",
                        "persona": "", "best_platform": "universal", "notes": str(e),
                    })
                done += 1
                if done % 5 == 0 or done == len(videos):
                    el = time.time() - t0
                    eta = (el / done) * (len(videos) - done)
                    print(f"  progress {done}/{len(videos)} elapsed={el:.0f}s eta={eta:.0f}s", flush=True)

    # If no taxonomy yet (or rebuild forced or consolidate-only), consolidate
    if not taxonomy.get("angles") or args.rebuild_taxonomy or args.consolidate_only:
        hyps = [r.get("angle_hypothesis", "") for r in results]
        try:
            cons = consolidate(hyps, rotator)
            taxonomy = {
                "angles": cons.get("angles", []),
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
            TAXONOMY_FILE.parent.mkdir(parents=True, exist_ok=True)
            TAXONOMY_FILE.write_text(json.dumps(taxonomy, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"[taxonomy] saved {len(taxonomy['angles'])} angles to {TAXONOMY_FILE}", flush=True)
            mapping = {k.strip(): v for k, v in cons.get("mapping", {}).items()}
        except Exception as e:
            print(f"[taxonomy] consolidation failed: {e} — using raw hypotheses", flush=True)
            mapping = {h: h for h in hyps}
    else:
        # Existing taxonomy: hypothesis is supposed to be a canonical name already
        canonical_names = {a["name"] for a in taxonomy["angles"]}
        mapping = {}
        for r in results:
            h = r.get("angle_hypothesis", "")
            if h in canonical_names:
                mapping[h] = h
            else:
                # Best-effort: leave as-is (will surface in review)
                mapping[h] = h

    # Build CSV rows
    rows = []
    for r in results:
        h = r.get("angle_hypothesis", "ERROR")
        rows.append({
            "file": r.get("file", ""),
            "language": r.get("language_folder", ""),
            "angle": mapping.get(h, h),
            "confidence": r.get("confidence", 0.0),
            "hook_line": r.get("hook_line", ""),
            "core_promise": r.get("core_promise", ""),
            "format": r.get("format", ""),
            "persona": r.get("persona", ""),
            "best_platform": r.get("best_platform", ""),
            "notes": r.get("notes", ""),
        })

    write_csvs(root, rows)

    # Final summary
    counts = Counter(r["angle"] for r in rows)
    print("\n=== Angle distribution ===", flush=True)
    for a, n in counts.most_common():
        print(f"  {n:3d}  {a}", flush=True)
    low = [r for r in rows if (r.get("confidence") or 0) < 0.6]
    print(f"\nLow-confidence (<0.6): {len(low)} — review in analysis.csv", flush=True)
    print(f"\nElapsed: {time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
