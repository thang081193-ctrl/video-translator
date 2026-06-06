# -*- coding: utf-8 -*-
"""BGM advisor for meta-ads-prepare-ultimate.

Suggests trend-aware background music per video, factoring THREE axes the user
asked for:

  1. Location target  -> regional music trend (anglo / west-eu / latam / mena / ...)
  2. Language target  -> default region + whether a VO bed is needed
  3. Content target   -> the creative angle's energy (calm / uplifting / tension / modern)

...all tuned for the 2026 short-form-ad meta and the app's audience (a Plant /
home-wellness IAA app: plant-parents, millennial-GenZ, aesthetic-driven, calm
"cozy" leaning — NOT aggressive EDM / trap / dramatic orchestral).

Two ways to run (see SKILL.md "Step 2c — BGM advisor"):
  A. Manifest mode  : reads <src>/_ultimate/manifest.json, writes
                      bgm_suggestions.csv + bgm_shopping_list.md, optional --write
                      back of the refined bgm_cluster.
  B. One-shot mode  : `... bgm_suggest.py one --language fr --angle care-hack
                      --countries FR [--voice]` -> prints one suggestion.

Self-contained: pure-stdlib, reads the manifest JSON directly, no run.py needed
(run.py also exposes it as the `bgm-suggest` subcommand).
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# 1) LOCATION  — country -> region bucket, and region -> 2026 BGM trend profile
# ---------------------------------------------------------------------------
COUNTRY_REGION = {
    # anglo
    "US": "anglo", "UNITED STATES": "anglo", "UK": "anglo", "UNITED KINGDOM": "anglo",
    "CA": "anglo", "CANADA": "anglo", "AU": "anglo", "AUSTRALIA": "anglo",
    "NZ": "anglo", "NEW ZEALAND": "anglo", "IE": "anglo", "IRELAND": "anglo",
    # west / central EU
    "DE": "west_eu", "GERMANY": "west_eu", "FR": "west_eu", "FRANCE": "west_eu",
    "NL": "west_eu", "NETHERLANDS": "west_eu", "BE": "west_eu", "BELGIUM": "west_eu",
    "AT": "west_eu", "AUSTRIA": "west_eu", "CH": "west_eu", "SWITZERLAND": "west_eu",
    # south EU
    "IT": "south_eu", "ITALY": "south_eu", "ES": "south_eu", "SPAIN": "south_eu",
    "PT": "south_eu", "PORTUGAL": "south_eu", "GR": "south_eu", "GREECE": "south_eu",
    # nordics
    "SE": "nordics", "SWEDEN": "nordics", "NO": "nordics", "NORWAY": "nordics",
    "DK": "nordics", "DENMARK": "nordics", "FI": "nordics", "FINLAND": "nordics",
    "IS": "nordics", "ICELAND": "nordics",
    # east EU
    "PL": "east_eu", "POLAND": "east_eu", "CZ": "east_eu", "CZECHIA": "east_eu",
    "HU": "east_eu", "HUNGARY": "east_eu", "RO": "east_eu", "ROMANIA": "east_eu",
    "HR": "east_eu", "SK": "east_eu", "SI": "east_eu", "BG": "east_eu",
    "LT": "east_eu", "LV": "east_eu", "EE": "east_eu",
    # latam
    "BR": "latam", "BRAZIL": "latam", "MX": "latam", "MEXICO": "latam",
    "AR": "latam", "ARGENTINA": "latam", "CO": "latam", "CL": "latam",
    # mena
    "SA": "mena", "SAUDI ARABIA": "mena", "AE": "mena", "UNITED ARAB EMIRATES": "mena",
    "QA": "mena", "QATAR": "mena", "EG": "mena",
    # east asia
    "KR": "east_asia", "SOUTH KOREA": "east_asia", "JP": "east_asia", "JAPAN": "east_asia",
    "HK": "east_asia", "HONG KONG": "east_asia", "TW": "east_asia", "TAIWAN": "east_asia",
    # SE asia
    "ID": "sea", "INDONESIA": "sea", "MY": "sea", "PH": "sea", "TH": "sea", "VN": "sea",
    "SG": "sea", "SINGAPORE": "sea",
}

# ISO 639-1 -> the region whose trend best fits that language when no country given.
LANG_REGION = {
    "en": "anglo", "de": "west_eu", "fr": "west_eu", "nl": "west_eu",
    "it": "south_eu", "es": "south_eu", "pt": "south_eu", "el": "south_eu",
    "sv": "nordics", "no": "nordics", "da": "nordics", "fi": "nordics",
    "pl": "east_eu", "cs": "east_eu", "hu": "east_eu", "ro": "east_eu",
    "ar": "mena", "tr": "mena", "fa": "mena", "he": "mena",
    "ko": "east_asia", "ja": "east_asia", "zh": "east_asia",
    "id": "sea", "ms": "sea", "th": "sea", "vi": "sea", "tl": "sea", "fil": "sea",
    "ru": "east_eu", "uk": "east_eu",
}

# Region -> 2026 short-form-ad BGM trend profile (genre seeds + a one-line note).
# Seeds are Pixabay-search-ready; commercial-safe, no-attribution pool.
REGION_BGM = {
    "anglo": {
        "label": "US/UK/CA/AU — cozy-aesthetic",
        "seeds": ["lofi chill", "cozy acoustic", "aesthetic indie", "soft piano calm", "warm chillhop"],
        "trend": "Cozy 'that-girl' lofi + warm acoustic own US/UK plant & home content; understated, no drops.",
    },
    "west_eu": {
        "label": "DE/FR/NL — refined minimal",
        "seeds": ["minimal acoustic", "melodic chill house", "french touch chill", "calm electronica", "soft piano"],
        "trend": "Refined, minimal beds; subtle melodic house works for FR/DE feature demos.",
    },
    "south_eu": {
        "label": "IT/ES/PT — warm mediterranean",
        "seeds": ["mediterranean acoustic", "warm guitar happy", "indie pop sunny", "bossa acoustic", "feel good acoustic"],
        "trend": "Sun-warm acoustic guitar + light indie-pop; emotive, family-warm tone resonates in IT/ES/PT.",
    },
    "nordics": {
        "label": "SE/NO/DK/FI — airy dream",
        "seeds": ["airy indie folk", "dream pop ambient", "minimal piano", "scandinavian chill", "calm ambient"],
        "trend": "Airy, spacious indie-folk & dream-pop; very calm, design-led aesthetic.",
    },
    "east_eu": {
        "label": "PL/CZ/RO — folk-tinged chill",
        "seeds": ["acoustic pop chill", "folk acoustic warm", "calm electronica", "soft indie", "uplifting acoustic"],
        "trend": "Acoustic-pop & gentle electronica; warm folk tint plays well in PL/CZ/RO.",
    },
    "latam": {
        "label": "BR/MX — tropical feel-good",
        "seeds": ["bossa nova chill", "tropical acoustic happy", "latin acoustic feel good", "uplifting latin", "sunny ukulele"],
        "trend": "Tropical/bossa acoustic + sunny feel-good; energetic-but-warm wins BR/MX home content.",
    },
    "mena": {
        "label": "AR/SA/AE — calm oud-fusion",
        "seeds": ["arabic ambient calm", "oud chill", "middle east ambient", "ethnic chill calm", "spa oriental"],
        "trend": "Calm oud/ethnic-fusion ambient; soft, premium, never aggressive for MENA wellness.",
    },
    "east_asia": {
        "label": "KR/JP — kawaii city-pop",
        "seeds": ["kawaii cute", "city pop chill", "lofi cute", "soft k indie", "gentle piano cute"],
        "trend": "Cute kawaii + city-pop/lofi; gentle, aesthetic, playful for KR/JP.",
    },
    "sea": {
        "label": "ID/PH/TH/VN — tropical cheerful",
        "seeds": ["tropical pop happy", "cheerful acoustic", "lofi tropical", "ukulele happy", "sunny pop"],
        "trend": "Cheerful tropical-pop & ukulele; bright, optimistic tone for SEA.",
    },
}
DEFAULT_REGION = "anglo"

# ---------------------------------------------------------------------------
# 2/3) CONTENT  — angle -> energy bucket -> mood + pool cluster + bpm + seeds
# ---------------------------------------------------------------------------
# Energy buckets and how they map onto the 4 --bgm-pool cluster folders.
ENERGY = {
    "calm": {
        "cluster": "C_lofi_chill", "mood": "calm / satisfying / ASMR-friendly",
        "bpm": "70-90", "seeds": ["lofi", "chill", "gentle"],
    },
    "uplift": {
        "cluster": "B_uplifting", "mood": "uplifting build / hopeful / feel-good",
        "bpm": "95-120", "seeds": ["uplifting", "feel good", "inspiring acoustic"],
    },
    "tension": {
        "cluster": "A_calm_nature", "mood": "intriguing tension -> warm resolve (hook-friendly)",
        "bpm": "85-110", "seeds": ["curious", "suspense light", "minimal tension"],
    },
    "modern": {
        "cluster": "D_corporate", "mood": "clean / modern / positive (app demo)",
        "bpm": "100-124", "seeds": ["positive corporate", "modern upbeat", "clean tech"],
    },
}

# audience profile (plant / home-wellness IAA) — steers AWAY from these and TOWARD calm/cozy.
AUDIENCE_NOTE = ("Plant/home-wellness IAA audience (plant-parents, millennial-GenZ, "
                 "aesthetic-led): favor cozy/calm/warm; AVOID aggressive EDM, trap, "
                 "dramatic orchestral, hard rock.")


def angle_energy(angle: str) -> str:
    """Map a creative angle string to an energy bucket (keyword-based, forgiving)."""
    a = (angle or "").lower()
    if any(k in a for k in ("myth", "authority", "mistake", "wrong", "stop")):
        return "tension"
    if any(k in a for k in ("transform", "testimonial", "diagnos", "rescue", "revive", "before")):
        return "uplift"
    if any(k in a for k in ("feature", "demo", "rules", "showcase", "benefit")):
        return "modern"
    # care-hack, how-to-scan, curiosity, beginner, id, tutorial -> calm by default
    return "calm"


def pick_region(language: str, countries: list[str]) -> tuple[str, list[str]]:
    """Region for trend purposes. Country list (if any) wins by majority; else language default."""
    regions = []
    for c in countries or []:
        r = COUNTRY_REGION.get(c.strip().upper())
        if r:
            regions.append(r)
    if regions:
        # majority region
        top = max(set(regions), key=regions.count)
        others = sorted(set(regions) - {top})
        return top, others
    return LANG_REGION.get((language or "").lower(), DEFAULT_REGION), []


def suggest(language: str, angle: str, has_voice: bool,
            countries: list[str] | None = None) -> dict:
    """Core advisor. Returns a dict with cluster, mood, bpm, genres, pixabay_queries, trend_note."""
    countries = countries or []
    region, also = pick_region(language, countries)
    rb = REGION_BGM.get(region, REGION_BGM[DEFAULT_REGION])
    eb = angle_energy(angle)
    e = ENERGY[eb]

    # Pixabay queries = clean region genre seeds + one energy anchor. Kept SHORT
    # (<=3 words) so Pixabay returns enough results — the cluster/mood/bpm carry the
    # energy, the queries carry the regional flavour.
    anchor = {"calm": "lofi chill", "uplift": "uplifting acoustic",
              "tension": "curious minimal", "modern": "positive corporate"}[eb]
    queries = list(dict.fromkeys(rb["seeds"]))[:4]
    if anchor not in queries:
        queries.append(anchor)

    # VO bed vs hero track
    if has_voice:
        voice_note = ("VOICE bed: instrumental, low-energy, NO vocal samples, steady "
                      "(ducked under VO). Use Pixabay's 'instrumental' filter.")
        queries.append("instrumental background")
    else:
        voice_note = "MUSIC-ONLY hero: trendier/hookier OK, more character & energy."
    queries = list(dict.fromkeys(queries))[:5]

    trend = rb["trend"]
    if also:
        trend += f"  (mixed markets: also {', '.join(also)} — pick neutral tracks.)"

    return {
        "region": region,
        "region_label": rb["label"],
        "energy": eb,
        "cluster": e["cluster"],
        "mood": e["mood"],
        "bpm": e["bpm"],
        "pixabay_queries": queries,
        "voice_note": voice_note,
        "trend_note": trend,
    }


# ---------------------------------------------------------------------------
# Manifest mode
# ---------------------------------------------------------------------------
def _manifest_path(src: Path) -> Path:
    return src / "_ultimate" / "manifest.json"


def cmd_manifest(args):
    src = Path(args.src).resolve()
    mpath = _manifest_path(src)
    if not mpath.is_file():
        sys.exit(f"manifest not found: {mpath} (run scan first)")
    data = json.loads(mpath.read_text(encoding="utf-8"))
    countries = [c for c in (args.countries or "").split(",") if c.strip()]

    rows = []
    shopping = {}  # cluster -> {region_label -> set(queries)} + trend
    for v in data.get("videos", []):
        if v.get("vertical") == "skip" or not v.get("language"):
            continue
        # Per-video region comes from the video's OWN language (its native market),
        # which is correct for the keep-original-language flow. --countries only
        # annotates the campaign scope in the shopping-list header below.
        s = suggest(v.get("language", ""), v.get("angle", ""),
                    bool(v.get("has_voice")), [])
        if args.write:
            v["bgm_cluster"] = s["cluster"]
        rows.append([
            v.get("renamed") or v["orig_name"], v.get("language", ""),
            v.get("angle", ""), "yes" if v.get("has_voice") else "no",
            s["region_label"], s["cluster"], s["mood"], s["bpm"],
            " ; ".join(s["pixabay_queries"]), s["trend_note"],
        ])
        bucket = shopping.setdefault(s["cluster"], {"by_region": {}, "trends": set()})
        bucket["by_region"].setdefault(s["region_label"], set()).update(s["pixabay_queries"])
        bucket["trends"].add(s["trend_note"])

    # CSV
    out_csv = src / "_ultimate" / "bgm_suggestions.csv"
    with open(out_csv, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["file", "language", "angle", "has_voice", "region",
                    "cluster", "mood", "bpm", "pixabay_queries", "trend_note"])
        w.writerows(rows)

    # Shopping list (markdown) — what to download into each pool folder.
    out_md = src / "_ultimate" / "bgm_shopping_list.md"
    lines = ["# BGM shopping list (Pixabay — free / no-attribution / commercial OK)",
             "", f"Audience: {AUDIENCE_NOTE}", ""]
    if countries:
        lines += [f"Target locations: {', '.join(countries)}", ""]
    for cluster in ["A_calm_nature", "B_uplifting", "C_lofi_chill", "D_corporate"]:
        b = shopping.get(cluster)
        if not b:
            continue
        lines.append(f"## `{cluster}/`  — grab 3-5 tracks per region below")
        for region_label, qs in sorted(b["by_region"].items()):
            qlinks = " · ".join(f"[{q}](https://pixabay.com/music/search/{q.replace(' ', '%20')}/)"
                                for q in sorted(qs))
            lines.append(f"- **{region_label}** → {qlinks}")
        lines.append("")
    out_md.write_text("\n".join(lines), encoding="utf-8")

    if args.write:
        mpath.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[bgm-suggest] {len(rows)} videos -> {out_csv.name} + {out_md.name}"
          f"{'  (bgm_cluster written back)' if args.write else ''}", flush=True)
    # echo the shopping list to stdout for convenience
    print("\n" + out_md.read_text(encoding="utf-8"), flush=True)


def cmd_one(args):
    countries = [c for c in (args.countries or "").split(",") if c.strip()]
    s = suggest(args.language, args.angle, args.voice, countries)
    print(json.dumps(s, ensure_ascii=False, indent=2))


def build_parser():
    p = argparse.ArgumentParser(description="BGM advisor (location x language x content, trend-aware)")
    sub = p.add_subparsers(dest="cmd", required=True)

    m = sub.add_parser("manifest", help="read manifest -> bgm_suggestions.csv + shopping list")
    m.add_argument("--src", required=True)
    m.add_argument("--countries", default="", help="comma list, e.g. US,FR,BR,SA")
    m.add_argument("--write", action="store_true", help="write refined bgm_cluster back to manifest")
    m.set_defaults(func=cmd_manifest)

    o = sub.add_parser("one", help="one-shot suggestion")
    o.add_argument("--language", required=True)
    o.add_argument("--angle", default="")
    o.add_argument("--countries", default="")
    o.add_argument("--voice", action="store_true", help="video has voice/VO (bed mode)")
    o.set_defaults(func=cmd_one)
    return p


if __name__ == "__main__":
    args = build_parser().parse_args()
    args.func(args)
