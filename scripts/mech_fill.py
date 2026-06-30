#!/usr/bin/env python3
"""Mechanical manifest fill for the BGM-only (V1) path + V2 base — NO translation.
Sets vertical, language fields (from whisper_lang), angle (keyword classifier),
bgm_cluster. Writes the enriched manifest back (V2 base) AND a sibling *_v1 job dir
whose manifest has has_voice=False for every clip (so brandpass routes all to
BGM_UNIVERSAL music-only). Run on Vast after scan. Usage:
  python3 mech_fill.py <job_dir> <app: deco|score>
"""
import json, os, sys, shutil
from pathlib import Path

JOB = Path(sys.argv[1]); APP = sys.argv[2]
ROOT = os.environ.get("VIDEO_TRANSLATOR_ROOT", "/workspace/video-translator")
sys.path.insert(0, os.path.join(ROOT, "_claude_skills", "meta-ads-prepare-ultimate"))
try:
    import langmaps as L
    iso2en = L.iso_to_english
except Exception:
    iso2en = lambda c: {"en":"English","id":"Indonesian","fr":"French","th":"Thai",
                        "vi":"Vietnamese","es":"Spanish","pt":"Portuguese","ar":"Arabic",
                        "ru":"Russian","de":"German","tr":"Turkish"}.get((c or "en").lower(), (c or "en").title())

CFG = {
  "deco": {"vertical": "home", "default_angle": "showcase", "bgm": "C_lofi",
    "angles": {
      "floorplan_3d": ["3d","floor plan","floorplan","render","virtual","walk through","walkthrough","layout","dimension"],
      "small_room":   ["small","tiny","bigger","larger","studio","cramped","maximize","more space","feel bigger"],
      "zero_spend":   ["without buying","existing furniture","already have","no spend","save money","budget","for free","don't buy"],
      "dream_home_tap":["dream home","dream house","dream space","one tap","design your"],
      "instant_magic":["second","instant","snap","upload","photo","magic","transform","turn any","in seconds","ai design"],
    }},
  "score": {"vertical": "sports", "default_angle": "fan_experience", "bgm": "B_indiepop",
    "angles": {
      "ai_predictions":["predict","prediction","tip","odds","forecast","betting","bet ","ai analysis"],
      "stats_depth":  ["lineup","line-up","head to head","head-to-head","h2h","player stat","statistics","stats","data"],
      "standings_fixtures":["table","standing","fixture","schedule","results","league table","upcoming"],
      "all_competitions":["league","competition","champions","worldwide","all leagues","coverage","every league"],
      "live_scores":  ["live score","live scores","goal","real-time","real time","alert","notification","minute by minute","score update"],
    }},
}[APP]

def classify(text: str) -> str:
    t = (text or "").lower()
    best, score = CFG["default_angle"], 0
    for ang, kws in CFG["angles"].items():
        s = sum(1 for kw in kws if kw in t)
        if s > score:
            best, score = ang, s
    return best

mf = JOB / "_ultimate" / "manifest.json"
d = json.load(open(mf, encoding="utf-8"))
vs = d["videos"]
n_voice = 0
for v in vs:
    wl = (v.get("whisper_lang") or "en").lower()
    v["vertical"] = CFG["vertical"]
    v["lang_code"] = wl
    v["language"] = iso2en(wl)
    v["language_folder"] = v["language"]
    v.setdefault("bgm_cluster", CFG["bgm"])
    if not v.get("bgm_cluster"):
        v["bgm_cluster"] = CFG["bgm"]
    v["angle"] = classify(v.get("transcript") or "")
    if v.get("has_voice"):
        n_voice += 1
json.dump(d, open(mf, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print(f"[mech_fill] {APP}: {len(vs)} videos, voiced={n_voice}, broll={len(vs)-n_voice}")
from collections import Counter
print("  angles:", dict(Counter(v["angle"] for v in vs)))
print("[mech_fill] done. Run organize on this job, then make_v1.py to emit the V1 variant.")
