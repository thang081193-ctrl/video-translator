#!/usr/bin/env python3
"""Emit the V1 (all music-only) job from an ALREADY-ORGANIZED main job. Copies the
main manifest into <job>_v1/_ultimate/manifest.json with has_voice=False + empty
transcript for every clip, so brandpass routes all clips to BGM_UNIVERSAL and
swaps in royalty-free BGM. organized_path/src_path stay absolute -> point at the
real files in the main job (no re-organize needed). Usage:
  python3 make_v1.py <main_job_dir>
"""
import json, sys
from pathlib import Path

JOB = Path(sys.argv[1])
mf = JOB / "_ultimate" / "manifest.json"
d = json.load(open(mf, encoding="utf-8"))
v1 = Path(str(JOB) + "_v1"); (v1 / "_ultimate").mkdir(parents=True, exist_ok=True)
d1 = json.loads(json.dumps(d))
n = 0
for v in d1["videos"]:
    v["has_voice"] = False
    v["transcript"] = ""
    v.pop("segments", None)
    n += 1
json.dump(d1, open(v1 / "_ultimate" / "manifest.json", "w", encoding="utf-8"),
          ensure_ascii=False, indent=2)
print(f"[make_v1] {n} clips -> {v1} (all music-only)")
