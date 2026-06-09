# Angle taxonomy + ad-copy bank

## Content-format ANGLE taxonomy (home-decor ad creatives)

Tag by *creative format*, not by room type (a video can show many rooms).

| Angle | What it looks like | Lang signal |
|---|---|---|
| `HELP_REDESIGN` | Hook frame: an awkward/empty space with distress text ("Help!! 😭") + a red circle/arrow → then numbered options (2., 4., 5., 7.) revealing AI redesigns of that spot. App-install ad (watermark like `homeplanner.info`, CTA "Search AI Home Planner", App Store badge). | overlay text (usually EN) — **baked in** |
| `TIPS_DODONT` | Educational do's & don'ts: ❌/✅ comparisons, "Correct bathroom layout", "Never paint your room until…", layout tiers (Normal/Beginner/Pro), wardrobe/TV-placement rules, hand-drawn or 2D floor-plan diagrams (e.g. Planner 5D). | overlay text (usually EN) — **baked in** |
| `WALKTHROUGH_3D` | Smooth 3D camera fly-through of a finished interior (kitchen/bath/bedroom/wardrobe). Showcase, little/no big text; often a small Chinese creator watermark. | usually none → `visual` |
| `STORAGE_VO` | Longer (60–100 s) narrated walkthrough about storage / space-saving (bunk beds, wardrobes, under-stair, hidden cabinets) with a running narration (real VO or one burned caption fragment per scene). | real VO → dub-able |
| `OTHER` | none of the above. | — |

**Key consequence:** only `STORAGE_VO` (real VO) can be dubbed. HELP/TIPS carry the language
as baked-in overlay graphics (this pipeline can't re-translate that → leave EN, route to
EN-tolerant markets). WALKTHROUGH_3D is language-neutral → usable in every country.

## Campaign structure (angle × country)

Each (angle, country) that makes sense = one campaign (~10–12 videos). Example for FR/TR/ZA:
- FR: `STORAGE_VO/FR` (dubbed), `WALKTHROUGH_3D` (universal)
- TR: `STORAGE_VO/TR` (dubbed), `WALKTHROUGH_3D`
- ZA (English): `STORAGE_VO/EN`, `WALKTHROUGH_3D`, `HELP_REDESIGN`, `TIPS_DODONT`

## Copy bank (DecoAI = style reference; write per-app copy)

Copy is **per app** — ground it in the app's `value_prop` (from `apps/<app>.json`). The
DecoAI bank below is a *style* reference for register/length/CTA, not wording to reuse for a
different app. A full validated DecoAI primary-text + headline bank (EN + FR + TR) per angle
lives at `D:/Dev/Tools/Video Translator/scripts/build_decoai_campaigns_2805.py` (`P_*`/`H_*`
constants). DecoAI themes per angle:

- **STORAGE_VO** — small-room/space-saving stories: "one bedroom, two kids → smart shared
  room", "find hidden storage", "see the redesign before you spend a cent". CTA "Try free".
- **WALKTHROUGH_3D** — instant room redesign: "your room, designer-redesigned in 5 s",
  "pick a style, AI redoes the whole room", "stop scrolling Pinterest".
- **HELP_REDESIGN** (EN only) — awkward-space fix: "that awkward corner? AI knows", "snap →
  5 makeovers", "dead zone to dream spot".
- **TIPS_DODONT** (EN only) — layout authority: "most rooms get the layout wrong — here's
  the fix", "❌ vs ✅ the mistakes you don't notice", "designer rules in an app".

Copy rules: ≤125 chars primary (1 emoji ok), ≤40 char headlines (action verb), CTA "Try
Free" / "Use App". Write copy in the campaign's **target language** (real FR/TR, not
machine-literal). For FR keep the punchy "DecoAI Gratuit" register; for TR natural idiom.

## Country tiers + language → Edge-TTS voice

Match each campaign's country block to (1) the creative language and (2) the app's market
tier. For IAA, "tier" ≈ Audience Network eCPM. **South Africa is a low-eCPM / emerging
market** — its English peers are other emerging English-primary countries, NOT the high-eCPM
T1 anglo block (a separate, higher tier you may *also* run).

**English — emerging tier (group with South Africa):**
```
South Africa, Nigeria, Kenya, Ghana, Philippines, Pakistan, India
```
IAA caveat: cheap installs, low eCPM — watch ROAS D3–D7. India + Philippines are
volume-heavy and can dominate spend (split them out if so). Add Uganda / Tanzania /
Bangladesh / Sri Lanka for more reach.

**English — high tier (T1 anglo + T2 EN-accepting), if you also want premium markets:**
```
United States, Canada, United Kingdom, Australia, New Zealand, Ireland, Netherlands, Sweden, Norway, Denmark, Finland, Singapore, Hong Kong, Israel, South Korea, Taiwan, United Arab Emirates
```

**Dub language → folder, country block, TTS voice:**

| Lang | folder | country block | TTS voice |
|---|---|---|---|
| fr | FR | France (+ Belgium, Switzerland, Canada if francophone) | fr-FR-DeniseNeural |
| tr | TR | Türkiye | tr-TR-EmelNeural |
| en | EN | the English block above, chosen by tier — no dub, reuse originals | — |
| af / zu | AF / ZU | South Africa (if localizing to Afrikaans / Zulu) | af-ZA-AdriNeural / zu-ZA-ThandoNeural |

Names match Meta autocomplete (`United States` not "USA", `United Kingdom` not "UK",
`United Arab Emirates`, `South Korea`, `Hong Kong`). Supported langs: `pipeline/languages.py`.
Translation is **Claude**, not the pipeline's Gemini.
