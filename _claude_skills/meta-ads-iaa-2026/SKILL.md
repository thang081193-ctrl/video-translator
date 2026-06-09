---
name: meta-ads-iaa-2026
description: Meta Ads playbook for IAA (in-app advertising) monetized apps in 2026. Covers Advantage+ App Promotion (post-AAC), Value Optimization with `AdImpression`, AEM/SKAN 4 setup, Andromeda algorithm rules, 2026 eCPM tiers, budget formulas, scaling cadence, and creative specs for Reels-first delivery. Trigger when user asks about Meta Ads / Facebook Ads / Instagram Ads for an IAA-monetized app, mentions `ad_impression` or `AdImpression` optimization, asks for ROAS strategy with ad-revenue events, or wants country tier / budget / creative guidance for app install campaigns in 2026.
---

# Meta Ads — IAA Playbook 2026

Definitive setup for advertising IAA-monetized apps on Meta. Reflects the post-AAC unified Advantage+ structure (Marketing API v24/v25, Oct 2025–Q1 2026), the Andromeda algorithm rollout (March 2026), removed AEM event-priority slots (June 2025), and shrunken attribution windows (Jan 2026 retired 28-day-view).

Anti-pattern this skill exists to prevent: copying 2023–2024 advice. Most settings changed.

---

## §1 — Campaign objective + structure (post-AAC)

**Use `App Promotion` → Advantage+** every time. Legacy AAC creation removed from Marketing API; old "Automated App Ads" UI redirects.

**Structure budget-first, not "10 campaigns just because":**

| Total daily budget | Recommended structure | Why |
|---|---|---|
| < $200/day | 1 CBO campaign × 2 ad sets | Below this, Andromeda starves any split |
| $200–500/day | **2 CBO + 1 ABO testing** | 2 winners + 1 testbed; CBO scales freely |
| $500–1.5k/day | 3 CBO + 1 ABO + 1 retargeting | Add 3rd geo cluster |
| $1.5k–5k/day | 4–5 CBO + 1 ABO | Each CBO ≥ $300/day |
| > $5k/day | 6–10 CBO + 1–2 ABO | Bro's "10 campaigns" only makes sense here |

**Math floor (Andromeda needs):** 50 conversion events per ad set per week. For `AdImpression` value event:
- If avg CPI = $4 and 1 install = 30 ad_impressions in first 7 days → 50 conv events ≈ 2 installs ≈ $8/day per ad set bare minimum
- Realistic for ad_impression VO IAA: **$50–200/day per ad set** to feed the model
- Practitioner data: "One $200 daily campaign outperforms four $50 daily campaigns by 15–25%" (get-ryze.ai)

**Ad sets per campaign**: **2–3**, not 5–10. Each ad set targets a distinct geo cluster (see §3).

**Andromeda penalty**: campaigns with < 50 weekly events get higher CPMs as risk hedge. Consolidate budget, don't split.

---

## §2 — Optimization event (the most important setting)

### Android

Use **`AdImpression` with value** (Value Optimization for IAA = "VO IAA").

**SDK event format** (matters: Meta validates the value range now post-March 2026):

| MMP | Source callback | Event Meta receives |
|---|---|---|
| AppsFlyer | `af_revenue` from impression-level callback (AdMob / Applovin Max / ironSource) | `AdImpression`, `_valueToSum` = per-impression revenue in account currency |
| Adjust | Ad revenue callback | `AdImpression`, `_valueToSum` = revenue |
| Singular | Impression-level ad revenue | `AdImpression`, value-based bidding enabled |

**Per-impression value formula**: `eCPM ÷ 1000`. Example: T1 interstitial eCPM = $8 → value per impression = **$0.008**, sent as-is, no rounding up. Meta's March 2026 algorithm shift detects inflated payloads and downranks.

**Eligibility gate (quoted from Segwise's VO IAA guide):** *"Accumulate ≥15 attributed AdImpression events with two distinct revenue values within 28 days from active campaigns."* Fresh app? Run plain Install or `AdImpression`-no-value for 2 weeks to seed.

### iOS

VO IAA is **still blocked** by ATT/AEM. Use:
- **Install** (or `AdImpression`-no-value) for ramp
- SKAN 4 conversion-value schema (see §4) to encode IAA signal in Window 2 (D3–7)

### Multi-event predictive LTV (NEW Nov 2025)

Stack `AdImpression` + retention event (`fb_mobile_activate_app` D2/D7) in **one ad set**. Meta's pLTV model predicts downstream value. Reports +29% ROAS vs single-event VO.

Setup: Events Manager → app → Custom Conversion → add `fb_mobile_activate_app` with deferred postback window → assign to same ad set as AdImpression.

---

## §3 — Country tiers — 2026 eCPM-driven for IAA

**IAA tiering ≠ general advertising tiering.** Japan, Korea, Saudi, Taiwan punch above their advertising-tier weight on eCPM. Real 2026 eCPM data:

### Tier 1 IAA (rewarded eCPM > $10, interstitial > $7)

| Country | Meta autocomplete name | iOS rewarded eCPM | Android rewarded eCPM |
|---|---|---|---|
| US | `United States` | $19.63–$30.25 | $16.49 |
| Japan | `Japan` | ~$18 | $17.35 |
| Australia | `Australia` | ~$18 | $18.87 |
| Canada | `Canada` | ~$17 | ~$15 |
| UK | `United Kingdom` | ~$16 | ~$13 |
| South Korea | `South Korea` | ~$22 | ~$14 |
| Saudi Arabia | `Saudi Arabia` | $17.54 | ~$10 |
| Taiwan | `Taiwan` | $15.62 | ~$11 |
| Germany | `Germany` | ~$10 | ~$8 |

**Saudi moved up since 2024** (Vision 2030 ad spend). Group with Tier 1 for IAA — not Tier 2.

### Tier 2 IAA ($5–10 rewarded)

UAE, Singapore, Hong Kong, France, Norway, Switzerland, Netherlands, Sweden, Israel, Qatar, New Zealand, Italy, Spain.

### Tier 3 (volume floor)

Brazil, Mexico, Argentina, Colombia, Chile, India, Indonesia, Vietnam, Philippines, Thailand, Turkey, Egypt, Poland. eCPM < $1 iOS rewarded. **Don't mix with T1 in same ad set** — Meta blends to lowest CPI and ignores T1 ARPU.

### Paste-ready country blocks (Meta bulk-add autocomplete exact match)

**Tier 1 IAA (EN creative):**
```
United States, United Kingdom, Canada, Australia, New Zealand, Ireland
```

**Tier 1 IAA (Asia/MENA high-eCPM, mixed-lang creative ok):**
```
Japan, South Korea, Taiwan, Hong Kong, Singapore, Saudi Arabia, United Arab Emirates
```

**Tier 1 IAA (DACH — DE creative):**
```
Germany, Austria, Switzerland
```

**Tier 2 IAA (Western/Northern Europe — EN-tolerant):**
```
Netherlands, Sweden, Norway, Denmark, Finland, Belgium, Israel
```

**Tier 3 volume (Spanish/Portuguese creative if available):**
```
Brazil, Mexico, Argentina, Colombia, Chile
```

### Tier shifts since 2024 to remember

- **Saudi → T1 IAA** (Vision 2030)
- **LATAM down**: Brazil eCPM −6% Jan→Jul 2025, Mexico −20%
- **India**: Meta CPM $2.60 cheap, but IAA eCPM ~$0.30 — volume only, never blend with T1
- **Russia**: legacy revenue, ignore for new campaigns
- **KR/JP stable T1-equivalent for IAA** (KR rewarded iOS $22.01 competitive with US)

---

## §4 — AEM + SKAN 4 (iOS only)

### AEM 2026

**Event priority slots REMOVED** (June 2025 update). No more "slot 1–9" assignment. Auto-aggregation handles unlimited events.

Default behavior: when an iOS app is AEM-eligible, AEM is **auto-selected at ad-set creation** and runs **alongside SKAN 4** in parallel.

### SKAN 4 conversion values for IAA

| Window | Postback timing | Bit budget | What to encode for IAA |
|---|---|---|---|
| W1 | 0–48 h | 6-bit fine value (0–63) | session count + D0 ad revenue bucket |
| W2 | 3–7 d | 2-bit coarse (low/med/high) | D7 ad-revenue tier ← **PRIMARY IAA SIGNAL** |
| W3 | 8–35 d | 2-bit coarse | D35 ad-revenue tier |

**Where IAA signal lives**: W2. Most ad revenue accrues by D7 (compound rewarded views). Build AppsFlyer/Singular SKAN schema so W2 coarse maps to:
- `low` = D7 cumulative ad revenue < $0.05/user
- `mid` = $0.05–$0.20
- `high` = > $0.20

(Tune thresholds to your blended ARPU distribution.)

### Conversions API for Apps (NEW April 2026)

Released April 2026. Even if MMP sends events server-side, enable this in Events Manager → Connections → Apps:
- One-click toggle, no code
- Boosts Meta's Event Match Quality score
- Required to fully exit "limited learning" state in some accounts

---

## §5 — Bid strategy progression

| Stage | Strategy | When |
|---|---|---|
| **Days 1–14** (new campaign) | **Highest Value (no cap)** | Default. Let Meta find high-eCPM users. |
| **Day 14+ (≥ 50 conv/week)** | Continue Highest Value OR switch to **Cost Cap** | Cost Cap = 10–20% above measured CPA. Run parallel A/B for 5 days. |
| **Day 30+ stable** | **Minimum ROAS** | Floor at **80% of true target ROAS**. Drop to 70% if delivery throttles. Below 80%, Meta strangles delivery. |
| Never on new IAA | Bid Cap | 2026 Meta auction "penalizes manual bid adjustments" |

**Min-ROAS enable day**: **D7, not D1**. Earlier = signal too noisy, Meta over-restricts.

---

## §6 — Budget allocation formulas

### Daily budget per ad set (Value Opt)

```
Min ad set daily budget = (Target CPA × 50 conversions) / 7 days
```

Example: Target CPA $4 → min = $4 × 50 / 7 = **$29/day** (round up to $30).

### Practical ranges (2026, post-Andromeda)

| Spend level | Per ad set / day | Why |
|---|---|---|
| Bare minimum | $30 | Hits 50 conv/week at $4 CPA |
| Practical floor for VO | **$50–100** | Below this, Andromeda throttles |
| "Effective" optimization | **$200–400 ad set / $1.5–3k month** | Meta's stated optimal |
| Test ad set (ABO) | $30–50 | Short-lived, kill after 4–5 days |

### Scaling cadence (post-Andromeda)

| Current daily budget | Scale rule |
|---|---|
| < $150 | **+20% every 3–4 days** |
| $150–300 | +15% every 4–5 days |
| $300–500 | +10% every 5–7 days |
| > $500 | **Horizontal scaling**: duplicate campaign at new budget, run parallel, merge after dupe exits learning |

**CBO escape hatch**: CBO accepts uncapped budget increases without triggering learning reset — algorithm redistributes. This is the 2026 standard for fast scaling.

**Kill rules:**
- CPA > 25% above target for 3 consecutive days → cut budget 50%, swap to fresh creative
- Frequency > 3.0 → fatigue (10–25% CPA increase incoming), refresh creative
- ROAS < min-ROAS floor for 7 days → pause

---

## §7 — Creative specs + hook patterns

### Video specs (2026)

| Spec | Reels (priority) | Feed | Stories |
|---|---|---|---|
| Aspect ratio | **9:16 / 1080×1920 required** | 1:1 or 4:5 | 9:16 |
| Length | **15–30 s sweet spot** (max 90s) | <60 s | <60 s |
| Codec | H.264 / MP4 | H.264 | H.264 |
| Frame rate | **30 fps** (no lift above) | 30 | 30 |
| Bitrate | **15–20 Mbps export** (Meta re-encodes) | 15–20 | 15–20 |
| Audio | AAC, **256 kbps recommended** | AAC 128+ | AAC 128+ |
| Safe zone | Avoid bottom 15% (Reels CTA) | — | top/bottom 14% |
| Captions | **Burned-in required** (92% mobile is sound-off) | required | required |

**Andromeda penalizes watermarks** (TikTok logo, CapCut watermark). Strip before upload.

### Hook patterns that win 2026 (transformation/AI app)

| Pattern | Status | Notes |
|---|---|---|
| **Selfie POV "watch this"** | ★★★ Strongest | Creator talks → reveal. Use as dominant pattern. |
| **Before/after at frame 1** | ★★★ Strong | Plain selfie → result, no fade |
| **Circle-inset / PiP** | ★★★ Trending | Small selfie overlay with arrow on reveal (FANCAM-style) |
| **Curiosity gap** ("Wait until 0:15…") | ★★ Solid | Needs payoff |
| **Pattern interrupt** (color flash, zoom) | ★★ Solid | Cheap A/B |
| **Bold-stat / number flash** | ★ Mid | OK for utility apps |
| ~~Fake text-message / fake-news~~ | ✗ **FATIGUED** | Avoid 2026 — Meta flags + users tune out |

**UGC vs polished**: UGC outperforms +31% hook rate, +33% CTR (12k ads, Liftoff). **Mix**: 60% UGC / 40% polished. UGC fatigues 2× faster (7.6d vs 15.4d) → high-velocity refresh.

**Music**: trending audio > licensed library > silent. Reels CPM pool rewards trending. Meta's **AI-generated music** option for licensing-blocked cases.

### Copy specs

| Field | Hard limit | Recommended |
|---|---|---|
| Primary text | 2200 | **~125 chars** (mobile "See more" cutoff). Front-load value prop in first 80. |
| Headline (Feed) | 255 | **27 chars** |
| Headline (Reels) | 255 | **40 chars** |
| Reels burned-in overlay | — | **10 chars** (1 verb + noun) |
| Description | 30 | skip — not rendered on Reels |

**Emojis**: 1–2 lifts CTR; 3+ correlates with lower install quality (worse ad_impression/DAU).

**"Free"**: still works but **saturating** — replace with "Try it" / "See yourself as ___" for AI apps. Same intent, lower policy friction.

**CTA button ranking for IAA (lift on ad_impression value 2026):**
1. **Install Now** — direct intent
2. **Try Free** — creative/AI apps (best fit for face-swap, photo, video)
3. Download — flat
4. Learn More — drags D1 retention, avoid for IAA
5. Play Game — only if app is gaming

---

## §8 — Advantage+ Creative (Feb 2026 default-ON)

Every new App Promotion campaign launches with **all Advantage+ Creative enhancements ON by default** (Andromeda ranking). Advertisers using them report **+22% ROAS**.

Enhancements include:
- **AI Dubbing** — multilingual voice from one source upload (one master → DE/FR/ES/JP)
- **AI-generated music** when licensing blocks trending audio
- **Persona-based image variants** — multiple versions per audience segment
- **Image-to-video animation** — turn statics into 3–6s clips
- **Text variant generation** — Meta auto-creates 5–10 copy variants
- **Lifestyle backgrounds** (for static products)

**Audit before disabling**: only turn off for brand-safety reasons. Default-leave-on for AI photo / fancam / utility apps.

### Asset count per ad set (2026 — outdated "6 ads" guidance is dead)

| Monthly spend | Assets per ad set | Refresh cadence |
|---|---|---|
| < $1k | 5–10 | 4–6 weeks |
| $1k–10k | **20–50** | **2–4 weeks** |
| $10k+ | **50–150** | **1–2 weeks** |
| Advantage+ Shopping/App | up to **150** | continuous trickle |

**78% of top-quartile campaigns refresh weekly in 2026** (vs 41% in 2024). Andromeda needs creative variance to find winners.

**Fatigue triggers** (any 2 → refresh):
- CTR drops ≥15% from 7-day baseline
- CPM rises ≥10%
- Hook rate (3s view) drops ≥20%
- Frequency > 3.5 on prospecting

**Fatigue onset 2026: 9.2 days** (was 14 in 2024) — UGC even faster (7.6d).

---

## §9 — Benchmarks for IAA (2026)

| Metric | Benchmark |
|---|---|
| Meta App Promotion CPA | $35–55 avg, < $30 top, > $60 bad |
| CPI iOS NA | $4.50 (mid-core) |
| CPI Android NA | $2.97 |
| CPI APAC | $0.93 |
| CPI LATAM | $0.34 |
| IPM global median | 4.27 |
| IPM video | 2.9 |
| IPM playable | 4.8 (+23% YoY) |
| IPM static | 1.4 |
| Reels CTR avg | 0.76% IG / 0.94% FB |
| In-app CTR baseline | 0.56% (vs 0.23% mobile web) |
| D7 retention (good) | ≥ 20% |
| ARPDAU rewarded lift | +30–66% |
| CAC payback healthy | < 12 months |
| LTV:CAC target | ≥ 3:1 |

---

## §10 — Attribution windows (Jan 2026 reset)

| Window | Status |
|---|---|
| 1-day click | ✓ |
| 7-day click | ✓ (default for installs) |
| 1-day view | ✓ |
| 7-day view | ✗ **RETIRED Jan 12, 2026** |
| 28-day view | ✗ **RETIRED Jan 12, 2026** |

**Default for new IAA campaign**: 7-day-click + 1-day-view. Don't expect 28d-view; rebuild ad sets if copying templates from 2024 (those silently broke Jan 15, 2026).

---

## §11 — March 2026 Andromeda algorithm reality

What changed:
- **Auction-time → outcome-prediction-time bidding**
- 100× faster matching, 10,000× more variants in parallel
- **CPMs up 15–40% first 2 weeks of rollout**, structurally +20% YoY ($13.48 avg)
- **Punishes creative similarity** — text-overlay swaps no longer count as new ads. Need genuine visual variance.
- Penalizes campaigns < 50 weekly events

Adaptation: **Consolidate budget, diversify creative**. Opposite of pre-2024 "split test everything" playbook.

---

## §12 — Detailed-targeting cleanup (Jan 15, 2026)

Deprecated detailed-targeting interests stopped delivering. Saved audiences from 2024 silently broke. **Rebuild ad sets** if you copied old templates.

**2026 default**: Advantage+ Audience (broad). Interest targeting is back as *optional hint* but underperforms on App Promotion — leave it off.

---

## §13 — Setup checklist (10 items before launch)

1. ✅ `ad_impression` event firing in app — test via Events Manager → Test Events
2. ✅ MMP sends impression-level revenue (AppsFlyer `af_revenue` / Adjust callback / Singular)
3. ✅ ≥ 15 AdImpression events with 2 distinct revenue values logged in last 28d (eligibility for VO IAA)
4. ✅ Meta SDK ≥ v18 OR Conversions API for Apps enabled (April 2026)
5. ✅ Play Store / App Store URL active + deep link configured
6. ✅ Per-impression value range cap: low $0.001, high $0.10 (catches injection)
7. ✅ Country block matches Meta autocomplete (`United States` not "USA")
8. ✅ Budget ≥ $30/day per ad set (50-conv-week formula)
9. ✅ Bid strategy = Highest Value (no cap) for first 14 days
10. ✅ Advantage+ Creative enhancements ON (audit, don't disable)

---

## §14 — When to use this skill

- User mentions Meta Ads / Facebook Ads / Instagram Ads + an IAA-monetized app
- User asks about `AdImpression` / `ad_impression` event optimization
- User asks about VO / Value Optimization for ad revenue
- User asks for country tier list specific to IAA monetization
- User asks for budget/scaling strategy for app install campaigns in 2026
- User asks about Andromeda algorithm / Advantage+ App Promotion
- User dumps Play Store URL + folder of video creatives for an IAA app

## §15 — When NOT to use

- IAP-monetized apps (in-app purchases) — different optimization event, different tiers (use general meta-ads-tiers skill)
- Web checkout / e-commerce — entirely different objective (Conversion, not App Promotion)
- B2B SaaS — Lead objective, different KPIs
- 2023–2024-era questions — that advice is mostly outdated post-Andromeda

---

## §16 — Workflow when given app URL + video folder (Mode B)

1. **Read app context** from Play Store URL: package, category, language. If user confirms IAA + retention lifecycle (D0–D7), prioritize **install volume + fast-first-session** hooks.
2. **Audit creatives**: sample 6–8 frames from folder, identify dominant hook pattern (selfie POV, before/after, circle-inset, etc.).
3. **Generate copy**: 5 primary texts + 5 headlines per §7. Single themed emoji. CTA = "Try Free" for cold / "Install Now" for retargeting.
4. **Determine structure** from budget per §1.
5. **Output blocks**:
   - Country block (paste-ready, §3)
   - Campaign × ad set table (§1 structure)
   - Primary texts table (§7)
   - Headlines table (§7)
   - Pre-launch checklist (§13)
6. **Recommend Dynamic Creative ON** (Advantage+ Creative default since Feb 2026).

---

## Sources (verified 2026)

Meta + Marketing API:
- [Meta unified API for Advantage+ — ppc.land](https://ppc.land/meta-launches-unified-api-structure-for-advantage-campaigns/)
- [Meta deprecates legacy AAC APIs — ppc.land](https://ppc.land/meta-deprecates-legacy-campaign-apis-for-advantage-structure/)
- [Meta CAPI Complete Guide 2026 — AdMove](https://www.admove.ai/blog/meta-capi-guide)
- [Meta Advantage+ Creative Best Practices 2026 — AdMove](https://www.admove.ai/blog/meta-advantage-creative-best-practices-for-2026)

MMP integration:
- [Meta in-app event mapping — AppsFlyer](https://support.appsflyer.com/hc/en-us/articles/4410480904081-Meta-ads-in-app-event-mapping)
- [Meta ad revenue optimization — AppsFlyer Bulletin](https://support.appsflyer.com/hc/en-us/articles/29376794104209)
- [Meta AEM for iOS — AppsFlyer](https://support.appsflyer.com/hc/en-us/articles/19228737402129)
- [Set up Meta in Adjust](https://help.adjust.com/en/article/facebook)
- [Adjust + Meta AEM integration](https://www.adjust.com/blog/adjust-meta-aem-integration/)
- [Singular + Meta AEM](https://www.singular.net/blog/meta-aggregated-event-measurement/)

VO IAA + bidding:
- [Meta's Ad ROAS guide for mobile gaming UA — Segwise](https://segwise.ai/blog/guide-metas-ad-roas-mobile-gaming)
- [Meta Ads Bidding Strategies 2026 — Spintadigital](https://spintadigital.com/blog/meta-ads-bidding-strategies-2026/)
- [Stackmatix Bidding Strategy 2026](https://www.stackmatix.com/blog/meta-ads-bidding-strategy)
- [DTC Newsletter — Min ROAS Mar 2026](https://www.directtoconsumer.co/newsletter/when-to-use-meta-min-roas-bid-strategy)

Budget + scaling:
- [Stackmatix Min Daily Budget 2026](https://www.stackmatix.com/blog/meta-ads-minimum-daily-budget-2026)
- [get-ryze.ai Budget Guide 2026](https://www.get-ryze.ai/blog/meta-ads-budget-planning-how-much-spend-2026)
- [Modern Marketing Institute Exit Learning 2026](https://www.modernmarketinginstitute.com/blog/how-to-exit-the-meta-ads-learning-phase-fast-and-start-scaling-profitably-in-2026)
- [Benly Scaling Meta Ads 2026](https://benly.ai/learn/meta-ads/scaling-meta-ads-guide)
- [Skaleit ABO vs CBO 2026](https://skaleit.agency/blog/abo-vs-cbo-test-scale-meta-ads/)

eCPM + tiers:
- [AppsFlyer Performance Index 2025](https://www.appsflyer.com/company/newsroom/pr/performance-index-2025/)
- [Tenjin Ad Monetization Benchmark 2025](https://tenjin.com/blog/ad-mon-gaming-2025/)
- [Tenjin eCPM by Country/Format](https://tenjin.com/blog/ad-monetization-benchmark-report-2025-ecpm-ad-revenue/)
- [MAF Mobile Ads eCPM](https://maf.ad/en/blog/mobile-ads-ecpm/)
- [AdAmigo CPM/CPC by Country 2026](https://www.adamigo.ai/blog/meta-ads-cpm-cpc-benchmarks-by-country-2026)
- [AdAmigo Benchmarks by Objective/Placement 2026](https://www.adamigo.ai/blog/meta-ads-benchmarks-2026-by-objective-and-placement)

Andromeda + algorithm:
- [DigitalApplied March 2026 Performance Drop](https://www.digitalapplied.com/blog/meta-ads-performance-dropped-march-2026-ai-algorithm-changes)
- [JetFuel Andromeda Adaptation 2026](https://jetfuel.agency/metas-2026-algorithm-update-what-andromeda-changed-and-how-to-adapt-your-ads/)
- [Marketing Agent Advantage+ 2026 Playbook](https://marketingagent.blog/2026/05/06/the-complete-roadmap-to-using-meta-advantage-in-2026/)

Creative + UGC:
- [Adligator hook patterns 2026](https://adligator.com/blog/facebook-ad-hook-patterns-2026)
- [RocketShipHQ Liftoff Creative 2026](https://www.rocketshiphq.com/liftoff-mobile-ad-creative-report-2025-summary/)
- [Benly Creative Benchmarks 2026](https://benly.ai/learn/ad-creative/ad-creative-benchmarks-2026)
- [Insense UGC Playbook](https://insense.pro/blog/user-generated-content)
- [AdStellar Refresh Frequency 2026](https://www.adstellar.ai/blog/meta-ad-creative-refresh-frequency)
- [Pixel Panda Fatigue 2026](https://www.pixelpandacreative.com/blog/why-your-best-performing-ad-is-your-biggest-risk-in-2026)
- [Vizup 2026 Ad Specs](https://www.tryvizup.com/blog/meta-ad-specs-2026-every-dimension-size-you-need)

SKAN 4 + privacy:
- [SKAN 4 Conversion Value Setup — Airbridge](https://help.airbridge.io/en/guides/skadnetwork-4-settings)

Targeting + attribution:
- [Adligator Broad Targeting 2026](https://adligator.com/blog/meta-broad-targeting-advantage-plus-audiences-2026)
- [Cybersolution Deprecated Targeting Jan 2026](https://www.thecybersolution.pk/blog-detail/meta-is-retiring-deprecated-targeting-options-here-s-what-you-need-to-do)
- [Madgicx Learning Phase Lowered](https://madgicx.com/blog/meta-lowers-learning-phase-requirement-for-select-campaigns)
