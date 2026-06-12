---
name: meta-ads-reels
description: End-to-end Meta Ads Reels production pipeline for IAA-monetized apps. Auto-runs when user provides a folder of source videos + an app context. Classifies by language → discovers creative angles → converts to Reels 1080×1920 → brand-passes (dedup evasion + rebrand) → generates ads kit (Primary text + Headlines + country block + campaign structure) → organizes into upload-ready campaign folders. Triggers when user mentions "làm pack Meta ads", "build Meta ads pack", "Meta ads kit cho videos", "process videos for Meta Ads", "Reels pack cho app", "rebrand + ads kit", "FANCAM pack", any request that pairs a video source folder with an app/brand for Meta Ads upload.
---

# Meta Ads Reels — End-to-end production pipeline

Consolidates 4 sub-skills + adds campaign organization layer. Output = upload-ready campaign folders for Meta Ads Manager.

---

## §1 — When to auto-run (trigger phrases)

Vietnamese:
- "làm pack Meta ads cho videos"
- "build Meta ads cho app này"
- "pack ads cho [app] với videos [folder]"
- "tạo Reels pack" / "convert + rebrand + ads kit"
- "FANCAM pack" (or similar creative-line pack)

English:
- "build Meta Ads pack from videos"
- "process this video folder for Meta Ads"
- "rebrand + ads kit for app X"
- "Meta Reels pipeline"

Skip (use individual sub-skill instead):
- Single video conversion only → `convert-video`
- Single video rebrand only → `meta-ads-prepare`
- Just country tier list → `meta-ads-iaa-2026`
- Just classify by language → `classify-translate-videos`

---

## §2 — Required inputs (ASK if missing, do NOT proceed without)

| Input | Format | Example |
|---|---|---|
| **Source videos folder** | Absolute path | `D:/Dev/App Details/<App>/Video/<MMDD>/` |
| **App context** | Play Store URL + app name | `https://play.google.com/store/apps/details?id=com.x.y` |
| **Brand logo PNG** | Absolute path, 512×512+ | `D:/Dev/App Details/<App>/Logo.png` |
| **Brand background PNG** | 1080×1920 per [meta-ads-prepare brand-bg](../meta-ads-prepare/SKILL.md#brand-bg-png-legacy---brand-bg) | `D:/Dev/App Details/<App>/brand_bg_1080x1920.png` |
| **Brand text** | App name + tagline | `"Artify Gen" + "AI Photo Studio"` |
| **Budget level** | $/day | `$50/day` (1 test campaign) → 6+ videos; `$250/day` → 5 campaigns × $50; `$500+/day` → 10 campaigns |
| **Confirm before batch** | yes/no | Brand-pass batch takes 1.5-2.5 hours, ask before kicking off |

**If brand_bg PNG missing**: give user the DALL-E prompt from [meta-ads-prepare brand-bg](../meta-ads-prepare/SKILL.md#brand-bg-png-legacy---brand-bg) and pause.

---

## §3 — 7-step workflow (auto-execute in order)

### Step 1 — Organize raw downloads (classify + dedupe)

Sub-skill: **`classify-translate-videos`**

Input: Mixed-language folder (e.g. `Unknown/`, `English/`, `Italiano/` from Meta Video Download extension)
Action:
- MD5 hash all sources → dedupe (capture duplicate count)
- Extract 4-up vision grids per unique creative
- Classify by language via vision (Mode B since IAA ads have boilerplate English text)
- Move to `<MMDD>/<NativeLangFolder>/<CODE>_<MMDD><NN>.mp4`

Output: organized language folders with deduped + renamed videos.

### Step 2 — Discover creative angles (classify by product, not hook)

Use 2-pass vision tagging via parallel subagents:
- Pass 1: tag each unique creative with `{lang, angle_label, desc, ux_pattern}` — broad freeform labels
- Pass 2: re-tag for PRODUCT (what the CTA sells, not the hook). Critical because ads use misleading hooks (fake news, birthday photobooth, etc.) that lead to FANCAM/stadium morph.
- Cluster into canonical angle list (e.g. FANCAM, AI_DANCE, BEAUTY_ATLAS, MOVIE_SCENE, AI_PORTRAIT, COUNTRIES, WALLPAPER, ENHANCE, OTHER)
- Optional FANCAM sub-clustering by sport (MLB/KBO/F1/NBA/SOCCER/UFC/...) OR merge into single FANCAM bucket per user preference

Output: `<MMDD>/<AngleFolder>/<LangFolder>/<ANGLE>_<LANG>_<MMDD><NN>.mp4`

### Step 3 — Ensure Reels 1080×1920 format

Skip this step. Brand-pass (step 4) handles aspect conversion via its `pad_bg_image` + cropdetect logic.

If brand-pass is being skipped (rare), invoke sub-skill `convert-video` with Reels preset.

### Step 4 — Brand-pass (dedup evasion + rebrand)

Sub-skill: **`meta-ads-prepare`**

For each video in target angle folder:
```python
brand_pass_video(
    input_path=src,
    output_path=dst,
    watermark_image=LOGO,
    watermark_size=140,
    outro_logo_image=LOGO,
    outro_logo_size=300,
    outro_title=BRAND_TITLE,
    outro_subtitle=BRAND_SUB,
    trim_endcard=True,
    pad_bg_image=BRAND_BG,    # 1080×1920 per spec
    random_seed=None,          # different per run = unique fingerprint
)
```

Run via batch wrapper: `scripts/batch_brand_pass_fancam.py` with `--workers 2` (4 OOMs MKL on most CPUs).

Output: `_branded/<AngleFolder>/<LangFolder>/<ANGLE>_<LANG>_<MMDD><NN>.mp4` — all 1080×1920, all unique fingerprints, all branded with app's logo + name + tagline.

**Voice Audibility (auto):** voiced sources get the measured voice-first mix — voice −16 LUFS, BGM 11–14 dB under, post-mix `VOICEMIX` gate (never pass `bgm_volume=`, that's the legacy ungated mix). Audit voiced deliverables before upload: `python "<skills-dir>/meta-ads-prepare/qa_voice_mix.py" "_branded" --sample 10 --whisper --expect-voice`. Spec: meta-ads-prepare SKILL.md → "Voice Audibility QA".

**Time estimate**: ~40-80s per video on consumer CPU. For 300+ videos at 2 workers: 2-3 hours. ASK USER BEFORE KICKING OFF.

Known fails + retry: 1-3% videos fail with empty Whisper transcript → recover with fallback transcript. See [meta-ads-prepare pitfalls](../meta-ads-prepare/SKILL.md#common-pitfalls--how-to-recover).

### Step 5 — Build Meta Ads kit

Sub-skill: **`meta-ads-iaa-2026`**

Determine campaign count from budget:
- < $200/day → 1 test campaign (6 videos)
- $200-500/day → 5 campaigns × $50/day (geo-split OR angle-split)
- $500-1.5k/day → 5-8 campaigns
- $1.5k+/day → 10 campaigns

For each campaign, generate:
- **5-10 Primary text** variants (≤125 chars, 1 emoji, CTA "Try Free", angle-specific theme: FOMO / Speed / Curiosity / Social Proof / Direct CTA)
- **5-10 Headlines** (≤40 chars, action verb)
- **1 country block** (T1+T2 EN-tolerant combined per IAA-2026 best practice)
- **Settings spec**: Advantage+ App Promotion, VO IAA optimization, Highest Volume bid, Advantage+ Audience+Placements, Dynamic Creative ON, CTA "Try Free"

### Step 6 — Organize into campaign folders

Create `<MMDD>/_branded/<AngleFolder>/_campaigns/<Cn_AngleName>/` per campaign with:

```
C1_AngleName/
├── videos.txt          ← 6 filenames, 1/line, plain text
├── primary_text.txt    ← 5 variants, 1/line, plain text, NO numbering/backticks
├── headlines.txt       ← 5 variants, 1/line, plain text
├── _ASSETS.md          ← markdown reference (videos + primary + headlines + settings)
└── 6 × <ANGLE>_<LANG>_<MMDD><NN>.mp4
```

Top-level:
```
_campaigns/
├── README.md           ← overview + launch order + kill rules + structure
├── country.txt         ← shared T1+T2 EN block, paste into "Add Locations in Bulk"
├── C1_*/
├── C2_*/
└── ...
```

### Step 7 — Launch checklist + summary

Deliver to user:
1. Path to `_campaigns/` folder
2. Per-campaign upload flow:
   - Mở `country.txt` → copy → paste vào "Add Locations in Bulk"
   - Drag-drop 6 videos vào Meta Ads
   - Copy mỗi dòng `primary_text.txt` vào "Primary text" field
   - Copy mỗi dòng `headlines.txt` vào "Headlines" field
3. Settings checklist (from §5)
4. Launch order: stagger 15-30 min between campaigns (avoid bot-like pattern)
5. Kill/scale rules (D5-D7 check):
   - ROAS > 1.5x → scale +20% CBO
   - ROAS 0.8-1.5x → hold, check D14
   - ROAS < 0.5x sau 5d → pause
   - Frequency > 3.0 → refresh creative

---

## §4 — Output deliverable contract

User receives:
- `_campaigns/` folder ready for direct Meta Ads Manager upload
- 30+ branded mp4 files (1080×1920 Reels, unique fingerprints, app-branded)
- Plain `.txt` files for easy copy-paste into Meta UI fields
- `README.md` with launch + scale playbook
- Audit log showing what got processed (counts, errors, recovery)

---

## §5 — Decision points (PAUSE for user)

| Decision | When to pause | Default if no input |
|---|---|---|
| **Confirm brand assets** | Step 4 start | NEVER auto-proceed without confirmed Logo.png + brand_bg PNG |
| **Confirm batch run** | Step 4 (brand-pass is expensive) | Always ask, show ETA |
| **FANCAM sport split?** | Step 2 | Default: merge into single FANCAM bucket (user can split later) |
| **Campaign count + budget** | Step 5 | Ask explicitly; don't guess |
| **Geo split or angle split?** | Step 5 | Default to angle split (1 angle per campaign) |
| **Replace buggy aspect videos?** | After step 4 if non-9:16 sources exist | Default: replace with 9:16 sources from safe pool (no cutoff risk) |

---

## §6 — Sub-skill references (loaded on demand)

| Sub-skill | Purpose | Auto-invoke? |
|---|---|---|
| [`classify-translate-videos`](../classify-translate-videos/SKILL.md) | Step 1 — language classify + dedupe | Yes |
| [`meta-ads-prepare`](../meta-ads-prepare/SKILL.md) | Step 4 — rebrand + dedup evasion | Yes (full pipeline) |
| [`meta-ads-iaa-2026`](../meta-ads-iaa-2026/SKILL.md) | Step 5 — kit generation | Yes (reference for tier eCPM, budget formulas) |
| [`convert-video`](../convert-video/SKILL.md) | Step 3 (rare) — standalone Reels conversion | No, brand-pass handles |
| [`meta-ads-tiers`](../meta-ads-tiers/SKILL.md) | Older meta-ads ref | Superseded by meta-ads-iaa-2026 |

Parked in repo `_claude_skills/` (rarely needed):
- `analyze-ad-angles` — pre-built taxonomy, inlined into Step 2 above
- `super-saiyan-translate` — alternative path for international markets (translate creative before brand-pass)

---

## §7 — Defaults for Artify Gen (current primary app)

```python
APP_NAME    = "Artify Gen"
APP_TAGLINE = "AI Photo Studio"
APP_URL     = "https://play.google.com/store/apps/details?id=com.artifygen.aiphotostudio.enhancer.aiart"
LOGO        = "D:/Dev/App Details/Artify Gen/Logo.png"
BRAND_BG    = "D:/Dev/App Details/Artify Gen/brand_bg_1080x1920.png"
WORKERS     = 2  # OOM-safe for MKL on consumer CPU
```

For other apps: replicate the asset structure under `D:/Dev/App Details/<AppName>/`.

---

## §8 — Reference data (snapshots)

### IAA Tier 1 countries (2026)
```
United States, Japan, Australia, Canada, United Kingdom, South Korea, Saudi Arabia, Taiwan, Germany
```

### IAA Tier 2 (EN-tolerant)
```
Netherlands, Sweden, Norway, Denmark, Finland, Ireland, Singapore, Hong Kong, Israel, United Arab Emirates, Italy, Austria, Switzerland
```

### Default country block (T1+T2 EN combined, paste-ready)
```
United States, Canada, United Kingdom, Australia, New Zealand, Ireland, Netherlands, Sweden, Norway, Denmark, Finland, Singapore, Hong Kong, Israel, United Arab Emirates, Saudi Arabia, South Korea, Taiwan
```

### Andromeda thresholds (2026)
- Min conv/week/ad set: **50** (below → CPM penalty)
- Min daily budget/ad set: **$50** practical floor
- Refresh creative when: CTR drops 15% OR frequency > 3.0
- Scale rule: +20% every 3-4 days at <$300/day, +10% every 5-7 days at $500+/day

### Brand-bg PNG safe zones (HARD)
- Top brand zone: **y=0-265** (logo + sparkles ONLY)
- Middle video zone: y=265-1655 (MUST be empty)
- Bottom brand zone: **y=1655-1920** (text + tagline ONLY)

See [meta-ads-prepare brand-bg](../meta-ads-prepare/SKILL.md#brand-bg-png-legacy---brand-bg) for full DALL-E prompt template.

---

## §9 — Typical timeline (300 video FANCAM-style batch)

| Phase | Duration |
|---|---|
| 1. Classify + dedupe | 15-20 min (Whisper + vision per unique) |
| 2. Analyze angles | 10-15 min (parallel subagents) |
| 3. Convert (skip — brand-pass handles) | — |
| 4. Brand-pass batch | **~2 hours** (300 videos × 40s @ 2 workers) |
| 5. Build ads kit | 5 min |
| 6. Organize campaigns | 5 min |
| 7. Deliver | <1 min |
| **Total** | **~2.5-3 hours** |

Plan accordingly. Ask user when to kick off step 4.
