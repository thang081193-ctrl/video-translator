# -*- coding: utf-8 -*-
"""AI voiceover for BGM-only clips — turns language-agnostic B-roll into localized
VOICED ads. App-config driven (any app, any vertical).

Flow per BGM-only clip × target language:
  vo_bank[lang][angle][short|long]  (Opus-authored, {app} placeholder)
    -> Edge-TTS (4 expressive multilingual voices, rotated for variety)
    -> fit to clip duration (atempo)
    -> mix over the clip's BGM (ducked) -> VOICED_<Language>/<angle>/<LANG>-VO_DDMMNN.mp4
The original BGM-only master in BGM_UNIVERSAL is kept (it's the reusable source).
"""
from __future__ import annotations
import asyncio, json, os, re, subprocess, sys
from pathlib import Path

# 4 approved Edge *Multilingual* voices (read ALL top-10 langs) — rotated per clip.
VOICE_ROTATION = [
    ("en-US-AvaMultilingualNeural", "+8%"),
    ("en-US-EmmaMultilingualNeural", "+8%"),
    ("en-US-AndrewMultilingualNeural", "+6%"),
    ("en-US-BrianMultilingualNeural", "+6%"),
]
LONG_TIER_S = 22.0   # clip >= this -> use the "long" script, else "short"

def _dur(f: str) -> float:
    r = subprocess.run(["ffprobe","-v","error","-show_entries","format=duration","-of","csv=p=0",f],
                       capture_output=True, text=True)
    try: return float(r.stdout.strip())
    except Exception: return 0.0

def _fill(text: str, app: dict) -> str:
    return (text.replace("{app}", app.get("app_name","the app"))
                .replace("{cta}", app.get("cta","Download now"))
                .replace("{tagline}", app.get("tagline","")))

async def _tts(text: str, voice: str, rate: str, out: str):
    import edge_tts
    await edge_tts.Communicate(text, voice, rate=rate).save(out)

async def _tts_robust(text: str, voices: list, vidx: int, rate: str, out: str, attempts: int = 4):
    """TTS with retry — rotates to the NEXT voice in the language's list on a transient
    'No audio received' / network blip so one flaky call never leaves a gap at scale."""
    n = len(voices); last = None
    for k in range(attempts):
        v = voices[(vidx + k) % n]
        try:
            await _tts(text, v, rate, out)
            if os.path.exists(out) and os.path.getsize(out) > 1500:
                return v
        except Exception as e:
            last = e
        await asyncio.sleep(0.5)
    raise last or RuntimeError("no audio after retries")

# run ffmpeg politely: below-normal priority on Windows so the render never steals
# CPU from whatever the user has in the foreground.
_LOWPRI = subprocess.BELOW_NORMAL_PRIORITY_CLASS if os.name == "nt" else 0

def _mix(clip: str, vo: str, out: str, clip_dur: float, vo_dur: float):
    target = clip_dur - 0.3
    atempo = min(1.18, vo_dur / target) if vo_dur > target else 1.0
    vof = "adelay=300|300,volume=1.7"
    if atempo > 1.001:
        vof = f"atempo={atempo:.3f}," + vof
    # -c:v copy => video is NOT re-encoded (only AAC audio), so each job is light;
    # -threads 1 keeps a single ffmpeg from grabbing every core.
    subprocess.run(["ffmpeg","-v","error","-threads","1","-i",clip,"-i",vo,"-filter_complex",
        f"[0:a]volume=0.30[bg];[1:a]{vof}[v];[bg][v]amix=inputs=2:duration=first:"
        f"dropout_transition=2:normalize=0[a]",
        "-map","0:v","-map","[a]","-c:v","copy","-c:a","aac","-b:a","160k","-y",out],
        capture_output=True, creationflags=_LOWPRI)

def cmd_voiceover(args):
    import langmaps as L
    import manifest as M
    src = Path(args.src).resolve()
    dst = Path(args.dst).resolve()
    data = M.load_manifest(src)
    app = json.loads(Path(args.app_config).read_text(encoding="utf-8"))
    bank = json.loads(Path(args.vo_bank).read_text(encoding="utf-8"))
    langs = [x.strip() for x in args.target_langs.split(",") if x.strip()]
    scratch = dst / "_vo_tmp"; scratch.mkdir(parents=True, exist_ok=True)

    clips = [v for v in data.get("videos", [])
             if not v.get("has_voice") and v.get("vertical") not in (None, "skip")
             and (not args.vertical or v.get("vertical") == args.vertical)]
    if args.limit:
        clips = clips[:args.limit]
    jobs = []
    for i, v in enumerate(clips):
        master = (v.get("outputs") or {}).get("_")
        if not master or not os.path.exists(master):
            print(f"  SKIP-NOMASTER {v.get('renamed')}", flush=True); continue
        angle = v.get("angle") or "other"
        ddmmnn = re.sub(r"\D", "", Path(v.get("renamed") or "x").stem)[-6:] or f"{i:06d}"
        cdur = _dur(master); tier = "long" if cdur >= LONG_TIER_S else "short"
        for lang in langs:
            blk = (bank.get(lang) or {}).get(angle) or (bank.get(lang) or {}).get("care-hack")
            if not blk:
                print(f"  NO-SCRIPT {lang}/{angle}", flush=True); continue
            text = _fill(blk.get(tier) or blk.get("short") or "", app)
            if not text.strip(): continue
            # native voices per language from app config (rotate within the lang for
            # variety); fall back to the 4 multilingual voices if a lang isn't mapped.
            vlist = (app.get("vo", {}).get("voices") or {}).get(lang) or [v for v, _ in VOICE_ROTATION]
            vidx = (i + langs.index(lang)) % len(vlist)
            rate = app.get("vo", {}).get("rate", "+6%")
            tfolder = L.iso_to_english(lang); code = lang.upper()
            outdir = dst / f"VOICED_{tfolder}" / angle; outdir.mkdir(parents=True, exist_ok=True)
            out = outdir / f"{code}-VO_{ddmmnn}.mp4"
            jobs.append({"v": v, "lang": lang, "master": master, "cdur": cdur,
                         "text": text, "voices": vlist, "vidx": vidx, "rate": rate,
                         "out": str(out), "vo": str(scratch / f"{code}_{ddmmnn}.mp3")})
    # bounded concurrency so the PC never overloads: ~half the cores (cap 6),
    # ffmpeg runs in a thread so TTS (network) and mix (CPU) overlap.
    cpu = os.cpu_count() or 4
    conc = getattr(args, "concurrency", 0) or max(2, min(6, cpu // 2))
    print(f"[voiceover] {len(jobs)} VO jobs ({len(clips)} clips × {len(langs)} langs)  "
          f"voices=native-per-lang  concurrency={conc} (cpu={cpu}, below-normal prio)", flush=True)

    async def run_all():
        sem = asyncio.Semaphore(conc)
        prog = {"n": 0, "ok": 0}
        total = len(jobs)

        async def one(j):
            async with sem:
                try:
                    await _tts_robust(j["text"], j["voices"], j["vidx"], j["rate"], j["vo"])
                    vdur = await asyncio.to_thread(_dur, j["vo"])
                    await asyncio.to_thread(_mix, j["master"], j["vo"], j["out"], j["cdur"], vdur)
                    if os.path.exists(j["vo"]):
                        try: os.remove(j["vo"])
                        except OSError: pass
                    if os.path.exists(j["out"]) and os.path.getsize(j["out"]) > 50000:
                        prog["ok"] += 1
                        j["v"].setdefault("vo_outputs", {})[j["lang"]] = j["out"]
                    else:
                        print(f"    THIN {os.path.basename(j['out'])}", flush=True)
                except Exception as e:
                    print(f"    ERR {os.path.basename(j['out'])}: {e}", flush=True)
                prog["n"] += 1
                if prog["n"] % 50 == 0 or prog["n"] == total:
                    print(f"  {prog['n']}/{total}  ok={prog['ok']}", flush=True)

        await asyncio.gather(*(one(j) for j in jobs))
        return prog["ok"]
    ok = asyncio.run(run_all())
    M.merge_save(src, data["videos"], ("vo_outputs",))
    try: scratch.rmdir()
    except Exception: pass
    print(f"[voiceover] done: {ok}/{len(jobs)} voiced clips -> {dst}", flush=True)
