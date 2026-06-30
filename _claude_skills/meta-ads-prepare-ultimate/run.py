"""meta-ads-prepare-ultimate — one orchestrator, one shared manifest.

End-to-end Meta/TikTok ad-prep pipeline. Whisper runs ONCE (scan); every later
step reuses the manifest. The LLM-reasoning steps (language ID, angle, ad copy,
translation) are done by Opus editing manifest.json — this script handles the
mechanical work and hands control back to Opus between phases.

Subcommands (run in order):
  scan       Whisper-once -> manifest.json (transcript + voice gate + lang hint)
  status     Show what Opus still needs to fill in the manifest
  organize   Apply Opus-decided language: move to lang folders + rename CODE_DDMMNN
  dub        Translate-apply (voice videos only) using Opus translations
  brandpass  Resize 9:16 + fingerprint/dedup + watermark/outro + BGM swap
             (passes manifest transcript -> NO re-Whisper; routes audio by has_voice)
  package    creative_assets.csv + country list + QA gate + BGM license check

Repo: assumes Video Translator at D:/Dev/Tools/Video Translator
      (override with env VIDEO_TRANSLATOR_ROOT).
"""
from __future__ import annotations

import sys, io, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace", line_buffering=True)

# ---- low-impact throttle (keep the foreground PC usable) -------------------
# Demucs/torch grab EVERY core per process; with N parallel workers that
# oversubscribes the box into thrash (that's the "lag everything" symptom). Cap
# per-process math threads BEFORE torch is imported, and drop our scheduling
# priority. This block runs at module load, so it applies to the main process
# AND every spawned worker (Windows re-imports this module per worker). Override
# with env, e.g. OMP_NUM_THREADS=8, or bump --workers, when you want full tilt.
for _k in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS",
           "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
    os.environ.setdefault(_k, "3")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
try:
    if sys.platform == "win32":
        import ctypes as _ct
        _ct.windll.kernel32.SetPriorityClass(
            _ct.windll.kernel32.GetCurrentProcess(), 0x00004000)  # BELOW_NORMAL
    else:
        os.nice(10)
except Exception:
    pass

import argparse
import csv
import json
import random
import re
import shutil
import subprocess
import tempfile
import time
import traceback
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SKILL_DIR))

import manifest as M  # noqa: E402
import langmaps as L  # noqa: E402
import countries as C  # noqa: E402
import bgm_suggest as B  # noqa: E402
import voiceover as VO  # noqa: E402
import signature as SIG  # noqa: E402

REPO_ROOT = Path(os.environ.get("VIDEO_TRANSLATOR_ROOT", r"D:/Dev/Tools/Video Translator"))


# ----------------------------------------------------------------------------
# scan
# ----------------------------------------------------------------------------
def cmd_scan(args):
    src = Path(args.src).resolve()
    if not src.is_dir():
        sys.exit(f"src not a directory: {src}")
    data = M.scan(src, model_size=args.whisper,
                  skip_processed=getattr(args, "skip_processed", False))
    _print_fill_instructions(src, data)


# ----------------------------------------------------------------------------
# signature — recognize / mark already-processed files
# ----------------------------------------------------------------------------
def cmd_signature(args):
    """Mark processed videos with a hidden mp4 signature, or check coverage.

    --mark stamps every *.mp4 under --src (skips _* work folders) so a later
    `scan --skip-processed` ignores them. --check (default) reports how many
    already carry a signature and lists the unmarked ones.
    """
    src = Path(args.src).resolve()
    if not src.is_dir():
        sys.exit(f"src not a directory: {src}")
    vids = M.collect_videos(src)
    if not vids:
        sys.exit(f"no .mp4 under {src}")

    if args.mark:
        sig = SIG.build(app=args.app, batch=args.batch, extra=args.note)
        print(f"[signature] stamping {len(vids)} file(s): {sig}", flush=True)
        ok = fail = 0
        for v in vids:
            good, err = SIG.stamp(v, sig)
            if good:
                ok += 1
            else:
                fail += 1
                print(f"  FAIL {v.name} :: {err}", flush=True)
        print(f"[signature] marked OK={ok} FAIL={fail}", flush=True)
        if fail:
            sys.exit(1)
        return

    # default: check
    marked = [v for v in vids if SIG.is_processed(v)]
    unmarked = [v for v in vids if v not in marked]
    print(f"[signature] {len(marked)}/{len(vids)} already processed; "
          f"{len(unmarked)} unmarked", flush=True)
    if marked:
        ex = SIG.read_comment(marked[0])
        print(f"  example tag: {ex}", flush=True)
    for v in unmarked[:40]:
        print(f"  UNMARKED  {v.relative_to(src)}", flush=True)
    if len(unmarked) > 40:
        print(f"  ... +{len(unmarked) - 40} more", flush=True)


def _print_fill_instructions(src: Path, data: dict):
    need_meta = [v for v in data["videos"] if not v.get("vertical") or not v.get("language_folder")]
    voice = [v for v in data["videos"] if v.get("has_voice")]
    print("\n=== NEXT (Opus) ===", flush=True)
    print(f"Manifest: {M.manifest_path(src)}", flush=True)
    print(f"  {len(need_meta)} videos need vertical + language + angle + copy{{lang}} + bgm_cluster", flush=True)
    print(f"  {len(voice)} VOICE videos need segments[].translations{{lang}} per target language", flush=True)
    print("Opus: read manifest.json, fill those fields (translations keyed by ISO lang), "
          "save, then run `organize`.", flush=True)


# ----------------------------------------------------------------------------
# status
# ----------------------------------------------------------------------------
def cmd_status(args):
    src = Path(args.src).resolve()
    data = M.load_manifest(src)
    vids = data.get("videos", [])
    if not vids:
        sys.exit("No manifest yet — run `scan` first.")
    voice = [v for v in vids if v.get("has_voice")]
    need_vert = [v["orig_name"] for v in vids if not v.get("vertical")]
    need_meta = [v["orig_name"] for v in vids if v.get("vertical") != "skip" and not v.get("language_folder")]
    need_angle = [v["orig_name"] for v in vids if v.get("vertical") != "skip" and not v.get("angle")]
    need_copy = [v["orig_name"] for v in vids if v.get("vertical") != "skip" and not v.get("copy")]
    if getattr(args, "target_langs", None):
        # Lang-aware gate: mirror cmd_dub's exact skip condition so a manifest that is
        # missing even ONE target lang for ONE segment is flagged (a silently-skipped lang
        # otherwise yields a short pack with no error). Excludes vertical=="skip" like dub.
        _t = _parse_langs(args.target_langs)
        need_tr = []
        for v in voice:
            if v.get("vertical") == "skip":
                continue
            segs = v.get("segments", [])
            _sl = _src_iso(v)
            for _tl in _t:
                if _tl == _sl:
                    continue
                if not segs or any(not (s.get("translations", {}).get(_tl) or "").strip() for s in segs):
                    need_tr.append(v["orig_name"]); break
    else:
        need_tr = [v["orig_name"] for v in voice
                   if not v.get("segments") or any(not s.get("translations") for s in v.get("segments", []))]
    organized = [v for v in vids if v.get("renamed")]
    dubbed = [v for v in vids if v.get("dubbed_outputs")]
    done = [v for v in vids if v.get("outputs")]
    print(f"=== STATUS  {src}", flush=True)
    print(f"videos={len(vids)}  voice={len(voice)}  bgm-only={len(vids)-len(voice)}", flush=True)
    print(f"need vertical        : {len(need_vert)}", flush=True)
    print(f"need language/folder : {len(need_meta)}", flush=True)
    print(f"need angle           : {len(need_angle)}", flush=True)
    print(f"need copy{{lang}}      : {len(need_copy)}", flush=True)
    print(f"need translations    : {len(need_tr)} (voice videos)", flush=True)
    print(f"organized/renamed    : {len(organized)}/{len(vids)}", flush=True)
    print(f"dubbed               : {len(dubbed)}", flush=True)
    print(f"brand-passed (output): {len(done)}/{len(vids)}", flush=True)
    for label, lst in (("MISSING-VERTICAL", need_vert), ("MISSING-META", need_meta),
                       ("MISSING-COPY", need_copy), ("MISSING-TRANSLATION", need_tr)):
        if lst:
            print(f"  {label}: {', '.join(lst[:12])}{' ...' if len(lst) > 12 else ''}", flush=True)

    # --strict: hard manifest-complete gate for the unattended Vast batch. The default
    # (no flag) keeps the historical behavior of always exiting 0 — only --strict turns a
    # MISSING-* into a non-zero exit so full_batch.sh can refuse to start the dub phase on an
    # under-filled manifest (a silently-skipped lang = a short pack, no error otherwise).
    if getattr(args, "strict", False):
        blockers = {
            "MISSING-VERTICAL": need_vert,
            "MISSING-META": need_meta,
            "MISSING-COPY": need_copy,
            "MISSING-TRANSLATION": need_tr,
        }
        offending = {k: len(v) for k, v in blockers.items() if v}
        if offending:
            summary = ", ".join(f"{k}={n}" for k, n in offending.items())
            print(f"STRICT FAIL: manifest incomplete ({summary})", flush=True)
            sys.exit(1)
        print("STRICT OK: manifest complete (no MISSING-* blockers)", flush=True)


# ----------------------------------------------------------------------------
# organize
# ----------------------------------------------------------------------------
TS_RE = re.compile(r"_(\d{8})T(\d{6})")


def _video_date(entry: dict) -> str | None:
    m = TS_RE.search(entry["orig_name"])
    if m:
        try:
            return datetime.strptime(m.group(1), "%Y%m%d").strftime("%d%m")
        except ValueError:
            pass
    try:
        return datetime.fromtimestamp(Path(entry["src_path"]).stat().st_mtime).strftime("%d%m")
    except Exception:
        return None


def cmd_organize(args):
    src = Path(args.src).resolve()
    data = M.load_manifest(src)
    vids = data.get("videos", [])
    if not vids:
        sys.exit("No manifest — run scan first.")

    missing = [v["orig_name"] for v in vids
               if v.get("vertical") != "skip" and not v.get("language_folder")]
    if missing:
        sys.exit(f"{len(missing)} videos missing language_folder (Opus must fill first): "
                 f"{', '.join(missing[:8])}...")

    # Assign DDMMNN per date bucket — the sequence number is GLOBAL across
    # languages so every source creative gets a unique number. Each language
    # variant of the same creative (originals + dubs) then shares that number,
    # differing only by the CODE_ prefix (EN_040601 / ES_040601 / PT_040601).
    # This guarantees a dub's target name can never collide with another
    # source video's organized name.
    buckets: dict[str, list[dict]] = defaultdict(list)
    for v in vids:
        if v.get("vertical") == "skip":
            continue
        folder = v["language_folder"]
        code = v.get("lang_code") or L.folder_to_code(folder)
        if not code:
            print(f"  SKIP-NOCODE {v['orig_name']} (folder={folder})", flush=True)
            continue
        v["lang_code"] = code
        ddmm = _video_date(v) or "0000"
        v["_ddmm"] = ddmm
        buckets[ddmm].append(v)

    moved = 0
    for ddmm, group in buckets.items():
        group.sort(key=lambda v: v["orig_name"])
        width = 2 if len(group) <= 99 else 3
        for i, v in enumerate(group, 1):
            code = v["lang_code"]
            renamed = f"{code}_{ddmm}{i:0{width}d}.mp4"
            dest_dir = src / v["language_folder"]
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / renamed
            srcp = Path(v["src_path"])
            if dest.resolve() == srcp.resolve():
                v["renamed"] = renamed
                v["organized_path"] = str(dest)
                continue
            if dest.exists():
                print(f"  SKIP-EXISTS {renamed}", flush=True)
            else:
                if not args.dry_run:
                    shutil.move(str(srcp), str(dest))
                print(f"  {'DRY' if args.dry_run else 'MOVED'} {v['orig_name']} -> "
                      f"{v['language_folder']}/{renamed}", flush=True)
            v["renamed"] = renamed
            v["organized_path"] = str(dest)
            v["src_path"] = str(dest)  # follow the file
            moved += 1
        v["_ddmm"] = None  # cleanup transient

    for v in vids:
        v.pop("_ddmm", None)
    if args.dry_run:
        print(f"\n[organize] DRY-RUN: would move {moved}/{len(vids)} (manifest not written)", flush=True)
        return
    M.save_manifest(src, data)
    print(f"\n[organize] moved={moved} total={len(vids)}", flush=True)


# ----------------------------------------------------------------------------
# dub  (voice videos only, uses Opus translations)
# ----------------------------------------------------------------------------
def _dub_worker(job: dict) -> tuple[str, str, str]:
    src_path = job["src_path"]
    out_path = job["out_path"]
    segments = job["segments"]
    target_lang = job["target_lang"]
    voice = job["voice"]
    scratch_dir = job["scratch"]
    repo = job["repo"]

    import sys as _s, os as _o, shutil as _sh
    _s.path.insert(0, repo)
    _o.chdir(repo)
    env_file = Path(repo) / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, val = line.split("=", 1)
                _o.environ.setdefault(k.strip(), val.strip())
    try:
        from pipeline.audio import extract_audio_hq
        from pipeline.dub import build_dubbed_audio, dub_video, get_audio_duration
        from pipeline.dub.separator import separate_audio
    except Exception as e:
        return ("err", job["id"], f"import-fail: {e}")

    try:
        scratch = Path(scratch_dir)
        scratch.mkdir(parents=True, exist_ok=True)
        local = scratch / Path(src_path).name
        if not local.exists():
            _sh.copy2(src_path, local)
        translated = [{"id": s["id"], "start": s["start"], "end": s["end"],
                       "text": (s.get("translation") or "").strip()} for s in segments]
        hq = extract_audio_hq(str(local), str(scratch))
        demucs_dir = scratch / "_demucs"; demucs_dir.mkdir(exist_ok=True)
        stems = separate_audio(hq, str(demucs_dir))
        dubbed = scratch / "dubbed.m4a"
        build_dubbed_audio(
            translated, lang=target_lang, output_path=str(dubbed),
            output_dir=str(scratch), custom_voice=voice,
            audio_mode="keep_original_bgm", bgm_path=None, bgm_volume=0.25,
            video_duration=get_audio_duration(str(local)),
            pre_separated_no_vocals_path=stems["no_vocals"],
        )
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        out_local = scratch / "out.mp4"
        dub_video(str(local), str(dubbed), str(out_local))
        if Path(out_path).exists():
            Path(out_path).unlink()
        _sh.move(str(out_local), out_path)
        _sh.rmtree(scratch, ignore_errors=True)
        return ("ok", job["id"], "")
    except Exception as e:
        return ("err", job["id"], f"dub-fail: {type(e).__name__}: {e}\n{traceback.format_exc()[-300:]}")


def _target_name(src_renamed: str, target_code: str) -> str:
    """EN_040601.mp4 -> ES_040601.mp4 (reuse the DDMMNN suffix, swap the code)."""
    stem = Path(src_renamed).stem
    m = re.match(r"^[A-Za-z]+_(.+)$", stem)
    seq = m.group(1) if m else stem
    return f"{target_code}_{seq}.mp4"


def _parse_langs(s: str) -> list[str]:
    return [x.strip().lower() for x in (s or "").split(",") if x.strip()]


def _src_iso(v: dict) -> str:
    """Authoritative source-language ISO code for a video.

    Prefer the (Opus-confirmed) language_folder, then lang_code, then the
    `language` field, then the whisper hint. Folder is most reliable because it
    is what `organize` actually used. This must NOT depend on `language` being a
    bare ISO code — Opus may fill it as a full name ("english").
    """
    f = v.get("language_folder")
    if f and f in L.FOLDER_TO_ISO:
        return L.FOLDER_TO_ISO[f]
    lc = (v.get("lang_code") or "").lower()
    if lc and L.iso_to_folder(lc) != f"Other_{lc}":
        return lc
    lang = (v.get("language") or "").lower()
    if 0 < len(lang) <= 3:
        return lang
    return (v.get("whisper_lang") or "").lower()


def cmd_dub(args):
    """Multi-language dub. Each voice video is localized into every target lang.

    For a target lang == the video's source language the original already carries
    that voice, so no dub is produced (the organized original is recorded as the
    `dubbed_outputs[lang]` so brandpass can route it). Every other target lang is
    Edge-TTS dubbed (over Demucs-isolated BGM) into `<src>/<lang_folder>/CODE_*`.
    """
    src = Path(args.src).resolve()
    data = M.load_manifest(src)
    by_id = M.index_by_id(data)
    targets = _parse_langs(args.target_langs)
    if not targets:
        sys.exit("--target-langs required (e.g. it,fr,pt)")

    jobs = []  # each carries (id, tlang)
    for v in data.get("videos", []):
        if not v.get("has_voice") or v.get("vertical") == "skip":
            continue
        segs = v.get("segments", [])
        if not segs:
            continue
        src_lang = _src_iso(v)
        src_renamed = v.get("renamed") or v["orig_name"]
        dubbed = dict(v.get("dubbed_outputs") or {})
        for tlang in targets:
            if tlang == src_lang and not getattr(args, "revoice_source", False):
                # Original is already in this language — record it, no dub.
                dubbed[tlang] = v.get("organized_path") or v["src_path"]
                continue
            # Need every segment translated into this language.
            if any(not (s.get("translations", {}).get(tlang) or "").strip() for s in segs):
                continue  # Opus hasn't filled this lang yet — skip silently
            tfolder = L.iso_to_folder(tlang)
            tcode = L.folder_to_code(tfolder) or tlang.upper()
            out_path = src / tfolder / _target_name(src_renamed, tcode)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            if out_path.exists() and out_path.stat().st_size > 100_000:
                dubbed[tlang] = str(out_path)
                continue
            flat_segs = [{"id": s["id"], "start": s["start"], "end": s["end"],
                          "text": s.get("text", ""),
                          "translation": s["translations"][tlang]} for s in segs]
            jobs.append({
                "id": v["id"], "tlang": tlang,
                "src_path": v.get("organized_path") or v["src_path"],
                "out_path": str(out_path), "segments": flat_segs,
                "target_lang": tlang, "voice": args.voice,
                "scratch": str(Path(tempfile.gettempdir()) / "vt_ultimate" / f"{v['id']}_{tlang}"),
                "repo": str(REPO_ROOT),
            })
        v["dubbed_outputs"] = dubbed

    if not jobs:
        M.merge_save(src, data["videos"], ("dubbed_outputs",))
        print("[dub] nothing to dub (no pending translated voice videos)", flush=True)
        return

    print(f"[dub] {len(jobs)} dub jobs  workers={args.workers}  targets={','.join(targets)}", flush=True)
    t0 = time.time(); ok = err = 0; fails = []
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(_dub_worker, j): j for j in jobs}
        for n, fut in enumerate(as_completed(futs), 1):
            status, vid, msg = fut.result()
            job = futs[fut]
            if status == "ok":
                ok += 1
                by_id[vid].setdefault("dubbed_outputs", {})[job["tlang"]] = job["out_path"]
            else:
                err += 1; fails.append((vid, msg))
            print(f"[{n}/{len(jobs)}] {status.upper()} {vid} ->{job['tlang']}  "
                  f"elapsed={(time.time()-t0):.0f}s", flush=True)
            if status == "err":
                print(f"    {msg}", flush=True)
    M.merge_save(src, data["videos"], ("dubbed_outputs",))
    print(f"\n[dub] ok={ok} err={err} time={(time.time()-t0):.0f}s", flush=True)
    if fails:
        sys.exit(1)


# ----------------------------------------------------------------------------
# brandpass  (resize 9:16 + fingerprint + watermark/outro + BGM swap)
# ----------------------------------------------------------------------------
def _load_cluster_map(pool: Path) -> dict:
    cmap = pool / "cluster_map.csv"
    if not cmap.is_file():
        return {}
    with open(cmap, encoding="utf-8") as f:
        return {r["file"]: r["cluster"] for r in csv.DictReader(f) if r.get("cluster")}


def _pick_bgm(cluster: str | None, pool: Path, rng: random.Random) -> str | None:
    exts = ("*.mp3", "*.wav", "*.m4a")
    if cluster:
        sub = pool / cluster
        if sub.is_dir():
            tracks = [t for e in exts for t in sorted(sub.glob(e))]
            if tracks:
                return str(rng.choice(tracks))
    allt = [t for e in exts for t in sorted(pool.rglob(e))]
    return str(rng.choice(allt)) if allt else None


def _brand_worker(job: dict) -> tuple[str, str, float, str]:
    repo = job["repo"]
    import sys as _s
    _s.path.insert(0, repo)
    try:
        from pipeline.brand_pass import brand_pass_video
    except Exception as e:
        return ("err", job["id"], 0.0, f"import-fail: {e}")
    out = Path(job["out_path"])
    if out.exists() and out.stat().st_size > 100_000:
        return ("skip", job["id"], 0.0, "")
    out.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    try:
        kwargs = dict(
            input_path=job["input_path"], output_path=str(out),
            transcript=job["transcript"],            # <-- skips Whisper entirely
            keep_original_voice=job["keep_voice"],
            outro_title=job["outro_title"], outro_subtitle=job["outro_subtitle"],
            trim_endcard=job["trim_endcard"], random_seed=job["seed"],
        )
        if job["watermark"]:
            kwargs["watermark_image"] = job["watermark"]
            kwargs["watermark_size"] = job["watermark_size"]
        if job["outro_logo"]:
            kwargs["outro_logo_image"] = job["outro_logo"]
        if job["outro_video"]:
            kwargs["outro_video"] = job["outro_video"]
        if job["bgm_replace"]:
            kwargs["bgm_replace_path"] = job["bgm_replace"]
        brand_pass_video(**kwargs)
        return ("ok", job["id"], time.time() - t0, "")
    except Exception as e:
        return ("err", job["id"], time.time() - t0,
                f"{type(e).__name__}: {e}\n{traceback.format_exc()[-300:]}")


def _pick_outro(v: dict, args) -> str | None:
    """Route outro by the Opus-tagged man/woman variant (hair talking-heads).
    Falls back to the generic --outro-video when no variant or no per-sex outro."""
    variant = (v.get("outro_variant") or "").lower()
    if variant == "man" and args.outro_man:
        return args.outro_man
    if variant in ("woman", "women", "female") and args.outro_woman:
        return args.outro_woman
    return args.outro_video


def cmd_brandpass(args):
    src = Path(args.src).resolve()
    dst = Path(args.dst).resolve()
    data = M.load_manifest(src)
    by_id = M.index_by_id(data)
    for p in (args.watermark, args.outro_logo, args.outro_video,
              args.outro_man, args.outro_woman):
        if p and not Path(p).is_file():
            sys.exit(f"file not found: {p}")
    outro_logo = args.outro_logo or args.watermark
    want_vertical = args.vertical
    target_langs = _parse_langs(args.target_langs)  # optional filter on dub outputs

    pool = None; cluster_map = {}
    if args.bgm_pool:
        pool = Path(args.bgm_pool).resolve()
        if not pool.is_dir():
            sys.exit(f"--bgm-pool not a dir: {pool}")
        cluster_map = _load_cluster_map(pool)

    def _mkjob(v, okey, inp, out_path, keep_voice, seed):
        transcript = (v.get("transcript") or "x") if keep_voice else ""
        bgm = None
        if pool:
            rng = random.Random(seed if seed is not None else hash(f"{v['id']}_{okey}"))
            cluster = v.get("bgm_cluster") or cluster_map.get(v["orig_name"])
            bgm = _pick_bgm(cluster, pool, rng)
        return {
            "id": v["id"], "okey": okey, "input_path": inp, "out_path": str(out_path),
            "transcript": transcript, "keep_voice": keep_voice,
            "watermark": args.watermark, "watermark_size": args.watermark_size,
            "outro_logo": outro_logo, "outro_video": _pick_outro(v, args),
            "outro_title": args.outro_title, "outro_subtitle": args.outro_subtitle,
            "trim_endcard": args.trim_endcard, "bgm_replace": bgm,
            "seed": seed, "repo": str(REPO_ROOT),
        }

    jobs = []
    seed_i = 0
    for v in data.get("videos", []):
        if v.get("vertical") == "skip":
            continue
        if want_vertical and v.get("vertical") != want_vertical:
            continue
        if not v.get("language_folder"):
            print(f"  SKIP-NOLANG {v['orig_name']}", flush=True); continue
        seed = (args.seed_base + seed_i) if args.seed_base else None
        ang = (v.get("angle") or "other").strip() or "other"
        if v.get("has_voice"):
            # One output per dubbed/localized language. Campaign tree:
            #   <dst>/VOICED_<Language>/<angle>/CODE_DDMMNN.mp4
            #   (folder = campaign, angle subfolder = ad set, .mp4 = ads)
            dubbed = v.get("dubbed_outputs") or {}
            langs = [l for l in dubbed if (not target_langs or l in target_langs)]
            if not langs:
                print(f"  SKIP-NODUB {v.get('renamed') or v['orig_name']} "
                      f"(no dub outputs for {target_langs or 'any'})", flush=True)
                continue
            for tlang in langs:
                tcode = L.folder_to_code(L.iso_to_folder(tlang)) or tlang.upper()
                name = _target_name(v.get("renamed") or v["orig_name"], tcode)
                out_path = dst / f"VOICED_{L.iso_to_english(tlang)}" / ang / name
                jobs.append(_mkjob(v, tlang, dubbed[tlang], out_path, True, seed))
                seed_i += 1
        else:
            # BGM-only: language-agnostic — ONE output in the universal campaign:
            #   <dst>/BGM_UNIVERSAL/<angle>/MU_DDMMNN.mp4  (reusable in every market)
            inp = v.get("organized_path") or v["src_path"]
            name = v.get("renamed") or v["orig_name"]
            out_path = dst / "BGM_UNIVERSAL" / ang / name
            jobs.append(_mkjob(v, "_", inp, out_path, False, seed))
            seed_i += 1

    if not jobs:
        sys.exit("No jobs — check --vertical / dub outputs / languages.")
    print(f"[brandpass] {len(jobs)} jobs  vertical={want_vertical or 'all'}  "
          f"workers={args.workers}  bgm_pool={'yes' if pool else 'no'}  "
          f"trim_endcard={args.trim_endcard}", flush=True)
    t0 = time.time(); ok = skip = err = 0; fails = []
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(_brand_worker, j): j for j in jobs}
        for n, fut in enumerate(as_completed(futs), 1):
            status, vid, dur, msg = fut.result()
            job = futs[fut]
            if status in ("ok", "skip"):
                ok += (status == "ok"); skip += (status == "skip")
                by_id[vid].setdefault("outputs", {})[job["okey"]] = job["out_path"]
            else:
                err += 1; fails.append((vid, msg))
            if n % 3 == 0 or n == len(jobs):
                print(f"[{n}/{len(jobs)}] ok={ok} skip={skip} err={err} "
                      f"elapsed={(time.time()-t0)/60:.1f}m", flush=True)
            if status == "err":
                print(f"    {vid}: {msg}", flush=True)
    M.merge_save(src, data["videos"], ("outputs",))
    print(f"\n[brandpass] ok={ok} skip={skip} err={err} time={(time.time()-t0)/60:.1f}m", flush=True)

    # Stamp the processed signature so a later `scan --skip-processed` skips these.
    # Outputs (deliverables) get provenance; sources are marked done too.
    if not getattr(args, "no_sign", False):
        app = want_vertical or "meta_ads"
        sig = SIG.build(app=app, extra=f"step=brandpass")
        sok = 0
        for v in data.get("videos", []):
            for op in (v.get("outputs") or {}).values():
                if op and Path(op).exists() and SIG.stamp(op, sig)[0]:
                    sok += 1
            srcp = v.get("organized_path") or v.get("src_path")
            if srcp and Path(srcp).exists():
                SIG.stamp(srcp, sig)
        print(f"[brandpass] signature stamped on {sok} output(s) + sources", flush=True)

    if fails:
        sys.exit(1)


# ----------------------------------------------------------------------------
# package  (creative_assets.csv + country list + QA gate + license check)
# ----------------------------------------------------------------------------
def _probe_stream(video: Path) -> dict:
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height",
             "-show_entries", "format=duration",
             "-of", "json", str(video)],
            capture_output=True, text=True, timeout=30,
        )
        j = json.loads(r.stdout)
        st = (j.get("streams") or [{}])[0]
        dur = float(j.get("format", {}).get("duration", 0) or 0)
        # audio?
        ra = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a:0",
             "-show_entries", "stream=index", "-of", "csv=p=0", str(video)],
            capture_output=True, text=True, timeout=30,
        )
        return {"w": st.get("width"), "h": st.get("height"),
                "dur": round(dur, 2), "has_audio": bool(ra.stdout.strip())}
    except Exception as e:
        return {"w": None, "h": None, "dur": 0, "has_audio": False, "err": str(e)}


# English campaign-folder label -> the countries that VOICED campaign should target
LANG_COUNTRIES = {
    "English": "US, UK, Canada, Australia, Ireland, New Zealand",
    "Spanish": "Spain, Mexico, Argentina, Colombia, Chile",
    "French": "France, Belgium, Switzerland, Canada (QC)",
    "Portuguese": "Portugal, Brazil", "German": "Germany, Austria, Switzerland",
    "Italian": "Italy, Switzerland", "Polish": "Poland",
    "Dutch": "Netherlands, Belgium", "Turkish": "Türkiye",
    "Japanese": "Japan", "Korean": "South Korea", "Chinese": "Taiwan, Hong Kong, Singapore",
    "Arabic": "Saudi Arabia, UAE, Qatar, Egypt", "Hindi": "India",
    "Bengali": "Bangladesh", "Gujarati": "India (Gujarat)", "Tagalog": "Philippines",
    "Indonesian": "Indonesia", "Vietnamese": "Vietnam", "Thai": "Thailand",
    "Hungarian": "Hungary", "Greek": "Greece", "Ukrainian": "Ukraine",
    "Czech": "Czechia", "Romanian": "Romania", "Russian": "(restricted on Meta)",
}


def _write_campaign_readme(out_dir: Path) -> None:
    """Write 00_CAMPAIGNS_README.txt — a visual map of the campaign tree (folder =
    campaign, angle subfolder = ad set, .mp4 = ads) + per-campaign target countries
    + Meta setup + the BGM-swap scaling note."""
    camps = sorted([p for p in out_dir.iterdir() if p.is_dir()
                    and (p.name == "BGM_UNIVERSAL" or p.name.startswith("VOICED_"))],
                   key=lambda p: (not p.name.startswith("BGM"), p.name))
    L = ["META ADS — CAMPAIGN PACK",
         "Folder = Campaign  |  angle subfolder = Ad Set  |  .mp4 = Ads", ""]
    for c in camps:
        adsets = sorted([s for s in c.iterdir() if s.is_dir()],
                        key=lambda s: -len(list(s.glob("*.mp4"))))
        total = sum(len(list(s.glob("*.mp4"))) for s in adsets)
        if c.name == "BGM_UNIVERSAL":
            tgt = "ALL markets (T1+T2, see countries.txt) — swap BGM per country to scale"
        else:
            tgt = LANG_COUNTRIES.get(c.name[len("VOICED_"):], c.name[len("VOICED_"):])
        L.append(f"[{c.name}]  ({total} ads)  ->  {tgt}")
        for s in adsets:
            L.append(f"    {s.name:18} {len(list(s.glob('*.mp4')))}")
        L.append("")
    L += ["META SETUP: 1 Campaign per top folder (App Promotion / Advantage+); 1 Ad Set",
          "per angle subfolder; upload the .mp4s inside as Ads. BGM_UNIVERSAL = workhorse.",
          "",
          "SCALE a new country: reuse BGM_UNIVERSAL (swap BGM to that country's trending",
          "track, re-run brandpass --bgm-pool <pool>) + reuse/dub the matching VOICED_<lang>."]
    (out_dir / "00_CAMPAIGNS_README.txt").write_text("\n".join(L), encoding="utf-8")


def _copy_for(v: dict, lang: str) -> tuple[list, list]:
    """Localized (headlines, primary_texts) for a market language, with fallback
    to the legacy single-language fields when `copy` isn't filled per-lang."""
    copy = (v.get("copy") or {}).get(lang) or {}
    headlines = copy.get("headlines") or v.get("headlines") or []
    primary = copy.get("primary_texts") or v.get("primary_texts") or []
    return headlines, primary


def cmd_package(args):
    src = Path(args.src).resolve()
    dst = Path(args.dst).resolve()
    data = M.load_manifest(src)
    want_vertical = args.vertical
    target_langs = _parse_langs(args.target_langs)
    vids = [v for v in data.get("videos", [])
            if v.get("vertical") != "skip"
            and (not want_vertical or v.get("vertical") == want_vertical)]
    out_dir = dst
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1a. creative_assets.csv — VOICED videos only, one row per (video, spoken lang).
    #     These are language-SPECIFIC (the voiceover is in that language).
    assets = out_dir / "creative_assets.csv"
    nvoiced = 0
    with open(assets, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["file", "language", "vertical", "angle", "hook", "headlines",
                    "primary_texts", "bgm_cluster"])
        for v in vids:
            if not v.get("has_voice"):
                continue
            outputs = v.get("outputs") or {}
            langs = [l for l in outputs if l != "_"]
            if target_langs:
                langs = [l for l in langs if l in target_langs]
            for lang in langs:
                file_name = Path(outputs[lang]).name
                headlines, primary = _copy_for(v, lang)
                w.writerow([file_name, lang, v.get("vertical", ""),
                            v.get("angle", ""), v.get("hook", ""),
                            " | ".join(headlines), " | ".join(primary),
                            v.get("bgm_cluster", "")])
                nvoiced += 1
    print(f"[package] voiced creative assets ({nvoiced}) -> {assets}", flush=True)

    # 1b. bgm_only_assets.csv — language-AGNOSTIC BGM-only videos (in `_music/`).
    #     One row each. REUSABLE across every market: to scale to a new country,
    #     swap the BGM to that country's trending track (no re-dub, no re-classify).
    #     `market_hint` = where the competitor originally ran it (reference only).
    bgm_assets = out_dir / "bgm_only_assets.csv"
    nbgm = 0
    with open(bgm_assets, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["file", "vertical", "angle", "hook", "bgm_cluster",
                    "market_hint", "reuse"])
        for v in vids:
            if v.get("has_voice"):
                continue
            outputs = v.get("outputs") or {}
            file_name = Path(outputs.get("_", v.get("renamed") or v["orig_name"])).name
            w.writerow([file_name, v.get("vertical", ""), v.get("angle", ""),
                        v.get("hook", ""), v.get("bgm_cluster", ""),
                        v.get("market_hint", "") or v.get("language", ""),
                        "all markets — swap BGM per country"])
            nbgm += 1
    print(f"[package] BGM-only assets ({nbgm}) -> {bgm_assets}", flush=True)

    # 2. country targeting list
    extra = [c for c in (args.extra_countries or "").split(",") if c.strip()]
    clist = C.build_country_list(include_east_eu=args.east_eu, extra=extra)
    countries_txt = out_dir / "countries.txt"
    countries_txt.write_text("\n".join(clist), encoding="utf-8")
    print(f"[package] {len(clist)} countries -> {countries_txt}", flush=True)

    # 3. QA gate over every output (voice videos have one per language).
    qa_rows = []
    fails = 0
    for v in vids:
        outputs = v.get("outputs") or {}
        if not outputs:
            qa_rows.append([v.get("renamed") or v["orig_name"], "MISSING", "", "", ""])
            fails += 1
            continue
        for okey, op in sorted(outputs.items()):
            if not op or not Path(op).exists():
                qa_rows.append([f"{v.get('renamed') or v['orig_name']}:{okey}",
                                "MISSING", "", "", ""])
                fails += 1
                continue
            info = _probe_stream(Path(op))
            dim_ok = (info["w"] == 1080 and info["h"] == 1920)
            aud_ok = info["has_audio"]
            verdict = "PASS" if (dim_ok and aud_ok and info["dur"] > 1) else "FAIL"
            if verdict == "FAIL":
                fails += 1
            qa_rows.append([Path(op).name, verdict, f'{info["w"]}x{info["h"]}',
                            info["dur"], "audio" if aud_ok else "NO-AUDIO"])
    qa_csv = out_dir / "qa_report.csv"
    with open(qa_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["file", "verdict", "dimensions", "duration_s", "audio"])
        w.writerows(qa_rows)
    print(f"[package] QA gate: {len(qa_rows)-fails}/{len(qa_rows)} PASS -> {qa_csv}", flush=True)

    # 4. BGM license note
    if args.bgm_pool:
        print(f"[package] BGM license: tracks swapped from royalty-free pool "
              f"{args.bgm_pool} (verify pool is Pixabay/royalty-free).", flush=True)
    else:
        bgm_only = [v for v in vids if not v.get("has_voice")]
        if bgm_only:
            print(f"[package] WARNING: {len(bgm_only)} music-only videos kept SOURCE BGM "
                  f"(no --bgm-pool). Risk of Meta copyright flag — swap to royalty-free.",
                  flush=True)

    # 5. campaign README — visual map of the BGM_UNIVERSAL / VOICED_<lang> tree
    _write_campaign_readme(out_dir)
    print(f"[package] campaign README -> {out_dir / '00_CAMPAIGNS_README.txt'}", flush=True)

    if fails:
        print(f"\n[package] {fails} QA failures — review qa_report.csv before upload.", flush=True)
        sys.exit(1)
    print("\n[package] all clear. Ready to upload.", flush=True)


# ----------------------------------------------------------------------------
# framegrab  (extract a mid-video frame so Opus can tag man/woman outro)
# ----------------------------------------------------------------------------
def cmd_framegrab(args):
    """Extract a frame at ~50% duration for each (voice) video so Opus can look
    at it and set `outro_variant` = man|woman. Frames -> <src>/_ultimate/_frames/."""
    src = Path(args.src).resolve()
    data = M.load_manifest(src)
    out_dir = src / "_ultimate" / "_frames"
    out_dir.mkdir(parents=True, exist_ok=True)
    n = ok = 0
    for v in data.get("videos", []):
        if v.get("vertical") == "skip":
            continue
        if args.vertical and v.get("vertical") != args.vertical:
            continue
        if args.voice_only and not v.get("has_voice"):
            continue
        out = out_dir / f"{v['id']}.jpg"
        if out.exists():
            ok += 1; continue
        n += 1
        dur = v.get("duration") or 0
        ts = max(0.1, dur * args.at)
        inp = v.get("organized_path") or v["src_path"]
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-v", "error", "-ss", f"{ts:.2f}", "-i", inp,
                 "-frames:v", "1", "-q:v", "3", "-vf", "scale=480:-1", str(out)],
                check=True, capture_output=True, timeout=60,
            )
            ok += 1
        except Exception as e:
            print(f"  ERR {v['id']}: {e}", flush=True)
    print(f"[framegrab] {ok} frames in {out_dir} ({n} new). "
          f"Opus: view them, set each video's outro_variant = man|woman.", flush=True)


# ----------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(prog="meta-ads-prepare-ultimate")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("scan"); p.add_argument("--src", required=True)
    p.add_argument("--whisper", default="small",
                   choices=["tiny", "base", "small", "medium", "large-v3"])
    p.add_argument("--skip-processed", action="store_true",
                   help="ignore files already carrying a pipeline signature "
                        "(see `signature`) — never re-Whisper a finished file")
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("status"); p.add_argument("--src", required=True)
    p.add_argument("--target-langs",
                   help="when given, the MISSING-TRANSLATION gate becomes lang-aware: a voice "
                        "video is incomplete unless every target lang (!= its source) has a "
                        "non-empty translation in EVERY segment (mirrors `dub`'s skip rule)")
    p.add_argument("--strict", action="store_true",
                   help="exit non-zero if any voice video is missing required "
                        "vertical/meta/copy/translations (default: always exit 0)")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("organize"); p.add_argument("--src", required=True)
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_organize)

    p = sub.add_parser("dub"); p.add_argument("--src", required=True)
    p.add_argument("--target-langs", required=True,
                   help="comma list of ISO langs, e.g. it,fr,pt")
    p.add_argument("--voice", default=None)
    p.add_argument("--workers", type=int, default=2)
    p.add_argument("--revoice-source", action="store_true",
                   help="also TTS-dub the source language (don't keep original audio). "
                        "Use when the original VO must be replaced — e.g. rebranding the "
                        "spoken app name. Requires translations filled for the source lang too.")
    p.set_defaults(func=cmd_dub)

    p = sub.add_parser("framegrab"); p.add_argument("--src", required=True)
    p.add_argument("--vertical", default=None)
    p.add_argument("--voice-only", action="store_true",
                   help="only grab frames for voice (talking-head) videos")
    p.add_argument("--at", type=float, default=0.5, help="fraction of duration (0-1)")
    p.set_defaults(func=cmd_framegrab)

    p = sub.add_parser("brandpass")
    p.add_argument("--src", required=True); p.add_argument("--dst", required=True)
    p.add_argument("--vertical", default=None, help="only process this vertical (hair/home)")
    p.add_argument("--target-langs", default="", help="optional filter on dub-output langs")
    p.add_argument("--watermark", default=None)
    p.add_argument("--watermark-size", type=int, default=140)
    p.add_argument("--outro-title", default="")
    p.add_argument("--outro-subtitle", default="")
    p.add_argument("--outro-logo", default=None)
    p.add_argument("--outro-video", default=None)
    p.add_argument("--outro-man", default=None, help="outro for male talking-heads")
    p.add_argument("--outro-woman", default=None, help="outro for female talking-heads")
    p.add_argument("--trim-endcard", action="store_true")
    p.add_argument("--bgm-pool", default=None)
    p.add_argument("--workers", type=int, default=3)  # throttled: 3 workers x 3 threads ~= 9 cores, leaves headroom
    p.add_argument("--seed-base", type=int, default=0)
    p.add_argument("--no-sign", action="store_true",
                   help="do NOT stamp the processed signature onto outputs/sources")
    p.set_defaults(func=cmd_brandpass)

    p = sub.add_parser("package")
    p.add_argument("--src", required=True); p.add_argument("--dst", required=True)
    p.add_argument("--vertical", default=None)
    p.add_argument("--target-langs", default="")
    p.add_argument("--bgm-pool", default=None)
    p.add_argument("--east-eu", action="store_true")
    p.add_argument("--extra-countries", default="")
    p.set_defaults(func=cmd_package)

    # bgm-suggest — trend-aware BGM advisor (location x language x content)
    p = sub.add_parser("bgm-suggest",
                       help="suggest trend-aware BGM per video -> bgm_suggestions.csv + shopping list")
    p.add_argument("--src", required=True)
    p.add_argument("--countries", default="", help="campaign target locations, e.g. US,FR,BR,SA")
    p.add_argument("--write", action="store_true", help="write refined bgm_cluster back to manifest")
    p.set_defaults(func=B.cmd_manifest)

    # voiceover — generate AI voiceover for BGM-only clips -> localized VOICED ads
    p = sub.add_parser("voiceover",
                       help="add AI voiceover (Edge-TTS) to BGM-only clips per language")
    p.add_argument("--src", required=True)
    p.add_argument("--dst", required=True)
    p.add_argument("--app-config", required=True, help="app.json (app_name, cta, usp, ...)")
    p.add_argument("--vo-bank", required=True, help="vo_bank JSON {lang:{angle:{short,long}}}")
    p.add_argument("--target-langs", required=True, help="e.g. en,es,fr,pt")
    p.add_argument("--vertical", default=None)
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--concurrency", type=int, default=0,
                   help="parallel jobs (0=auto, ~half the CPU cores; render runs at below-normal priority)")
    p.set_defaults(func=VO.cmd_voiceover)

    # signature — recognize / mark already-processed files (skip re-runs)
    p = sub.add_parser("signature",
                       help="mark processed videos with a hidden mp4 tag, or "
                            "check coverage (so `scan --skip-processed` skips them)")
    p.add_argument("--src", required=True)
    p.add_argument("--mark", action="store_true",
                   help="stamp every mp4 under --src (default: just check)")
    p.add_argument("--app", default="", help="app label for the tag prefix, e.g. decoai")
    p.add_argument("--batch", default="", help="batch id for the tag, e.g. 2805")
    p.add_argument("--note", default="", help="extra key=val appended to the tag")
    p.set_defaults(func=cmd_signature)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
