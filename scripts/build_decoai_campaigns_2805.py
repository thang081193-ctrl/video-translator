"""Build DecoAI Meta Ads campaigns for the 2805 batch — by ANGLE × COUNTRY.

8 campaigns:
  FR : C1_FR_StorageVO, C2_FR_Walkthrough
  TR : C3_TR_StorageVO, C4_TR_Walkthrough
  ZA : C5_ZA_StorageVO, C6_ZA_Walkthrough, C7_ZA_HelpRedesign, C8_ZA_TipsDoDont

Videos are globbed from the classified/dubbed folders and HARDLINKED into each
campaign folder (shares inode — no disk doubling). Run AFTER the dub batch.
"""
from __future__ import annotations

import io
import os
import sys
from glob import glob
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

ROOT = Path(r"D:/Dev/App Details/Home Decor/Video/home decor 2805")
CAMP = ROOT / "_campaigns"

# ─── Country targeting (paste into "Add locations in bulk") ───────────────────
GEO_FR = "France"                 # scaling winner; expand to francophone later if needed
GEO_TR = "Türkiye"
GEO_ZA = "South Africa"           # EN-tolerant; can broaden to T1+T2 EN block later

# ─────────────────────────────  COPY  ────────────────────────────────────────
# STORAGE_VO — space-saving / hidden-storage transformation stories
P_STORAGE_EN = """One bedroom, two kids? AI redesigned it into a shared dream room. 🛏️ Try free →
That cluttered space → smart storage you didn't know you had. AI did this.
Tiny home, smart layout. AI fits a wardrobe, desk and bed where you saw nothing.
Stop "we'll deal with it later." Snap the room, AI plans the storage in 5s.
From cramped to organized — see the redesign before you spend a cent.
The under-bed, the wall, the corner — AI turns dead space into storage.
Small room problem? AI gives you 5 space-saving layouts instantly.
We found 200 sqft of storage we didn't know we had. Free with DecoAI.
Bunk beds, lift-beds, hidden cabinets — AI picks what fits your room.
Your space could hold twice as much. DecoAI shows you how. Try free."""
H_STORAGE_EN = """Small Room, Smart Storage
Find Hidden Storage
Two Kids, One Room? Fixed
AI Space-Saving Plan
Snap → Storage Solved
Maximize Tiny Spaces
Storage You Didn't Know
Try DecoAI Free
Cramped to Organized
Reclaim Dead Space"""

P_STORAGE_FR = """Une chambre, deux enfants ? L'IA l'a transformée en chambre partagée de rêve. 🛏️ Essai gratuit →
Cet espace encombré → des rangements que tu ne soupçonnais pas. L'IA l'a fait.
Petit logement, agencement malin. L'IA case lit, bureau et armoire là où tu ne voyais rien.
Avant les travaux, simule. L'IA planifie tes rangements en 5 secondes.
De « à l'étroit » à « parfaitement rangé » — vois le résultat avant de dépenser.
Sous le lit, le mur, le coin perdu — l'IA transforme l'espace mort en rangement.
Petite pièce ? L'IA te donne 5 agencements gain de place instantanément.
Ton espace peut contenir deux fois plus. DecoAI te montre comment. Gratuit."""
H_STORAGE_FR = """Petit Espace, Malin
Rangements Cachés
Une Pièce, Deux Lits
Plan Gain de Place
Photo → Rangement
Optimise Ta Pièce
DecoAI Gratuit
Avant/Après IA
Fini le Désordre
Essai Gratuit"""

P_STORAGE_TR = """Tek oda, iki çocuk? Yapay zeka onu hayalindeki ortak odaya dönüştürdü. 🛏️ Ücretsiz dene →
Dağınık alan → fark etmediğin akıllı depolama. Bunu yapay zeka yaptı.
Küçük ev, akıllı yerleşim. YZ; yatağı, masayı ve dolabı hiç yokken sığdırır.
Tadilattan önce simüle et. Yapay zeka depolamanı 5 saniyede planlar.
Dar alandan düzenli odaya — tek kuruş harcamadan sonucu gör.
Yatak altı, duvar, köşe — YZ ölü alanı depolamaya çevirir.
Küçük oda mı? YZ sana anında 5 yerden tasarruflu yerleşim verir.
Alanın iki katı eşya alabilir. DecoAI nasıl olduğunu gösterir. Ücretsiz."""
H_STORAGE_TR = """Küçük Oda, Akıllı Depo
Gizli Depolama Alanı
İki Çocuk, Tek Oda
Yerden Tasarruf Planı
Fotoğraf → Çözüm
Küçük Alanı Büyüt
DecoAI Ücretsiz
Dağınıktan Düzene
Ölü Alanı Geri Kazan
Hemen Ücretsiz Dene"""

# WALKTHROUGH_3D — room redesign / photoreal walkthrough showcase (no on-screen text)
P_WALK_EN = """Your room — but designer-redesigned. AI did it in 5 seconds. 🛋️ Try free →
Modern, cozy, Scandinavian — pick a style, AI redoes your whole room.
Same room, 5 different looks. Pick your favorite in 30 seconds.
Snap your space. Get a photorealistic 3D walkthrough. No software needed.
Kitchen, bath, bedroom — AI gives every room a magazine glow-up.
Stop scrolling Pinterest. Get YOUR room redesigned, not someone else's.
Designer-grade visualization for a fraction of the cost. Actually $0. ✨
Watch your space become the room you've been dreaming of. AI did this."""
H_WALK_EN = """Redesign Any Room
5 Styles in 5 Seconds
Photoreal 3D Tour
Snap. Pick. Done.
Magazine Room Instantly
AI Designs Your Space
From Tired to Designer
Try DecoAI Free
Your Room, Glow-Up
Skip the Designer"""

P_WALK_FR = """Une photo. L'IA redesigne ta pièce en 5 secondes. 🪄 Essai gratuit →
Moderne, cosy, scandinave — choisis un style, l'IA refait toute la pièce.
Même pièce, 5 looks différents. Choisis ton préféré en 30 secondes.
Le secret des intérieurs Instagram. Spoiler : c'est l'IA.
Cuisine, salle de bain, chambre — chaque pièce en version magazine.
Arrête de scroller Pinterest. L'IA crée pour TON espace, pas un autre.
Look magazine pour une fraction du prix. En vrai, 0 €. ✨
Regarde ta pièce devenir la pièce dont tu rêves. C'est l'IA."""
H_WALK_FR = """Redesigne en 5s
L'IA Décore Pour Toi
Visite 3D Photoréaliste
Photo → Design
Look Magazine
5 Styles en Secondes
Sans Décorateur
DecoAI Gratuit
Le Secret IA
Essai Gratuit"""

P_WALK_TR = """Tek fotoğraf. Yapay zeka odanı 5 saniyede yeniden tasarlıyor. 🪄 Ücretsiz dene →
Modern, sıcak, İskandinav — bir stil seç, YZ tüm odayı yeniden yapsın.
Aynı oda, 5 farklı görünüm. 30 saniyede favorini seç.
Instagram'daki iç mekânların sırrı. Spoiler: yapay zeka.
Mutfak, banyo, yatak odası — her odaya dergi kalitesinde yenilik.
Pinterest'i bırak. YZ senin alanın için tasarlar, başkasının değil.
Dergi görünümü, çok küçük bir maliyetle. Aslında 0 ₺. ✨
Odanın hayalini kurduğun hâle gelişini izle. Bunu YZ yaptı."""
H_WALK_TR = """Her Odayı Yenile
5 Saniyede 5 Stil
Fotogerçekçi 3B Tur
Fotoğraf → Tasarım
Dergi Odası Anında
YZ Odanı Tasarlar
Dekoratöre Gerek Yok
DecoAI Ücretsiz
Yorgun Odaya Yenilik
Hemen Ücretsiz Dene"""

# HELP_REDESIGN — "Help!" awkward space → AI redesign reveal (EN only, baked EN text)
P_HELP_EN = """That awkward corner you don't know what to do with? AI knows. 🪄 Try free →
"Help!!" → snap the dead space → AI gives you 5 stunning makeovers.
Dead zone by the stairs? AI turns it into a reading nook in 5 seconds.
Stop ignoring that weird empty spot. AI shows what it could be.
The space that "just sits there" — finally has a purpose. AI did this.
Awkward corners aren't useless. They're under-designed. DecoAI fixes that.
Reading nook? Bar? Mini-office? AI picks the perfect fit for your space.
Don't waste it, don't pay a designer. Snap → 5 ideas → done. Free."""
H_HELP_EN = """Fix That Awkward Space
Help! → AI Solved It
Dead Zone to Dream Spot
AI Sees the Potential
Snap → 5 Makeovers
No More Wasted Corners
Designer for Tiny Spots
Try DecoAI Free
Stop Ignoring That Wall
The Awkward-Corner Hack"""

# TIPS_DODONT — layout do's & don'ts / educational (EN only, baked EN text)
P_TIPS_EN = """Most rooms get the layout wrong. Here's the fix — and AI does it for you. 📐 Try free →
❌ vs ✅ — the layout mistakes you don't even notice. AI catches them.
Correct bathroom layout, perfect bedroom flow — AI plans it right.
Before you move a single piece of furniture, see the right layout.
The rules designers know — now in an app. Snap your room, get it right.
Stop rearranging for hours. AI shows the correct layout in 5 seconds.
Wardrobe placement, TV wall, bed position — get every call right.
Good design isn't luck, it's rules. DecoAI knows them. Try free."""
H_TIPS_EN = """Get the Layout Right
Layout Do's & Don'ts
❌ vs ✅ Room Layouts
Avoid Layout Mistakes
Designer Rules in an App
Correct Room Flow
Snap → Right Layout
Try DecoAI Free
Stop Guessing Layouts
Plan It the Right Way"""

SETTINGS = [
    "- App: AI Home Design: DecoAI (IAA-monetized)",
    "- Campaign: Advantage+ App Promotion (post-AAC)",
    "- Optimization: Value (event `AdImpression`)",
    "- Bid strategy: Highest Volume",
    "- Audience: Advantage+ Audience",
    "- Placements: Advantage+ Placements (Reels-first)",
    "- Dynamic Creative: ON",
    "- CTA: Use App / Install Now",
]

# ─── campaign definitions: (name, src_glob, lang, geo, primary, headlines, note) ──
C = [
    ("C1_FR_StorageVO",   "STORAGE_VO/FR/*.mp4",      "Français", GEO_FR, P_STORAGE_FR, H_STORAGE_FR, "Storage/space-saving stories, dubbed FR"),
    ("C2_FR_Walkthrough", "WALKTHROUGH_3D/none/*.mp4", "Français", GEO_FR, P_WALK_FR,    H_WALK_FR,    "Universal 3D walkthroughs (brand-pass to cover CN watermark)"),
    ("C3_TR_StorageVO",   "STORAGE_VO/TR/*.mp4",      "Türkçe",   GEO_TR, P_STORAGE_TR, H_STORAGE_TR, "Storage/space-saving stories, dubbed TR"),
    ("C4_TR_Walkthrough", "WALKTHROUGH_3D/none/*.mp4", "Türkçe",   GEO_TR, P_WALK_TR,    H_WALK_TR,    "Universal 3D walkthroughs (brand-pass to cover CN watermark)"),
    ("C5_ZA_StorageVO",   "STORAGE_VO/EN/*.mp4",      "English",  GEO_ZA, P_STORAGE_EN, H_STORAGE_EN, "Storage/space-saving stories, original EN VO"),
    ("C6_ZA_Walkthrough", "WALKTHROUGH_3D/none/*.mp4", "English",  GEO_ZA, P_WALK_EN,    H_WALK_EN,    "Universal 3D walkthroughs"),
    ("C7_ZA_HelpRedesign","HELP_REDESIGN/none/*.mp4", "English",  GEO_ZA, P_HELP_EN,    H_HELP_EN,    "Help-hook awkward-space reveals (baked EN text → EN markets only)"),
    ("C8_ZA_TipsDoDont",  "TIPS_DODONT/none/*.mp4",   "English",  GEO_ZA, P_TIPS_EN,    H_TIPS_EN,    "Layout do's/don'ts (baked EN text → EN markets only)"),
]


def main():
    CAMP.mkdir(parents=True, exist_ok=True)
    grand = 0
    for name, pattern, lang, geo, primary, headlines, note in C:
        cdir = CAMP / name
        cdir.mkdir(parents=True, exist_ok=True)
        vids = sorted(os.path.basename(p) for p in glob(str(ROOT / pattern)))

        (cdir / "primary_text.txt").write_text(primary.strip() + "\n", encoding="utf-8")
        (cdir / "headlines.txt").write_text(headlines.strip() + "\n", encoding="utf-8")
        (cdir / "country.txt").write_text(geo + "\n", encoding="utf-8")
        (cdir / "videos.txt").write_text("\n".join(vids) + "\n", encoding="utf-8")

        linked = miss = 0
        for v in vids:
            src = ROOT / Path(pattern).parent / v
            dst = cdir / v
            if dst.exists():
                continue
            try:
                os.link(src, dst); linked += 1
            except OSError as e:
                miss += 1; print(f"  WARN link {v}: {e}")

        md = [f"# {name} — DecoAI Meta Ads", "",
              f"**Angle/note:** {note}", f"**Language:** {lang}",
              f"**Country (paste into bulk locations):** {geo}",
              f"**Videos:** {len(vids)}", "",
              "## Videos (drag-drop from this folder)", ""]
        md += [f"- `{v}`" for v in vids]
        md += ["", "## Primary text (one per line — paste each as a variant)", ""]
        md += [f"{i}. {p}" for i, p in enumerate(primary.strip().splitlines(), 1)]
        md += ["", "## Headlines", ""]
        md += [f"{i}. {h}" for i, h in enumerate(headlines.strip().splitlines(), 1)]
        md += ["", "## Settings", ""] + SETTINGS
        (cdir / "_ASSETS.md").write_text("\n".join(md), encoding="utf-8")

        grand += len(vids)
        print(f"{name:<22} {len(vids):>2} videos (linked {linked}) [{lang}/{geo}]")

    # top-level README
    readme = ["# DecoAI 2805 — Meta Ads campaigns", "",
              f"{len(C)} campaigns, {grand} video slots, split by angle × country.", "",
              "| Campaign | Lang | Country | Videos |", "|---|---|---|---|"]
    for name, pattern, lang, geo, *_ in C:
        n = len(glob(str(ROOT / pattern)))
        readme.append(f"| {name} | {lang} | {geo} | {n} |")
    readme += ["", "## Per-campaign upload flow",
               "1. Open `country.txt` → copy → paste into Meta 'Add locations in bulk'.",
               "2. Drag-drop the mp4 files from the campaign folder.",
               "3. Paste each line of `primary_text.txt` as a Primary text variant.",
               "4. Paste each line of `headlines.txt` as a Headline.",
               "5. Apply settings from `_ASSETS.md`.", "",
               "## Notes",
               "- WALKTHROUGH_3D videos still carry a Chinese creator watermark — run brand-pass to cover it + swap BGM (copyrighted) before upload.",
               "- HELP_REDESIGN / TIPS_DODONT have baked-in English text → English markets only (here: ZA).",
               "- FR/TR StorageVO are TTS-dubbed; do NOT run standard brand-pass on them (its voice-gate would re-dub them back to English)."]
    (CAMP / "README.md").write_text("\n".join(readme), encoding="utf-8")
    print(f"\nTotal: {grand} video slots across {len(C)} campaigns -> {CAMP}")


if __name__ == "__main__":
    main()
