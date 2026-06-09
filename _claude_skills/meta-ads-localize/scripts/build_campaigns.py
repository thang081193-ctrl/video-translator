#!/usr/bin/env python3
"""Build Meta Ads campaign folders from a config (Claude authors the config per batch).

config (JSON list), each campaign:
  {"name":"C1_FR_StorageVO", "glob":"STORAGE_VO/FR/*.mp4", "lang":"Français",
   "country":"France", "note":"...", "primary":["...","..."], "headlines":["...","..."]}

For each: hardlink the glob'd videos into _campaigns/<name>/ and write videos.txt,
primary_text.txt, headlines.txt, country.txt, _ASSETS.md. Plus a top-level README.md.
Copy must already be in the campaign's target language. ~10-12 videos per campaign.

Usage: python build_campaigns.py --root <folder> --config campaigns.json [--app "App Name"]
"""
import argparse
import csv
import io
import json
import os
import sys
from glob import glob

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

SETTINGS = [
    "- Campaign: Advantage+ App Promotion (post-AAC)",
    "- Optimization: Value (event `AdImpression`)",
    "- Bid strategy: Highest Volume",
    "- Audience: Advantage+ Audience | Placements: Advantage+ (Reels-first)",
    "- Dynamic Creative: ON | CTA: Use App / Install Now",
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--config", required=True)
    ap.add_argument("--app", default="DecoAI")
    a = ap.parse_args()

    cfg_path = a.config if os.path.isabs(a.config) else os.path.join(a.root, a.config)
    campaigns = json.load(open(cfg_path, encoding="utf-8"))
    camp_root = os.path.join(a.root, "_campaigns")
    os.makedirs(camp_root, exist_ok=True)

    grand = 0
    master = []
    for c in campaigns:
        cdir = os.path.join(camp_root, c["name"])
        os.makedirs(cdir, exist_ok=True)
        srcdir = os.path.join(a.root, os.path.dirname(c["glob"]))
        # MAX 12 videos (= ads) per campaign. Select via explicit `videos`, a `slice`
        # [start,end] of the sorted glob (used to distribute a universal angle across
        # countries), or the first `limit` (default 12). Overflow stays in the source
        # folder as a creative refresh pool.
        all_v = sorted(os.path.basename(p) for p in glob(os.path.join(a.root, c["glob"])))
        if c.get("videos"):
            vids = c["videos"]
        elif c.get("slice"):
            vids = all_v[c["slice"][0]:c["slice"][1]]
        else:
            vids = all_v[:c.get("limit", 12)]
        if len(vids) > 12:
            print(f"  WARN {c['name']}: {len(vids)} videos exceeds the 12/campaign cap")
        pooled = len(all_v) - len(vids)

        open(os.path.join(cdir, "videos.txt"), "w", encoding="utf-8").write("\n".join(vids) + "\n")
        open(os.path.join(cdir, "primary_text.txt"), "w", encoding="utf-8").write(
            "\n".join(c["primary"]).strip() + "\n")
        open(os.path.join(cdir, "headlines.txt"), "w", encoding="utf-8").write(
            "\n".join(c["headlines"]).strip() + "\n")
        open(os.path.join(cdir, "country.txt"), "w", encoding="utf-8").write(c["country"] + "\n")

        linked = 0
        for v in vids:
            dst = os.path.join(cdir, v)
            if os.path.exists(dst):
                continue
            try:
                os.link(os.path.join(srcdir, v), dst)
                linked += 1
            except OSError as e:
                print(f"  WARN link {v}: {e}")

        md = [f"# {c['name']} — {a.app} Meta Ads", "",
              f"**Angle/note:** {c.get('note','')}", f"**Language:** {c['lang']}",
              f"**Country (paste into bulk locations):** {c['country']}",
              f"**Videos:** {len(vids)}", "",
              "## Videos (drag-drop from this folder)", ""]
        md += [f"- `{v}`" for v in vids]
        md += ["", "## Primary text (one per line — each = a variant)", ""]
        md += [f"{i}. {p}" for i, p in enumerate(c["primary"], 1)]
        md += ["", "## Headlines", ""]
        md += [f"{i}. {h}" for i, h in enumerate(c["headlines"], 1)]
        md += ["", "## Settings", f"- App: {a.app}"] + SETTINGS
        open(os.path.join(cdir, "_ASSETS.md"), "w", encoding="utf-8").write("\n".join(md))

        # Split into INDIVIDUAL ADS: 1 video = 1 ad, copy rotated through the pools.
        prim = c["primary"] or [""]
        head = c["headlines"] or [""]
        ads = []
        for i, v in enumerate(vids):
            ad = {"ad_name": f"{c['name']}_ad{i+1:02d}", "video": v,
                  "primary_text": prim[i % len(prim)], "headline": head[i % len(head)]}
            ads.append(ad)
            master.append({"campaign": c["name"], "country": c["country"], **ad})
        with open(os.path.join(cdir, "ads.csv"), "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=["ad_name", "video", "primary_text", "headline"])
            w.writeheader()
            w.writerows(ads)

        grand += len(vids)
        pool = f", +{pooled} refresh-pool" if pooled else ""
        print(f"{c['name']:<22} {len(vids):>2} ads (linked {linked}{pool}) "
              f"[{c['lang']} / {c['country'][:34]}]")

    readme = [f"# {a.app} — Meta Ads campaigns", "",
              f"{len(campaigns)} campaigns, {grand} video slots (angle x country).", "",
              "| Campaign | Lang | Country | Videos |", "|---|---|---|---|"]
    for c in campaigns:
        n = len(glob(os.path.join(a.root, c["glob"])))
        readme.append(f"| {c['name']} | {c['lang']} | {c['country']} | {n} |")
    readme += ["", "## Per-campaign upload flow",
               "1. `country.txt` -> copy -> Meta 'Add locations in bulk'.",
               "2. `ads.csv` lists each ad = 1 video + its primary text + headline "
               "(create them as separate ads), OR use Dynamic Creative with the pooled .txt files.",
               "3. Drag-drop the mp4 files from the campaign folder.",
               "4. Apply settings from `_ASSETS.md`.",
               "Master list of every ad across all campaigns: `all_ads.csv`.", "",
               "## Notes",
               "- Dubbed (non-EN) videos: do NOT run standard meta-ads-prepare (voice-gate re-dubs to EN).",
               "- WALKTHROUGH_3D may carry a creator watermark; HELP/TIPS have baked EN CTA -> meta-ads-prepare before upload.",
               "- Per-country BGM swap = cleared-for-ads tracks only."]
    open(os.path.join(camp_root, "README.md"), "w", encoding="utf-8").write("\n".join(readme))

    with open(os.path.join(camp_root, "all_ads.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["campaign", "country", "ad_name", "video",
                                          "primary_text", "headline"])
        w.writeheader()
        w.writerows(master)

    print(f"\nTotal: {grand} videos = {len(master)} individual ads across "
          f"{len(campaigns)} campaigns -> {camp_root}")
    print(f"Per-campaign ads.csv + master all_ads.csv written.")


if __name__ == "__main__":
    main()
