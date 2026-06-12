---
name: meta-ads-voiceover
description: Add AI voiceover (Edge-TTS, NATIVE voice per language) to BGM-only / voiceless clips → localized VOICED Meta-ads campaigns, for ANY app. Use when you have B-roll / music-only ad clips (e.g. BGM_UNIVERSAL masters from a meta-ads-prepare-ultimate brandpass) and want spoken, localized versions in N languages. A voiceover pitch converts far better for IAA than music alone.
metadata:
  node_type: skill
  type: meta-ads
---

# meta-ads-voiceover — turn voiceless clips into localized voiced ads

Takes **language-agnostic BGM-only clips** (no speech — pure B-roll + music) and adds an
**app-pitch voiceover in each target language**, using the **native voice** for that language,
then drops the results into a **Meta campaign tree** (folder = campaign, angle subfolder = ad set).
**App-agnostic:** point it at a different `app.json` + `vo_bank_<vertical>.json` and the same engine
localizes any app. The original BGM-only masters are KEPT (reusable in any market by swapping BGM).

> Engine lives in the sibling skill **`meta-ads-prepare-ultimate`** (`voiceover.py` + `run.py voiceover`).
> This skill is the **clear reusable playbook + the two config templates**. It does NOT duplicate code.

---

## When to use
- You ran `meta-ads-prepare-ultimate` (scan → organize → brandpass) and have a `BGM_UNIVERSAL/`
  tree of voiceless masters, and now want spoken localized versions.
- OR you simply have voiceless ad clips + a manifest and want multi-language voiced ads.
- You want the SAME app pitch spoken in 1–30 languages, each in a native-sounding voice.

## Prerequisites
- **Engine present:** `~/.claude/skills/meta-ads-prepare-ultimate/` (has `voiceover.py`, `run.py`,
  `langmaps.py`, `manifest.py`). If missing, re-sync repo `_claude_skills/` → live.
- **Python with deps:** `edge_tts`, plus `ffmpeg`/`ffprobe` on PATH.
  - Home PC `<PY>` = `C:/Users/Thang Dep Dai/AppData/Local/Programs/Python/Python313/python.exe`
  - Office PC `<PY>` = `D:/Dev/Tools/Video Translator/.venv/Scripts/python.exe`
- **A manifest** at `<src>/_ultimate/manifest.json` from a prior ultimate run (the voiceover engine
  reads it to find the BGM-only clips + their brandpassed master paths `outputs["_"]`).
- `<ULT>` = `C:/Users/Thang Dep Dai/.claude/skills/meta-ads-prepare-ultimate` (engine dir).

---

## The steps (do them in order)

### Step 1 — Write `app.json` (the per-app config)
Copy `app.template.json` (in this skill) to the app's root (e.g. `D:/Dev/App Details/<App>/app.json`)
and fill it. This is the ONLY app-specific knobs file. Key fields:
- `app_name`, `vertical`, `tagline`, `usp[]`, `cta`, `store`, `audience` — used to fill `{app}` etc.
- `assets` — `logo`, `outro` (the branded outro mp4 already baked into the masters by brandpass),
  `bgm_pool`.
- `vo.rate` — speaking-rate boost for energy (default `"+6%"`).
- `vo.voices` — **per-language LIST of NATIVE Edge voices** (see Step 3). This is the important one.

### Step 2 — Author `vo_bank_<vertical>.json` (the VO scripts)
Copy `vo_bank.template.json` and author the scripts. Shape: `{ lang: { angle: { short, long } } }`.
- **One script per (language × angle × duration-tier).** `short` = clips < 22s, `long` = clips ≥ 22s.
- Use the **`{app}`** placeholder everywhere the app name appears (also `{cta}`, `{tagline}`).
- **Opus (this chat) authors every script** — do NOT machine-translate. Style = a **B-roll app PITCH**
  (hook → USP → CTA), NOT a narration of the specific visual, so ONE script fits every clip of that
  angle. Keep `short` ~1–2 sentences, `long` ~4–6. Localize idiomatically per language (native ad copy,
  not literal). For EN write the master, then localize to each target language.
- Angles must match the manifest's angle keys (typical plant set: `care-hack`, `how-to-scan`,
  `feature-demo`, `beginner`, `diagnosis-rescue`). Unknown angle → engine falls back to `care-hack`.
- **Validate** before rendering:
  ```bash
  PYTHONIOENCODING=utf-8 "<PY>" -c "import json;b=json.load(open(r'<vo_bank>',encoding='utf-8'));print(len(b)-1,'langs', sum(len(a) for k,a in b.items() if k!='_note'),'angle-blocks')"
  ```

### Step 3 — Set NATIVE voices per language (in `app.json` → `vo.voices`)
**Critical for quality.** Each language uses ITS OWN native Edge voice (a LIST, rotated within the
language for variety). Do NOT let the EN multilingual voices read AR/HI/BN — they sound American.
- EN = the 4 expressive multilingual voices: `en-US-AvaMultilingualNeural`, `EmmaMultilingual`,
  `AndrewMultilingual`, `BrianMultilingual`.
- Others = native, e.g. `es`→`es-ES-Elvira`/`es-MX-Dalia`, `ar`→`ar-SA-Zariyah`/`ar-EG-Salma`,
  `hi`→`hi-IN-Swara`/`hi-IN-Madhur`, `bn`→`bn-BD-Nabanita`/`bn-IN-Tanishaa`, `de`→`de-DE-Katja`/`Conrad`,
  `it`→`it-IT-Elsa`/`Diego`, `pl`→`pl-PL-Zofia`/`Marek`. See `app.template.json` for a full set.
- Unmapped lang → engine falls back to the 4 EN multilingual voices.
- List Edge voices: `"<PY>" -m edge_tts --list-voices` (or `edge-tts --list-voices`).

### Step 3.5 — (Recommended) render a small SAMPLE first
Before a big render, hear the voices — especially non-Latin scripts (AR/HI/BN):
```bash
PYTHONIOENCODING=utf-8 "<PY>" "<ULT>/run.py" voiceover --src "<src>" --dst "<dst>_sample" \
    --app-config "<app.json>" --vo-bank "<vo_bank>" --target-langs <langs> --vertical <v> --limit 2
```
Listen, confirm the native accent is right, then delete the sample folder.

### Step 4 — Render the full VO (parallel + polite)
```bash
PYTHONIOENCODING=utf-8 "<PY>" "<ULT>/run.py" voiceover \
    --src "<src>" --dst "<dst>" \
    --app-config "<app.json>" --vo-bank "<vo_bank>" \
    --target-langs es,pt,id,hi,fr,ar,bn  --vertical <vertical>  [--concurrency N]
```
- `--dst` = the SAME `0506`-style campaign tree from brandpass → the VO files merge straight into
  `VOICED_<Language>/<angle>/` next to the natively-voiced clips (distinct `-VO_` suffix, no collision).
- **`--concurrency`** default = `max(2, min(6, cpu//2))`. ffmpeg runs `-threads 1` + **below-normal
  priority** + `-c:v copy` (video not re-encoded) → a 1500+ clip run finishes in ~15 min and the
  machine stays usable. Raise only if the machine is idle.
- Each TTS call **retries + rotates voice** on a transient Edge "no audio" blip → no gaps at scale.
- Per clip × lang: picks `short`/`long` by duration, fills `{app}`, then the **measured voice-first
  mix** — VO loudness-normalized to −16 LUFS, clip BGM placed 12 dB UNDER the VO (both measured via
  ffmpeg loudnorm; blind 0.30/1.7 ratios only as fallback), `amix normalize=0` + limiter,
  atempo≤1.18× to fit. The VO can never be drowned by a hot BGM master. Output:
  `<dst>/VOICED_<Language>/<angle>/<CODE>-VO_DDMMNN.mp4`. Records `vo_outputs{lang:path}` in manifest.
- **Branded outro is inherited** from the master (`-c:v copy`) — no extra outro step needed.

### Step 5 — Campaign-tree naming = ENGLISH
`VOICED_<Language>` uses the recognizable **English** name (`langmaps.iso_to_english`): `VOICED_Hindi`,
`VOICED_Arabic`, `VOICED_Bengali`, `VOICED_Spanish`… — NOT native-script autonyms (हिन्दी/العربية),
which are hard to read for Meta upload. (The `--src` source working tree keeps autonym
`<language_folder>/`; only the deliverable campaign folders are English.)

### Step 6 — QA
```bash
# count: expect 219 (or N) VO per language, 0 failures in the render log
# spot-check a non-Latin language has a real audio stream:
ffprobe -v error -show_entries stream=codec_type:format=duration -of default=noprint_wrappers=1 "<one VO mp4>"
# verify the last frame is the branded outro (grab frame at duration-1s, view it)

# VOICE AUDIBILITY (bắt buộc): LUFS −20…−11, whisper-tiny must recover words on every VO file
python "<skills-dir>/meta-ads-prepare/qa_voice_mix.py" "<dst>" --sample 10 --whisper --expect-voice
# 0 recovered words on a VO clip = voice drowned/missing → FAIL (exit 1). Spec & thresholds:
# meta-ads-prepare SKILL.md → "Voice Audibility QA".
```
Confirm: per-lang count correct, `codec_type=audio` present, duration sane, outro shows.

### Step 7 — Scale to MORE languages later
1. Add the new langs to `app.json` → `vo.voices` (native voices).
2. Add the new langs to `vo_bank_<vertical>.json` (Opus authors the 5×{short,long} blocks).
3. Re-run Step 4 with `--target-langs <new langs only>` into the same `--dst`. Idempotent; existing
   langs untouched.

### Step 8 — Rename an ALREADY-BUILT autonym tree to English (one-off migration)
If an older tree has autonym `VOICED_` folders, rename by reading the file CODE inside (avoids typing
autonyms), then regenerate the README:
```powershell
$map=@{EN="English";ES="Spanish";FR="French";PT="Portuguese";DE="German";PL="Polish";IT="Italian";HI="Hindi";ID="Indonesian";AR="Arabic";BN="Bengali"}
Get-ChildItem "<dst>" -Directory | ? { $_.Name -like "VOICED_*" } | % {
  $s=Get-ChildItem $_.FullName -Recurse -Filter *.mp4 | Select -First 1
  $c=($s.BaseName -split '[-_]')[0].ToUpper(); $e=$map[$c]
  if($e -and $_.Name -ne "VOICED_$e"){ Rename-Item $_.FullName -NewName "VOICED_$e" } }
# then regenerate the campaign README (English names + fresh counts):
PYTHONIOENCODING=utf-8 "<PY>" -c "import sys;sys.path.insert(0,r'<ULT>');import run;from pathlib import Path;run._write_campaign_readme(Path(r'<dst>'))"
```

---

## Gotchas (learned the hard way)
- **Native voices, not multilingual, for non-EN** — Ava/Emma read Spanish OK but give AR/HI/BN an
  American accent. Always map native voices in `app.json` → `vo.voices`.
- **DDMMNN code** must be taken from `Path(renamed).stem` BEFORE stripping non-digits, else `.mp4`'s
  "4" leaks into the date code. (Already handled in the engine.)
- **Edge transient "No audio received"** happens even at concurrency 1 (~1/1500). The engine's
  `_tts_robust` retries + rotates voice; don't panic over a single one.
- **Don't overload the PC** — keep `--concurrency` ≈ half the cores; the engine already sets ffmpeg
  to `-threads 1` + below-normal priority. Render a sample first.
- **Source vs campaign naming:** `--src` tree = autonym working folders; `--dst` campaign tree =
  English `VOICED_` folders. Don't "fix" the source folders — organize/dub depend on autonyms.
- **CSVs are fine in ISO codes** (`es`/`hi`/`ar`) — only the visual campaign FOLDERS were switched to
  English; no CSV change needed.
- **ElevenLabs** is the paid near-human upgrade (32 langs) for proven winning angles; Edge native is
  the free workhorse.

## Files in this skill
- `SKILL.md` — this playbook.
- `app.template.json` — copy → `<App>/app.json`, fill app name/usp/cta + native voices.
- `vo_bank.template.json` — copy → `<App>/vo_bank_<vertical>.json`, Opus authors the localized scripts.

## Re-sync (live ↔ repo)
LIVE `~/.claude/skills/meta-ads-voiceover/` is NOT git-tracked; the repo copy is
`_claude_skills/meta-ads-voiceover/`. After any pull that touches it, copy repo → live
(`SKILL.md`, `app.template.json`, `vo_bank.template.json`).
