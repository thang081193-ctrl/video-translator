---
name: gen-outro
description: Generate a branded outro card image (1080x1920 PNG) for mobile app video ads. Outputs a full-canvas outro with app icon, title, subtitle, CTA pill button, and optional rating badge. Three themes available — baby (pastel pink/lavender, chibi aesthetic, scattered hearts+stars), tech (dark purple gradient, minimal), minimal (clean light grey). Use when the user asks to "make an outro", "generate outro card", "create outro image for my app", or wants to preview a branded tail card before running a brand-pass batch.
---

# Gen Outro

Standalone PIL-based outro card generator. Produces a 1080x1920 PNG with a gradient background, app icon, title + subtitle text, "Download Now!" CTA pill button, and a rating/download badge. Designed for mobile app video ads (Meta/TikTok Reels format).

## When to use
- User wants to preview or regenerate the outro card for an app.
- User is about to run brand-pass and wants to check the outro design first.
- User wants a different theme (baby / tech / minimal) for a new app brand.
- User wants a standalone static asset (e.g. thumbnail, endcard image).

## Do NOT use when
- User wants to apply the outro to a batch of videos — that's `brand-pass` skill.
- User wants animated outro (this generates a static PNG only).

## Required information — ask if missing

1. **Logo** — path to the app icon PNG (transparent or white background). Example: `D:\Dev\Apps Detail\Chatify\Logo.png`
2. **Theme** — visual style for the outro card:
   - `baby` — pastel pink + lavender gradient, white icon background with pink border, hot-pink "Download Now!" pill, scattered hearts + stars, lavender rating badge. Best for lifestyle, parenting, social, companion apps.
   - `tech` — dark deep-purple gradient, purple icon background, subtle decorations off. Best for productivity, developer tools, AI utility apps.
   - `minimal` — clean light grey gradient, neutral tones, no decorations. Best for finance, business, clean-brand apps.

## Optional parameters (infer from context or use defaults)

| Flag | Default | Notes |
|---|---|---|
| `--title` | `Chatify` | App name, displayed large |
| `--subtitle` | `Your AI Companion` | Tagline, displayed below title |
| `--rating` | `4.8  /  2M+ Downloads` | Small badge below CTA pill. Pass `""` to hide. |
| `--out` | `outro_preview.png` | Output PNG path |
| `--seed` | random | Integer seed for deterministic decoration placement |

## Usage

```bash
python "<skills-dir>/gen-outro/gen_outro.py" \
  --logo   "<path to logo.png>" \
  --theme  baby \
  --title  "Chatify" \
  --subtitle "Your AI Companion" \
  --rating "4.8  /  2M+ Downloads" \
  --out    "<output path>/outro_preview.png"
```

> Uses the system Python (or any env with Pillow installed). The script has no other dependencies.

## Output layout (baby theme)

```
[pastel pink → lavender vertical gradient]

  ♥  ★  ·  (scattered decorations)

  ┌─────────────────────┐
  │  white rounded rect │   ← app icon, 440×440, corner-radius 98
  │     [logo here]     │      pink drop-shadow, pink border
  └─────────────────────┘

       Chatify              ← 110pt bold, dark purple
   Your AI Companion        ← 66pt regular, medium lavender

  ╔═══════════════════╗
  ║   Download Now!   ║     ← 76pt bold, hot-pink pill (#FF5A8C)
  ╚═══════════════════╝

   4.8  /  2M+ Downloads    ← 42pt, soft lavender
```

## Themes quick-reference

| Theme | BG | Icon BG | Pill | Decorations |
|---|---|---|---|---|
| `baby` | Pink → Lavender gradient | White + pink border | Hot pink #FF5A8C | Hearts + stars scatter |
| `tech` | Near-black → deep purple | Purple #411EA0 | Purple #6937D2 | None |
| `minimal` | Off-white → light grey | White + grey border | Blue #3C3CB4 | None |

## Adding a new theme

Edit `THEMES` dict in `gen_outro.py`. Each theme needs these keys:
`bg_top`, `bg_bot`, `icon_bg`, `icon_border`, `shadow_color`, `text_title`, `text_sub`,
`pill_fill`, `pill_shadow`, `pill_text`, `rating_color`, `deco_hearts`, `deco_stars`, `decorations` (bool).

## Integration with brand-pass

The outro card rendered by brand-pass (`_generate_outro_frame` in `pipeline/brand_pass.py`) uses the same baby/chibi design as the `baby` theme here. To update the brand-pass outro, mirror any color changes from `gen_outro.py → THEMES["baby"]` into `_generate_outro_frame()` in `brand_pass.py`.

## Dependencies

- **Pillow** (`pip install Pillow`) — only dependency
- Windows system fonts: Segoe UI Semibold / Segoe UI / Arial (auto-fallback chain built in)
