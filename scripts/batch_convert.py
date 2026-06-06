"""Convert every .mp4 under a date folder to a Meta Ads preset.

Walks language folders (skipping anything starting with `Converted` and
`Unknown`), converts each video using `pipeline.convert.convert_video()`,
and writes the output into `<root>/<out_dir>/<lang_folder>/<name>.mp4`.

Presets:
- reels (1080×1920 9:16, max-content + blur-pad short axis)
- feed  (1080×1350 4:5,  cover + center-crop short axis)

Default `<out_dir>` is `Converted_<preset>` so successive runs of
different presets land in separate sibling folders without clobbering.

Resumable: skips files whose target already exists.

Usage:  python batch_convert.py "<root>" [reels|feed] [<out_dir_name>]
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

# Make the project importable. parents[1] is the project root (works for
# both main repo and worktree layouts since each has its own pipeline/).
_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))

from pipeline.convert import PRESETS, convert_video  # noqa: E402

SKIP_PREFIXES = ("Converted", "Unknown")


def main(root: str, preset_name: str = "reels", out_dir_name: str | None = None) -> None:
    if preset_name not in PRESETS:
        print(f"Unknown preset '{preset_name}'. Choose from: {sorted(PRESETS)}",
              file=sys.stderr)
        sys.exit(2)
    preset = PRESETS[preset_name]

    if out_dir_name is None:
        out_dir_name = f"Converted_{preset_name}"

    root_p = Path(root)
    if not root_p.is_dir():
        print(f"Not a directory: {root}", file=sys.stderr)
        sys.exit(2)

    out_root = root_p / out_dir_name
    out_root.mkdir(exist_ok=True)

    folders = [d for d in sorted(root_p.iterdir())
               if d.is_dir() and not d.name.startswith(SKIP_PREFIXES)]

    total_videos = sum(len(list(f.glob("*.mp4"))) for f in folders)
    print(f"Preset: {preset.name} ({preset.width}×{preset.height}, fit_mode={preset.fit_mode})")
    print(f"Output: {out_root}")
    print(f"Found {total_videos} videos across {len(folders)} folders.\n")

    done = 0
    skipped = 0
    failed: list[tuple[str, str]] = []
    t0 = time.time()

    for folder in folders:
        out_dir = out_root / folder.name
        out_dir.mkdir(exist_ok=True)
        videos = sorted(folder.glob("*.mp4"))
        print(f"=== {folder.name} ({len(videos)} videos) ===")
        for v in videos:
            out = out_dir / v.name
            if out.exists() and out.stat().st_size > 0:
                skipped += 1
                done += 1
                print(f"  [{done:3d}/{total_videos}] SKIP (exists) {v.name}")
                continue
            t1 = time.time()
            try:
                convert_video(str(v), str(out), preset)
                done += 1
                dt = time.time() - t1
                print(f"  [{done:3d}/{total_videos}] OK   {v.name}  ({dt:.1f}s)")
            except Exception as e:
                done += 1
                failed.append((str(v), str(e)))
                print(f"  [{done:3d}/{total_videos}] FAIL {v.name}  {e}")
        print()

    total_dt = time.time() - t0
    print(f"\n=== Done in {total_dt:.0f}s ===")
    print(f"Converted: {done - skipped - len(failed)}")
    print(f"Skipped:   {skipped}")
    print(f"Failed:    {len(failed)}")
    for path, err in failed:
        print(f"  {path}: {err}")


if __name__ == "__main__":
    if len(sys.argv) < 2 or len(sys.argv) > 4:
        print(__doc__)
        sys.exit(1)
    root = sys.argv[1]
    preset = sys.argv[2] if len(sys.argv) >= 3 else "reels"
    out = sys.argv[3] if len(sys.argv) == 4 else None
    main(root, preset, out)
