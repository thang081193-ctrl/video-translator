"""signature.py — recognize already-processed videos so re-runs skip them.

A hidden mp4 metadata "signature" is written into the container `comment` atom
(survives mux, ffprobe-readable) via **stream-copy + atomic temp-replace** — no
re-encode, so an already-finished (branded/dedup'd) deliverable is NOT touched
visually, it just gains a provenance tag.

Why: a folder may already be fully brand-passed. Re-running the pipeline would
double-watermark / waste hours. Stamp each processed file once; afterwards
`scan --skip-processed` (and brandpass) recognize the tag and skip the file.

Tag format (pipe-delimited key=val), e.g.:
    decoai_PROCESSED|batch=2805|brandpassed=yes|tool=meta-ads-ultimate|date=2026-06-09|bgm_swap=...

Recognition is deliberately loose + backward-compatible:
  is_processed() is True if the comment contains "PROCESSED" OR
  "tool=meta-ads-ultimate". So legacy `DECOAI_PROCESSED|...` tags still match.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import time
from datetime import date as _date
from pathlib import Path

TOOL = "meta-ads-ultimate"            # canonical provenance marker
_MARKERS = ("PROCESSED", f"tool={TOOL}")


def build(app: str = "", batch: str = "", processed_date: str | None = None,
          extra: str = "") -> str:
    """Compose a signature string. `app` becomes the `<app>_PROCESSED` prefix."""
    prefix = (app.strip() or "META_ADS")
    if not prefix.upper().endswith("PROCESSED"):
        prefix = f"{prefix}_PROCESSED"
    parts = [prefix]
    if batch:
        parts.append(f"batch={batch}")
    parts.append("brandpassed=yes")
    parts.append(f"tool={TOOL}")
    parts.append(f"date={processed_date or _date.today().isoformat()}")
    if extra:
        parts.append(extra.lstrip("|"))
    return "|".join(parts)


def read_comment(path: str | Path) -> str:
    """Return the mp4 container `comment` tag (empty string if none/error)."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format_tags=comment",
             "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True, timeout=30,
        )
        return (r.stdout or "").strip()
    except Exception:
        return ""


def is_processed(path: str | Path) -> bool:
    """True if the file already carries a pipeline signature (any vintage)."""
    c = read_comment(path)
    return any(m in c for m in _MARKERS)


def parse(comment: str) -> dict:
    """Best-effort parse of a signature comment back into a dict."""
    out: dict[str, str] = {}
    for i, tok in enumerate(comment.split("|")):
        tok = tok.strip()
        if not tok:
            continue
        if "=" in tok:
            k, v = tok.split("=", 1)
            out[k.strip()] = v.strip()
        elif i == 0:
            out["marker"] = tok
    return out


def stamp(path: str | Path, signature: str) -> tuple[bool, str]:
    """Write `signature` into the mp4 comment in-place (stream-copy, atomic).

    Returns (ok, err). Idempotent — re-stamping just overwrites the comment.
    """
    path = Path(path)
    fd, tmp = tempfile.mkstemp(suffix=".mp4", dir=str(path.parent))
    os.close(fd)
    try:
        r = subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-i", str(path), "-map", "0",
             "-c", "copy", "-metadata", "comment=" + signature, tmp],
            capture_output=True, text=True, timeout=600,
        )
        if r.returncode != 0:
            return False, (r.stderr or r.stdout or "ffmpeg failed")[-300:]
        # Windows can throw a transient PermissionError (AV / handle still open)
        # on the rename — retry a few times before giving up.
        for attempt in range(5):
            try:
                os.replace(tmp, str(path))
                return True, ""
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.4)
        return True, ""
    except Exception as e:  # noqa: BLE001
        return False, str(e)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def stamp_many(paths, app: str = "", batch: str = "", note: str = "",
               on_each=None) -> tuple[int, int]:
    """Stamp a list of files with one signature. Returns (ok_n, fail_n)."""
    sig = build(app=app, batch=batch, extra=note)
    ok = fail = 0
    for p in paths:
        good, err = stamp(p, sig)
        ok, fail = (ok + 1, fail) if good else (ok, fail + 1)
        if on_each:
            on_each(p, good, err)
    return ok, fail
