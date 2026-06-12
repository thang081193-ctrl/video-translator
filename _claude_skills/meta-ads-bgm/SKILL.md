---
name: meta-ads-bgm
description: >-
  Curate royalty-free background music (Pixabay / Mixkit — free for commercial use, no
  attribution) for ad-video packs, grouped by MOOD/THEME and mapped to the meta-ads-prepare
  brandpass `--bgm-pool` clusters (B_uplifting / C_lofi_chill / A_calm_nature / D_corporate),
  delivered as ready-to-click download links. Use this whenever the user asks to find, curate,
  or suggest background music / BGM / soundtrack / "nhạc" for videos, ads, or Reels by theme or
  mood; wants no-copyright / royalty-free music WITH download links; needs to fill, build, or
  swap a `--bgm-pool`; asks which music fits a study / tech / cozy / uplifting / calm video; or
  wants to replace source BGM to avoid Meta copyright flags. Trigger even when they don't say
  "royalty-free" explicitly — any request for music to put under ad creatives counts.
---

# Meta Ads BGM — royalty-free music finder, by theme

Picks **safe, free-for-commercial, no-attribution** music for ad creatives and hands the user
**clickable download links grouped by mood**, ready to drop into a `--bgm-pool/<cluster>/` and
re-score with `meta-ads-prepare(-ultimate) brandpass`. Two reasons this matters: (1) competitors'
source BGM is a Meta copyright-flag risk, and (2) the *mood* of the track changes conversion —
a study-hack hook wants energy, a parent testimonial wants warmth.

## The 4 mood clusters (match the brandpass `bgm_cluster` names exactly)

Each video the pipeline classifies already carries a `bgm_cluster`. Group tracks under the SAME
names so `brandpass --bgm-pool <pool> --bgm-mode by-mood` routes each clip to its folder.

| cluster | feel | fits these angles / content | BPM-ish |
|---|---|---|---|
| **B_uplifting** | upbeat, motivational, feel-good, viral-pop | snap-solve, study-hack, exam-prep, back-to-school; most music-only "hero" demos | 100–128 |
| **C_lofi_chill** | lofi, cozy, relatable, aesthetic-calm | parent-helper, relatable, UGC testimonials, "day in my life" | 70–90 |
| **A_calm_nature** | calm, ambient, wellness, soft piano/nature | plant / home / wellness / beauty apps, spa & before-after | 60–85 |
| **D_corporate** | clean tech, modern, innovation, minimal electronic | ai-assistant, math-trick, calculator, fintech / productivity / "AI" tech demos | 90–120 |

## The one rule that most affects quality: voiced vs music-only

- **VOICED clips** (have a voiceover — `VOICED_*` campaigns): pick the **calmer, more instrumental**
  option in the cluster. The track is a *bed* that sits UNDER the voice — the pipeline
  loudness-normalizes it 11–14 dB below the voice automatically (measured mix, see
  meta-ads-prepare → "Voice Audibility QA"), so master loudness doesn't matter when picking;
  but a busy vocal-sample track still fights the VO *spectrally*. No vocals, gentle movement.
- **Music-only clips** (`BGM_UNIVERSAL`, no voice): pick the **catchiest / hookiest** track — here
  the music IS the hero that stops the scroll. Trendier, bigger drop, vocal chops OK.

## Curated seed pool (Pixabay — start here, all free-commercial-no-attribution)

Hand these first; they're vetted go-tos. Each link is a track page with a green **Download** button.

**B_uplifting**
- Feel Good Upbeat Motivational Pop — https://pixabay.com/music/upbeat-feel-good-upbeat-motivational-pop-312622/
- Happy Upbeat Pop (Intro Theme) — https://pixabay.com/music/upbeat-happy-upbeat-pop-background-music-intro-theme-286239/
- Inspirational Motivational Music — https://pixabay.com/music/upbeat-inspirational-motivational-music-311778/
- Motivational Uplifting (Outdoors) — https://pixabay.com/music/upbeat-outdoors-no-copyright-music-motivational-uplifting-388463/
- Good Mood / Happy Upbeat — https://pixabay.com/music/pop-good-mood-happy-upbeat-background-music-123759/ (hookier → music-only)
- Upbeat Happy Indie Pop — https://pixabay.com/music/indie-pop-upbeat-happy-indie-pop-186815/ (hookier → music-only)

**C_lofi_chill**
- Lofi Study – Calm Peaceful Chill Hop — https://pixabay.com/music/beats-lofi-study-calm-peaceful-chill-hop-112191/
- Good Night – Lofi Cozy Chill — https://pixabay.com/music/beats-good-night-lofi-cozy-chill-music-160166/
- Good Night / Lo-Fi Vibes — https://pixabay.com/music/beats-good-night-lo-fi-vibes-172535/
- Ambient Night – Chill Lofi (cozy) — https://pixabay.com/music/beats-ambient-night-lofi-chill-beat-hip-hop-sleepy-lazy-cozy-149582/

**D_corporate**
- Inspiring Technology — https://pixabay.com/music/corporate-inspiring-technology-background-179473/
- Technology Inspiring Innovation — https://pixabay.com/music/corporate-technology-inspiring-innovation-3914/
- Corporate Technology Inspirational — https://pixabay.com/music/corporate-corporate-technology-inspirational-background-music-152967/
- Innovation Technology — https://pixabay.com/music/corporate-innovation-technology-149933/

**A_calm_nature** — no fixed seeds; search per pack (queries below). Good descriptors: *soft piano,
calm ambient, peaceful nature, spa, meditation, warm acoustic*.

## Finding MORE specific tracks (so links are real, never invented)

NEVER fabricate a Pixabay URL — they look like `pixabay.com/music/<genre>-<slug>-<id>/` and the id
must be real. To get fresh specific tracks, **WebSearch** (it reliably surfaces real track-page
URLs), optionally confirm with WebFetch:

Query template: `Pixabay <mood descriptor> royalty free music download track no copyright`
- B_uplifting → "feel good upbeat motivational pop", "happy energetic", "uplifting corporate pop"
- C_lofi_chill → "lofi study chill", "cozy lofi", "chill aesthetic beat"
- A_calm_nature → "calm soft piano", "peaceful ambient nature", "spa meditation"
- D_corporate → "inspiring technology", "innovation corporate", "minimal tech"

Browse pages (also one-click downloads): `https://pixabay.com/music/search/<url-encoded query>/`

**Other free, no-attribution libraries** if the user wants variety or fresh tracks:
- Mixkit — no sign-up: https://mixkit.co/free-stock-music/ (tags: `/tag/uplifting/`, `/tag/corporate/`)
- Uppbeat (free tier, may need credit on free plan), Chosic, YouTube Audio Library (needs YT Studio).

## Licensing — keep it Meta-safe

Pixabay Content License and Mixkit License both allow **commercial use with no attribution**.
Still glance at each page's license line before downloading (a rare track can carry extra terms),
and prefer fully-instrumental tracks for VOICED beds. This avoids the copyright/audio-match flags
Meta throws when an ad reuses a competitor's licensed song.

## Output format — what to hand the user

Write a **`BGM_DOWNLOAD_GUIDE.md`** into the pack folder (next to the campaign tree) AND echo it in
chat. Structure: one section per cluster the pack actually uses (read the manifest's `bgm_cluster`
values, or run `bgm-suggest` below), each with 3–6 specific track links + a "🔎 more" search link,
plus the voiced-vs-music-only note and the apply steps. See the companion guide pattern produced for
the Math 0806 pack for reference.

## Hook into the existing pipeline

1. **(optional) Per-clip routing + shopping list** — meta-ads-prepare-ultimate already has an advisor:
   `python run.py bgm-suggest --src "<pack>" --countries "US,FR,BR,..." [--write]`
   → writes `bgm_suggestions.csv` + `bgm_shopping_list.md` (per-video cluster × Pixabay queries) and,
   with `--write`, stamps a refined `bgm_cluster` onto each manifest entry. This skill adds the
   **curated specific tracks** on top of those queries.
2. **Build the pool**: `<pool>/B_uplifting/  <pool>/C_lofi_chill/  <pool>/A_calm_nature/  <pool>/D_corporate/`,
   3–5 tracks each (more variety = better Andromeda dedup, since each output picks a random track).
3. **Re-score by mood** (write to a FRESH `--dst` so the current pack stays intact, because outputs
   are skipped if they already exist):
   `python run.py brandpass --src "<pack-src>" --dst "<pack>_bgm" --vertical <v> --watermark <logo> --bgm-pool "<pool>" --bgm-mode by-mood --workers 6`
   then re-run the end-card trim + real-outro append (see meta-ads-prepare-ultimate notes).
4. **Priority**: the music-only `BGM_UNIVERSAL` clips first — they carry the competitors' source music
   (real flag risk). VOICED clips already mix BGM under the new voiceover, so they're lower risk.

> Repo sync: live skills under `~/.claude/skills/` aren't git-tracked. If the user keeps a repo copy
> (`_claude_skills/`), mirror this folder there too so a `git pull` re-sync keeps it.
