"""Phase 2: apply translations from JSONL → TTS + Demucs + mix → final dubbed videos.

Usage:
  PYTHONIOENCODING=utf-8 python -u apply.py <root> [--workers 2] [--voice <edge>]
"""
import sys, io, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace", line_buffering=True)

import argparse
import json
import shutil
import time
import tempfile
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

PROJECT = Path(r"D:/Dev/Tools/Video Translator")


def load_project_env():
    sys.path.insert(0, str(PROJECT))
    os.chdir(str(PROJECT))
    env_file = PROJECT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def dub_one(entry: dict, voice: str | None, scratch_dir: str) -> tuple[bool, str, dict]:
    """Worker subprocess: build dubbed audio + merge into final mp4."""
    import sys as _sys, os as _os, time as _time, shutil as _shutil
    _sys.path.insert(0, str(PROJECT))
    _os.chdir(str(PROJECT))
    # Load env
    env_file = PROJECT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            _os.environ.setdefault(k.strip(), v.strip())

    try:
        from pipeline.audio import extract_audio_hq
        from pipeline.dub import build_dubbed_audio, dub_video, get_audio_duration
        from pipeline.dub.separator import separate_audio
    except Exception as e:
        return (False, f"import-fail: {e}", {})

    timings = {}
    try:
        src = entry["source_path"]
        out_path = entry["out_path"]
        segs = entry["segments"]
        # Build "translated_segments" — pipeline.dub expects dicts with text/start/end.
        # The English text (translation_en) is what gets TTS'd.
        translated = []
        for s in segs:
            t = (s.get("translation_en") or "").strip()
            translated.append({
                "id": s["id"],
                "start": s["start"],
                "end": s["end"],
                "text": t,
            })

        # Copy source into scratch so all pipeline temp files live there
        scratch = Path(scratch_dir)
        scratch.mkdir(parents=True, exist_ok=True)
        src_local = scratch / Path(src).name
        if not src_local.exists():
            _shutil.copy2(src, src_local)

        # 1. HQ audio
        t = _time.time()
        hq = extract_audio_hq(str(src_local), str(scratch))
        timings["audio_hq"] = round(_time.time() - t, 1)

        # 2. Demucs
        t = _time.time()
        demucs_dir = scratch / "_demucs"
        demucs_dir.mkdir(exist_ok=True)
        stems = separate_audio(hq, str(demucs_dir))
        no_vocals = stems["no_vocals"]
        timings["demucs"] = round(_time.time() - t, 1)

        # 3. Build dubbed audio
        t = _time.time()
        dubbed_audio = scratch / "dubbed.m4a"
        vid_duration = get_audio_duration(str(src_local))
        build_dubbed_audio(
            translated, lang="en",
            output_path=str(dubbed_audio), output_dir=str(scratch),
            custom_voice=voice, audio_mode="keep_original_bgm",
            bgm_path=None, bgm_volume=0.25,
            video_duration=vid_duration,
            pre_separated_no_vocals_path=no_vocals,
        )
        timings["dub"] = round(_time.time() - t, 1)

        # 4. Merge into final mp4
        t = _time.time()
        out_local = scratch / "out.mp4"
        dub_video(str(src_local), str(dubbed_audio), str(out_local))
        timings["merge"] = round(_time.time() - t, 1)

        # Move to final location
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        if Path(out_path).exists():
            Path(out_path).unlink()
        _shutil.move(str(out_local), out_path)

        return (True, "ok", timings)
    except Exception as e:
        return (False, f"dub-fail: {type(e).__name__}: {e}\n{traceback.format_exc()[-400:]}", timings)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--voice", default=None, help="Edge TTS voice (default: project default for en)")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    jsonl = root / "_super_saiyan" / "translations.jsonl"
    if not jsonl.exists():
        print(f"ERR translations.jsonl not found: {jsonl}", flush=True)
        sys.exit(1)

    entries = []
    incomplete = []
    for line in jsonl.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        segs = d.get("segments", [])
        if not segs:
            continue
        missing = [s for s in segs if not (s.get("translation_en") or "").strip()]
        if missing:
            incomplete.append((d["file"], len(missing), len(segs)))
            continue
        entries.append(d)

    print(f"[init] complete entries: {len(entries)}", flush=True)
    if incomplete:
        print(f"[init] SKIP-INCOMPLETE: {len(incomplete)} entries with missing translation_en", flush=True)
        for f, m, t in incomplete:
            print(f"  {f}: {m}/{t} segments empty", flush=True)

    if not entries:
        print("Nothing to dub. Fill in translation_en for entries first.", flush=True)
        return

    base_scratch = Path(tempfile.gettempdir()) / "vt_saiyan"
    base_scratch.mkdir(exist_ok=True)

    t0 = time.time()
    fails = []
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        futs = {}
        for e in entries:
            scratch = base_scratch / Path(e["source_path"]).stem
            scratch.mkdir(parents=True, exist_ok=True)
            futs[ex.submit(dub_one, e, args.voice, str(scratch))] = (e, scratch)
        done = 0
        for fut in as_completed(futs):
            entry, scratch = futs[fut]
            done += 1
            try:
                ok, msg, timings = fut.result()
            except Exception as e:
                ok, msg, timings = False, f"exec-fail: {e}", {}
            elapsed = time.time() - t0
            eta = (elapsed / done) * (len(entries) - done) if done else 0
            tag = "OK " if ok else "FAIL"
            t_str = " ".join(f"{k}={v}" for k, v in timings.items())
            print(f"[{done}/{len(entries)}] {tag} {entry['file']} ({entry.get('angle','?')}) "
                  f"elapsed={elapsed:.0f}s eta={eta:.0f}s {t_str}", flush=True)
            if not ok:
                print(f"    {msg}", flush=True)
                fails.append((entry["file"], msg))
            else:
                shutil.rmtree(scratch, ignore_errors=True)

    print(f"\n=== Done. OK={len(entries)-len(fails)}/{len(entries)} Failed={len(fails)} Time={time.time()-t0:.0f}s ===", flush=True)
    for f, m in fails:
        print(f"  {f}: {m[:200]}", flush=True)


if __name__ == "__main__":
    main()
