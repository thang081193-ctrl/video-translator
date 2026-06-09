---
name: classify-translate-videos
description: Classify ungrouped Meta Ad Library videos by language and rename them into a uniform `<CODE>_<MMDD><NN>` format. Use whenever the user asks to sort/classify/rename videos that came out of the Meta Video Download extension — typically when there is an `Unknown` folder full of `meta_ad_*.mp4` + `meta_ad_*.txt` pairs sitting next to language folders like `Deutsch`, `Français`, `English`. Two detection modes: (A) text-based on the `.txt` PRIMARY TEXT, (B) audio+vision fallback when the advertiser uses the same language copy across all countries so PRIMARY TEXT is unreliable.
---

# Classify & rename Meta Ad Library translation videos

The Meta Video Download Chrome extension downloads ad videos as pairs:
- `meta_ad_<libraryId>_HD_<timestamp>.mp4`
- `meta_ad_<libraryId>_HD_<timestamp>.txt` (contains `PRIMARY TEXT:` block — the ad copy)

Ads whose language could not be auto-detected land in an `Unknown/` subfolder. This skill classifies them by language, moves them up to native-name language folders alongside the existing ones, and renames every pair to `<CODE>_<MMDD><NN>`.

The user will sometimes ask to **re-check existing language folders** as well — Meta's auto-classification can be wrong (e.g. an `Italiano/` folder where every video is actually English/Portuguese/Tamil). Treat any folder the user names explicitly the same way as `Unknown/`.

## Folder layout assumed

```
.../<MMDD>/                        ← parent folder named with the download date (e.g. 0905)
    Deutsch/                       ← native-language folder names (already exist for prior batches)
    English/
    Español/
    Français/
    Italiano/
    Português/
    Unknown/                       ← target of this skill — contains meta_ad_*.{mp4,txt}
```

## Conventions

**Folder names — native, matching parent format:**

| Language | Folder | ISO code |
|---|---|---|
| German | `Deutsch` | DE |
| English | `English` | EN |
| Spanish | `Español` | ES |
| French | `Français` | FR |
| Italian | `Italiano` | IT |
| Portuguese | `Português` | PT |
| Arabic | `العربية` | AR |
| Polish | `Polski` | PL |
| Turkish | `Türkçe` | TR |
| Dutch | `Nederlands` | NL |
| Russian | `Русский` | RU |
| Japanese | `日本語` | JA |
| Korean | `한국어` | KO |
| Chinese (simplified) | `中文` | ZH |
| Vietnamese | `Tiếng Việt` | VI |
| Indonesian | `Bahasa Indonesia` | ID |
| Hindi | `हिन्दी` | HI |
| Tamil | `தமிழ்` | TA |

If the user's parent folder uses a different spelling for an existing language, MATCH the existing spelling — do not create a duplicate folder. Always `ls` the parent first to see what exists.

**File names — `<CODE>_<MMDD><NN>`:**
- `<CODE>` = ISO 2-letter code from table above (uppercase)
- `<MMDD>` = parent folder name (the download date), used verbatim
- `<NN>` = zero-padded sequence starting at `01`, sorted by the timestamp in the original filename so order is reproducible
- **Padding width:** `max(2, ceil(log10(file_count)))`. So `01..99` for ≤99 files, `001..125` for 100-999. Renumber the whole folder if it crosses the 99→100 boundary.
- Apply to BOTH `.mp4` and matching `.txt` (same base name)

Example: `meta_ad_1196601676016687_HD_20260509T020854.mp4` → `PL_090501.mp4` (with `.txt` getting the same base).

## Procedure

### 1. List `Unknown/` (and any other folder the user named) and read every `.txt`

```powershell
Get-ChildItem -LiteralPath "<root>\Unknown" -Filter *.txt
```

Read each file. The relevant section is `PRIMARY TEXT:` — the ad copy that follows it indicates the language. Ignore boilerplate like `EU transparency`, `Open Dropdown`, `Quality`, `hd`, `sd`, `PLAY.GOOGLE.COM`, `Install now`, the headline / product name (`Plant Identifier & Care`, `AI Generator Art Nano`, etc.) — these appear in every language and tell you nothing.

**Decide which detection mode applies:**

- **Mode A (text-based):** `PRIMARY TEXT` body differs across files — different scripts, different diacritics, different language. Most batches are this mode. Go to step 2A.
- **Mode B (audio + vision fallback):** `PRIMARY TEXT` is the *same English boilerplate* across every file (and matches what's already in folders like `Italiano/`). The advertiser writes one English copy and runs it everywhere; only the video creative differs by country. Go to step 2B.

### 2A. Text-based detection (Mode A)

Use script clues + characteristic words on the PRIMARY TEXT body:
- **Arabic script** (`ا ب ت ث...`) → AR
- **Devanagari** (`अ आ इ ई...`, `तस्वीरें`, `अपलोड`) → HI
- **Tamil script** (`அ ஆ இ...`, `தமிழ்`) → TA
- **Cyrillic** (`А Б В...`) → RU
- **CJK** → check for Hangul (KO), kana (JA), else ZH
- **Latin** → distinguish by diacritics + function words:
  - Polish: `ł ą ę ż ź ć ń`, words like `Twój`, `roślina`, `aplikacji`, `Zeskanuj`
  - Turkish: `ı ş ğ ç`, words like `Bitkilerin`, `hızlıca`, `acemiden`
  - French: `é è ê à ç`, words like `votre`, `plante`, `découvrez`, `meurent`
  - German: `ä ö ü ß`, words like `Ihre`, `Pflanzen`, `sterben`
  - Spanish: `ñ ¿ ¡`, words like `tus`, `plantas`, `mueren`, `Convierte tus fotos`
  - Portuguese: `ã õ ç`, words like `suas`, `plantas`, `morrendo`, `Seu amor`, `Sáb de fev`, `Ensolarado`, `Envie`, `Transforme suas fotos`
  - Italian: words like `tue`, `piante`, `muoiono`, `Il tuo amore`
  - Vietnamese: tone marks `ă â ê ô ơ ư đ` and combining marks
  - Indonesian: no diacritics, words like `tanaman`, `Anda`

### 2B. Audio + vision fallback (Mode B)

When PRIMARY TEXT is identical English across every file, the .txt is useless and you must look at the actual video. Default approach is **vision (overlay text in frames) as the primary signal, audio (Whisper) as confirmation** — this inverts the user's "audio first" preference because most of these ads are silent except for background music or an English song that has nothing to do with the target language.

Build the detection rig in a system temp folder, not inside the user's video folder.

#### Step 2B-1. Extract 20 s audio + a 4-up frame grid per video

```python
# _classify_extract.py — run once per batch
import os, subprocess, sys
from pathlib import Path

ROOT = Path(r"<absolute path to MMDD parent folder>")
FOLDERS = ["Unknown", "<other folder user asked to recheck>"]   # e.g. "Italiano"
TMP = Path(os.environ["TEMP"]) / "vid_classify"
(TMP / "frames").mkdir(parents=True, exist_ok=True)
(TMP / "grids").mkdir(parents=True, exist_ok=True)

videos = []
for f in FOLDERS:
    if (ROOT / f).exists():
        videos += sorted((ROOT / f).glob("*.mp4"))

for vid in videos:
    wav = TMP / f"{vid.stem}.wav"
    grid = TMP / "grids" / f"{vid.stem}.jpg"

    dur = float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(vid)],
        capture_output=True, text=True
    ).stdout.strip() or "10")

    if not wav.exists():
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(vid),
                        "-t", "20", "-ac", "1", "-ar", "16000", "-vn", str(wav)])

    if not grid.exists():
        # 4 timestamps tiled 2x2 → catches intro hook, mid, end-card in one Read
        seeks = [max(0.5, min(p * dur, dur - 0.5)) for p in (0.10, 0.40, 0.70, 0.95)]
        inputs = []
        for s in seeks:
            inputs += ["-ss", f"{s:.2f}", "-i", str(vid)]
        fc = (
            "[0:v]select=eq(n\\,0),scale=360:-2,setsar=1[a];"
            "[1:v]select=eq(n\\,0),scale=360:-2,setsar=1[b];"
            "[2:v]select=eq(n\\,0),scale=360:-2,setsar=1[c];"
            "[3:v]select=eq(n\\,0),scale=360:-2,setsar=1[d];"
            "[a][b]hstack=inputs=2[ab];[c][d]hstack=inputs=2[cd];"
            "[ab][cd]vstack=inputs=2[out]"
        )
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error"] + inputs +
                       ["-filter_complex", fc, "-map", "[out]",
                        "-frames:v", "1", "-q:v", "3", str(grid)])
```

#### Step 2B-2. Run Whisper for language detection

On this machine `cublas64_12.dll` is missing, so faster-whisper crashes on `device="cuda"`. Use **CPU + `tiny`** — that's plenty for first-pass language detection on a 20 s clip. Wrap each transcribe in try/except.

```python
# _classify_whisper.py
import json, sys
from pathlib import Path
from faster_whisper import WhisperModel

TMP = Path(os.environ["TEMP"]) / "vid_classify"
model = WhisperModel("tiny", device="cpu", compute_type="int8")

results = []
for wav in sorted(TMP.glob("*.wav")):
    try:
        segs, info = model.transcribe(str(wav), language=None, beam_size=1, vad_filter=True)
        seg_list = list(segs)
        text = " ".join(s.text.strip() for s in seg_list)[:200]
        results.append({"stem": wav.stem, "lang": info.language,
                        "prob": round(info.language_probability, 3),
                        "speech": bool(seg_list and len(text) > 5),
                        "text": text})
    except Exception as e:
        results.append({"stem": wav.stem, "lang": None, "prob": 0, "speech": False, "text": f"err: {e}"})

print(json.dumps(results, ensure_ascii=False, indent=2))
```

Run with `PYTHONIOENCODING=utf-8` and unbuffered (`python -u`), redirect stdout to a JSON file. Stream stderr through Monitor (bash, `tail -F | grep`) for live progress.

#### Step 2B-3. Read every grid with the multimodal vision tool

This is the **primary** signal. For each grid, identify the overlay text language. The tile that usually has language-discriminating text is the green/purple CTA chip ("Upload photos", "Just upload one photo", "Envie apenas 2 fotos", "तस्वीरें अपलोड करें", "حمّل صورتك فقط") and any subtitle bar.

Batch the Reads — 8-10 grids per assistant message — to stay efficient.

#### Step 2B-4. Cross-check audio results

- If **vision** says HI/AR/TA/ZH/JA/KO/PT/ES (clear non-Latin script or unmistakable diacritics) → trust vision, ignore Whisper.
- If **vision** says EN and Whisper says EN at high confidence → EN, done.
- If **vision** is ambiguous (no overlay visible at any of the 4 timestamps) and Whisper detected real speech (probability ≥ 0.5, transcript is recognizable not gibberish) → use Whisper.
- If both are ambiguous → re-extract a **dense 4×4 grid** (16 timestamps from 3 % to 99 %) for just that one video, Read it, and decide. If still nothing, ask the user.

#### Step 2B-5. Background-music traps

The same English song ("I'm falling for you, season's they change, but I never do") plays under many of these ads regardless of target country — Whisper will confidently report `en` because of the lyrics. Russian credits like "Редактор субтитров А.Гоева" (subtitle-editor signature appended by some pirated audio source) should also be ignored — they aren't the ad's actual language. Always defer to the overlay in vision when audio is just music or scrolling credits.

### 3. When PRIMARY TEXT is missing or only the boilerplate headline (Mode A)

ASK THE USER — do not guess. The extension sometimes saves before the ad copy DOM rendered, so several files in a single batch may all be missing the body. Surface the count and Library IDs and ask "what language are these?" before moving them. The user often knows from context (e.g. "those are all French").

### 4. Move to language folders at the parent level

Files belong in the language folder at the **parent** level (sibling of `Unknown`), not in a new subfolder inside `Unknown`. If the language folder doesn't exist yet, create it with the native name. If it does, merge into it. After moving all 46+ files out of (say) `Italiano/`, that folder ends up empty — delete it in step 6.

### 5. Rename ALL videos in EACH affected folder (existing + newly moved)

After moving, renumber the entire language folder from `01` so the sequence is contiguous. Sort by timestamp embedded in the original `meta_ad_*` name to keep numbering deterministic. If files are already in `<CODE>_<MMDD><NN>` format from a prior run, sort them too (they sort lexicographically by sequence) — the renumbering is idempotent.

**Use a two-pass rename through unique `__tmp_NNNN` names** to avoid collisions when re-numbering files that already follow the target format. A direct rename can clash (`EN_100501.mp4` → `EN_100501.mp4` while another file also wants `EN_100501.mp4`).

### 6. Verify

- Print final counts per folder. Total before == total after.
- `Unknown/` should be empty (or only contain pairs the user explicitly asked you to leave). Same for any folder that was fully reclassified (e.g. `Italiano/` after a Mode B reclass).
- Empty folders → `rmdir`.

## Python implementation template (canonical — replaces the old PowerShell template)

PowerShell's default cp1252 encoding mangles `हिन्दी`, `العربية`, `தமிழ்` etc. and refuses to print or even open paths with these characters when stdout is captured. Use Python with `PYTHONIOENCODING=utf-8` for everything that touches Unicode folder names.

```python
# _apply.py
import re, shutil, sys
from pathlib import Path

ROOT = Path(r"<absolute path to MMDD parent folder>")
DATE_CODE = "<MMDD>"
sys.stdout.reconfigure(encoding="utf-8")

# (libraryId, target_folder, iso_code) — fill in from detection results
MOVES = [
    # ("1009749055335429", "English", "EN"),
    # ("1170511995200304", "हिन्दी",  "HI"),
    # ...
]

LANG_CODES = {
    "Deutsch": "DE", "English": "EN", "Español": "ES", "Français": "FR",
    "Italiano": "IT", "Português": "PT", "العربية": "AR", "Polski": "PL",
    "Türkçe": "TR", "Nederlands": "NL", "Русский": "RU", "日本語": "JA",
    "한국어": "KO", "中文": "ZH", "Tiếng Việt": "VI", "Bahasa Indonesia": "ID",
    "हिन्दी": "HI", "தமிழ்": "TA",
}

SOURCES = ["Unknown"]  # add more if user asked to re-check, e.g. "Italiano"

def find_pair(lib_id):
    for src in SOURCES:
        for mp4 in (ROOT / src).glob(f"meta_ad_{lib_id}_HD_*.mp4") if (ROOT / src).exists() else []:
            txt = mp4.with_suffix(".txt")
            return mp4, (txt if txt.exists() else None)
    return None, None

# Step 1: move
for lib_id, folder, code in MOVES:
    dst = ROOT / folder
    dst.mkdir(exist_ok=True)
    mp4, txt = find_pair(lib_id)
    if mp4 is None:
        print(f"MISS {lib_id}"); continue
    shutil.move(str(mp4), str(dst / mp4.name))
    if txt: shutil.move(str(txt), str(dst / txt.name))

# Step 2: drop empty source folders
for src in SOURCES:
    sdir = ROOT / src
    if sdir.exists() and not list(sdir.iterdir()):
        sdir.rmdir()

# Step 3: renumber every language folder with dynamic-width padding
def ts_key(name):
    m = re.search(r"_HD_(\d{8}T\d{6})", name)
    return m.group(1) if m else name

for folder, code in LANG_CODES.items():
    fdir = ROOT / folder
    if not fdir.exists(): continue
    videos = sorted(fdir.glob("*.mp4"), key=lambda p: ts_key(p.name))
    if not videos:
        try: fdir.rmdir()
        except OSError: pass
        continue

    width = max(2, len(str(len(videos))))   # 01..99 for ≤99, 001..125 for 100+

    # Two-pass rename through unique tmp names
    staged = []
    for i, v in enumerate(videos):
        base = v.stem
        tmp_mp4 = fdir / f"__tmp_{i:04d}.mp4"
        v.rename(tmp_mp4)
        txt = fdir / f"{base}.txt"
        tmp_txt = fdir / f"__tmp_{i:04d}.txt" if txt.exists() else None
        if tmp_txt: txt.rename(tmp_txt)
        staged.append((tmp_mp4, tmp_txt))

    for idx, (tmp_mp4, tmp_txt) in enumerate(staged, 1):
        new_base = f"{code}_{DATE_CODE}{idx:0{width}d}"
        tmp_mp4.rename(fdir / f"{new_base}.mp4")
        if tmp_txt: tmp_txt.rename(fdir / f"{new_base}.txt")

    print(f"{folder} ({code}): {len(videos)} files, width={width}")
```

Run with:
```powershell
$env:PYTHONIOENCODING = "utf-8"
python -u _apply.py
```

## Pitfalls

- **Don't classify from headline/product name alone.** `Plant Identifier & Care`, `AI Plant Guide & Care`, `AI Generator Art Nano`, `Plant Identifier: Scan & Care` are product names that appear in every language. They are not language signals.
- **Don't trust Meta's auto-classification of existing folders.** A folder named `Italiano/` may contain zero Italian videos — Meta groups by target country, the actual creative may be in any language. When the user says "tôi không tin lắm" / "double-check" / "re-classify", treat that folder exactly like `Unknown/`.
- **Don't trust Whisper alone on these ads.** Most are silent except for stock background music. Vision (overlay text) is the primary signal. Whisper helps only when there's actual speech and probability ≥ 0.5 with a recognizable transcript. Treat anything below 0.3 as a hallucination.
- **Don't run faster-whisper on CUDA on this machine.** `cublas64_12.dll` is not on PATH; it crashes immediately. Use `device="cpu", compute_type="int8"`. The `tiny` model is fast enough on CPU (~3-5 min for 50 clips of 20 s).
- **Don't use the page_id in the URL** as a language signal. The same advertiser runs ads in multiple languages — and sometimes runs the same English copy in all of them.
- **Don't use PowerShell for Unicode folder names.** Default cp1252 encoding garbles `हिन्दी`, `العربية`, `தமிழ்`, `中文`, `日本語`, `한국어`, `Русский` when captured by Tee/Out-File. Use Python with `PYTHONIOENCODING=utf-8`.
- **Match existing folder spelling exactly.** If the parent already has `Português`, do not create a new `Portuguese` or `pt-BR` next to it — case and diacritics matter on Windows for cleanliness even though NTFS is case-insensitive.
- **Numbering resets per folder, not per batch.** A new batch added to `Français` triggers a full renumber of all French videos in that date folder, not just the new ones. The two-pass `__tmp_NNNN` rename is mandatory when files are already in the target format.
- **Pad to 3 digits when a folder crosses 99.** `EN_1005100` < `EN_100599` in alphabetical sort; keep all files in a folder at the same width so listing order matches numerical order.
