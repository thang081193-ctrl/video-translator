"""Build v3 campaign structure — by VISUAL category (not narrative angle).

6 campaigns total:
  - C01_EN_FloorPlanTo3D: 6 sketch/2D-to-3D videos
  - C02_EN_AwkwardNook:   6 awkward-corner makeover videos
  - C03_EN_LivingRoom:    6 LR redesign videos
  - C04_EN_UnderStair:    6 under-stair storage videos
  - C05_EN_GardenExterior:6 garden/patio/exterior videos
  - C06_FR_All:           9 FR videos (mixed angle, unchanged)
"""
from __future__ import annotations

import os, sys, io
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

ROOT = Path(r"D:/Dev/App Details/Home Decor/Video/1205")
BRANDED = ROOT / "_branded"
CAMP = BRANDED / "_campaigns"

GEO_EN = "United States, Canada, United Kingdom, Australia, New Zealand, Ireland, Netherlands, Sweden, Norway, Denmark, Finland, Singapore, Hong Kong, Israel, United Arab Emirates, Saudi Arabia, South Korea, Taiwan"
GEO_FR = "France, Belgium, Switzerland, Canada, Luxembourg, Monaco"

# Top 6 per visual bucket (from visual_taxonomy.csv)
FLOORPLAN = ["EN_1205001.mp4", "EN_1205002.mp4", "EN_1205003.mp4",
             "EN_1205004.mp4", "EN_1205005.mp4", "EN_1205010.mp4"]
NOOK      = ["EN_1205007.mp4", "EN_1205026.mp4", "EN_1205027.mp4",
             "EN_1205031.mp4", "EN_1205044.mp4", "EN_1205047.mp4"]
LIVINGROOM = ["EN_1205009.mp4", "EN_1205023.mp4", "EN_1205028.mp4",
              "EN_1205038.mp4", "EN_1205040.mp4", "EN_1205041.mp4"]
UNDERSTAIR = ["EN_1205032.mp4", "EN_1205034.mp4", "EN_1205036.mp4",
              "EN_1205042.mp4", "EN_1205062.mp4", "EN_1205063.mp4"]
GARDEN    = ["EN_1205029.mp4", "EN_1205033.mp4", "EN_1205035.mp4",
             "EN_1205064.mp4", "EN_1205065.mp4", "EN_1205073.mp4"]
FR_ALL    = ["FR_120501.mp4", "FR_120502.mp4", "FR_120503.mp4", "FR_120504.mp4",
             "FR_120505.mp4", "FR_120506.mp4", "FR_120507.mp4", "FR_120508.mp4",
             "FR_120509.mp4"]

# Angle-specific ad copy (English)
PRIMARY_FLOORPLAN = """Upload your floor plan. AI builds the room in 5 seconds. 🏗️ Free trial →
2D sketch on paper, 3D photoreal room on phone. That fast.
Builders + interior designers use this app. Now you can too.
Stop guessing how your reno will look. AI shows you in seconds.
Floor plan → photorealistic 3D walkthrough. No software needed.
Test 5 layouts before you call the contractor. Save thousands.
The sketch in your hand → the dream room on your screen.
Watch a blueprint become a furnished room. AI did this. Try free.
2D to 3D in one tap. Architecture-grade visualization for everyone.
DecoAI turns your floor plan into a tour. Try free today.
"""

HEAD_FLOORPLAN = """Floor Plan → 3D Room
2D Sketch to Photoreal
Pre-Viz Your Reno
See It Before You Build
Architect-Grade AI
Floor Plan Magic
Plan Before You Build
Reno Visualizer Free
DecoAI Sketch Mode
Try DecoAI Free
"""

PRIMARY_NOOK = """That awkward corner in your hallway? AI knows what to do. 🪄 Free trial →
Dead zone next to the stairs? Snap → AI makes it a reading nook.
Stop ignoring that empty wall. AI gives you 5 fixes in seconds.
Tiny niche, big potential. AI turns it into your favorite spot.
The chair in the corner that "just sits there" — fixed in 5s.
Awkward corners aren't useless. They're under-designed. DecoAI fixes that.
That weird L-shaped wall? AI turned it into a mini reading library.
Don't waste space. Don't pay a designer. Snap → solved.
Reading nook. Plant corner. Mini-office. AI picks the right fit.
DecoAI sees what your space could be. Try free today.
"""

HEAD_NOOK = """Fix That Awkward Corner
Dead-Zone to Dream Spot
AI Sees Hidden Potential
Snap → Fix Any Nook
Reading Nook in Seconds
No More Wasted Space
Designer for Tiny Corners
The Nook Hack
Try DecoAI Free
Stop Ignoring That Wall
"""

PRIMARY_LR = """Your living room — but designer-redesigned. AI did it in 5 seconds. 🛋️ Try free →
TV wall, sofa, coffee table — AI shows you 5 better layouts.
Modern. Cozy. Scandinavian. Pick a style — AI redoes your LR.
Same room. 5 different looks. Pick your favorite in 30 seconds.
The Pinterest LR vs YOUR LR — AI bridges the gap.
Bored of your living room? AI gives it a glow-up in 5s.
TV mount, accent wall, layered rugs — AI knows what works.
Snap your LR. Get 5 magazine-style redesigns. Free.
Stop scrolling Pinterest. Get YOUR room redesigned. DecoAI.
Designer LR for a fraction of the cost. Actually $0. ✨
"""

HEAD_LR = """Redesign Your LR
Living Room Glow-Up
AI Designs Your LR
5 LR Styles in 5s
LR Like Pinterest
From Tired to Designer
Snap. Pick. Done.
Try DecoAI Free
Magazine LR Instantly
DecoAI LR Mode
"""

PRIMARY_UNDERSTAIR = """That clutter under your stairs? AI made it a home office. 🤯 Try free →
Under-stair junk → AI turns it into a wardrobe. In 5 seconds.
The space everyone wastes — finally has a purpose. AI did this.
Cluttered, dark, useless → bright, organized, beautiful. AI fix.
Stairs hiding a treasure? AI shows you the closet you didn't know you had.
From "throw stuff there" zone to "Wow, you have a laundry room?".
Mini-office under stairs. Mini-bar under stairs. Pet bed under stairs. AI picks.
That ugly door under your stairs? AI redesigns the whole thing.
DecoAI gave us 200 sqft of storage we didn't know we had. Free.
Under-stair makeover in 5 seconds. Save $5k on renovations.
"""

HEAD_UNDERSTAIR = """Under-Stair Makeover
Hidden Storage Hack
Stairs → Home Office
Reclaim Wasted Space
DIY Under-Stair Fix
$5k Storage Hack
AI Found a Closet
Try DecoAI Free
That Junk Zone? Fixed.
Under-Stair Magic
"""

PRIMARY_GARDEN = """Your backyard could be magazine-worthy. AI shows you how. 🌿 Free trial →
Patio. Pool deck. Pergola. AI redesigns your outdoor space in 5s.
Stop staring at that ugly yard. AI gives you 5 garden makeovers.
Boring lawn → outdoor sanctuary. AI did this in seconds.
Garden lights, planters, fire pit — AI knows the right combo.
Hot tub area, BBQ zone, lounge — AI sketches them all for you.
That weird side strip next to your house? AI made it a zen garden.
Don't hire a landscaper. Plan it on DecoAI first. Free.
From "we should fix the yard someday" to "wow, this is the plan."
DecoAI does exteriors too. Garden, patio, facade. Try free.
"""

HEAD_GARDEN = """Backyard Glow-Up
Garden Redesign in 5s
Patio. Pool. Pergola.
AI for Your Garden
Outdoor Sanctuary Free
From Boring Lawn to Wow
Landscape With AI
DecoAI Garden Mode
Skip the Landscaper
Try DecoAI Free
"""

PRIMARY_FR = """Une photo. L'IA redesigne ta pièce en 5 secondes. 🪄 Essai gratuit →
Mon mari a mis 15 ans. L'IA, 3 secondes. 😳 Sans décorateur, sans budget.
J'ai testé 45 minutes — j'ai refait toute la maison. C'est addictif. ✨
Arrête de scroller Pinterest. L'IA crée pour TON espace, pas pour un autre.
Avant rénovation, simule. Économise de l'argent et des nerfs.
Photo, style, "créer". 5 secondes. 5 designs prêts.
Le canapé que tu adores ne rentre peut-être pas. Teste-le avec l'IA.
Le secret des intérieurs Instagram. Spoiler : c'est l'IA.
Petit budget, petit espace — résultat magazine. C'est gratuit.
De la chambre au patio — chaque pièce, chaque style. Secondes.
"""

HEAD_FR = """Redesigne en 5s
DecoAI Gratuit
Sans Décorateur
L'IA Décore Pour Toi
Photo → Design
Essai Gratuit
Look Magazine
Plan Reno Avant
Style en Secondes
Le Secret IA
"""

CAMPAIGNS = [
    {"name": "C01_EN_FloorPlanTo3D", "visual": "Sketch / 2D floor plan → 3D walkthrough",
     "lang": "English", "lang_folder": "English", "videos": FLOORPLAN,
     "primary": PRIMARY_FLOORPLAN, "headlines": HEAD_FLOORPLAN, "geo": GEO_EN, "budget": 250},
    {"name": "C02_EN_AwkwardNook", "visual": "Awkward corner / dead-zone makeover",
     "lang": "English", "lang_folder": "English", "videos": NOOK,
     "primary": PRIMARY_NOOK, "headlines": HEAD_NOOK, "geo": GEO_EN, "budget": 250},
    {"name": "C03_EN_LivingRoom", "visual": "Living room redesign",
     "lang": "English", "lang_folder": "English", "videos": LIVINGROOM,
     "primary": PRIMARY_LR, "headlines": HEAD_LR, "geo": GEO_EN, "budget": 250},
    {"name": "C04_EN_UnderStair", "visual": "Under-stair storage transformation",
     "lang": "English", "lang_folder": "English", "videos": UNDERSTAIR,
     "primary": PRIMARY_UNDERSTAIR, "headlines": HEAD_UNDERSTAIR, "geo": GEO_EN, "budget": 250},
    {"name": "C05_EN_GardenExterior", "visual": "Garden / patio / exterior",
     "lang": "English", "lang_folder": "English", "videos": GARDEN,
     "primary": PRIMARY_GARDEN, "headlines": HEAD_GARDEN, "geo": GEO_EN, "budget": 250},
    {"name": "C06_FR_All", "visual": "Mixed angles (FR pool quá nhỏ để split)",
     "lang": "Français", "lang_folder": "Français", "videos": FR_ALL,
     "primary": PRIMARY_FR, "headlines": HEAD_FR, "geo": GEO_FR, "budget": 250},
]


def main():
    for c in CAMPAIGNS:
        cdir = CAMP / c["name"]
        cdir.mkdir(parents=True, exist_ok=True)

        (cdir / "videos.txt").write_text("\n".join(c["videos"]) + "\n", encoding="utf-8")
        (cdir / "primary_text.txt").write_text(c["primary"], encoding="utf-8")
        (cdir / "headlines.txt").write_text(c["headlines"], encoding="utf-8")
        (cdir / "geo.txt").write_text(c["geo"] + "\n", encoding="utf-8")

        # Hardlink videos into campaign folder
        linked = 0
        skipped = 0
        for fname in c["videos"]:
            src = BRANDED / c["lang_folder"] / fname
            dst = cdir / fname
            if dst.exists():
                skipped += 1
                continue
            if not src.exists():
                print(f"  WARN: source missing {src}")
                continue
            try:
                os.link(src, dst)
                linked += 1
            except OSError as e:
                print(f"  WARN linking {fname}: {e}")

        # _ASSETS.md
        md = []
        md.append(f"# {c['name']} — DecoAI Meta Ads")
        md.append("")
        md.append(f"**Visual category:** {c['visual']}")
        md.append(f"**Language:** {c['lang']}")
        md.append(f"**Daily budget:** ${c['budget']}/day")
        md.append(f"**Total videos:** {len(c['videos'])}")
        md.append(f"**Geo target:** {c['geo']}")
        md.append("")
        md.append("## Videos (drag-drop these mp4 from this folder)")
        md.append("")
        for f in c["videos"]:
            md.append(f"- `{f}`")
        md.append("")
        md.append("## Primary text variants (paste each line into Primary text field)")
        md.append("")
        for i, p in enumerate(c["primary"].strip().splitlines(), 1):
            md.append(f"{i}. {p}")
        md.append("")
        md.append("## Headlines (paste each line into Headlines field)")
        md.append("")
        for i, h in enumerate(c["headlines"].strip().splitlines(), 1):
            md.append(f"{i}. {h}")
        md.append("")
        md.append("## Meta Ads settings")
        md.append("")
        md.append("- App: AI Home Design: DecoAI")
        md.append("- Optimization: `AdImpression` (Value Optimization)")
        md.append("- Bid: Highest Volume")
        md.append("- Audience: Advantage+ Audience")
        md.append("- Placements: Advantage+ Placements")
        md.append("- Dynamic Creative: ON")
        md.append("- CTA: Use App / Install Now")

        (cdir / "_ASSETS.md").write_text("\n".join(md), encoding="utf-8")
        print(f"{c['name']}: {len(c['videos'])} videos  (hardlinked: {linked}, already: {skipped}) [{c['visual']}]")

    total_videos = sum(len(c["videos"]) for c in CAMPAIGNS)
    total_budget = sum(c["budget"] for c in CAMPAIGNS)
    print(f"\nTotal: {total_videos} videos across {len(CAMPAIGNS)} campaigns")
    print(f"Total daily budget: ${total_budget}/day")


if __name__ == "__main__":
    main()
