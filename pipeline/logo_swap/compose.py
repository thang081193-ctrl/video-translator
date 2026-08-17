"""Render a logo-swapped clip: decode -> composite in numpy -> pipe to ffmpeg.

Why a rawvideo pipe and not a chain of timed `overlay` filters (the pattern in
pipeline/burn.py): measured on this machine, ffmpeg accepts ~300 `-i` inputs and
dies at 600 with `WinError 206` -- the Windows CreateProcess argv limit is
32,767 bytes and each `-i <png>` costs ~56 of them. Clips here need 241..1707
overlays. `-filter_complex_script` does NOT help: it moves only the filter
STRING to a file, the `-i` arguments stay on the command line. Piping frames
sidesteps all of it and measured ~5.6 ms/frame end to end.

Two other things this module is careful about:

* fps is taken as the exact `r_frame_rate` FRACTION from ffprobe and handed
  verbatim to `-r`. cv2.CAP_PROP_FPS returns a float, and a 30000/1001 source
  would round to 30.0 and drift audio out of sync silently.
* audio comes from a SECOND input (the original file) so it is never re-timed
  by the video path. With no cuts it is stream-copied bit-exact.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

import cv2
import numpy as np

from pipeline.logger import get_logger
from pipeline.logo_swap import render as R
from pipeline.logo_swap import track as T
from pipeline.logo_swap.spec import (
    FILL_BLUR, FILL_CLEAN_PLATE, FILL_LOGO_ROUNDED, FILL_LOGO_SQUARE,
    Box, ClipSpec, StaticSpec, TrackedSpec,
)

log = get_logger("LogoSwap")

AUDIO_FADE = 0.15   # seconds of fade either side of a splice


# ── probing ──────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class VideoInfo:
    width: int
    height: int
    fps: Fraction
    duration: float
    nb_frames: int
    has_audio: bool

    @property
    def fps_f(self) -> float:
        return float(self.fps)


def probe_video(path: str | Path) -> VideoInfo:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format",
         "-of", "json", str(path)],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise RuntimeError(f"ffprobe failed on {path}: {r.stderr[-400:]}")
    d = json.loads(r.stdout)
    v = next((s for s in d["streams"] if s.get("codec_type") == "video"), None)
    if v is None:
        raise RuntimeError(f"{path}: no video stream")
    has_audio = any(s.get("codec_type") == "audio" for s in d["streams"])
    fps = Fraction(v.get("r_frame_rate") or "25/1")
    dur = float(d["format"].get("duration") or v.get("duration") or 0.0)
    nb = int(v.get("nb_frames") or 0) or int(round(dur * float(fps)))
    return VideoInfo(int(v["width"]), int(v["height"]), fps, dur, nb, has_audio)


# ── per-frame plan ───────────────────────────────────────────────────────────

@dataclass
class FrameOp:
    """One composite to apply to one frame, already resolved to pixels.

    (x, y, w, h) is the COVER rect -- padded, so no rim of the competitor mark
    survives. `inset` then shrinks the rect the mark is DRAWN into, so the
    replacement lands at the mark's true size instead of the padded size.
    Covering and drawing at the same padded size makes the new logo visibly
    larger than the icon slot it sits in.
    """

    target_id: str
    x: int
    y: int
    w: int
    h: int
    fill: str
    corner_radius: float
    feather_px: float
    grade: bool
    cal: R.GradeCal | None
    plate: np.ndarray | None      # pre-sampled plate for statics, else None
    inset: int = 0
    shadow: bool = False
    # A blinking card cannot be described by a time range alone: clips 1/13/81
    # show their name pill in ~3s bursts, and a window that overshoots by a beat
    # paints a bare rectangle onto the footage -- worse than leaving the card.
    # When set, the op only fires on frames whose box still looks like this.
    ref_sig: np.ndarray | None = None
    # Measured on clip 81: while the card is up the distance runs 0.0-22.8, and
    # the moment it leaves it jumps to 53-96. The gap is enormous, so the
    # threshold only has to land inside it. 22 sat on the wrong edge and dropped
    # the half-second right after the card finished sliding in.
    ref_tol: float = 35.0


def _sig(patch: np.ndarray) -> np.ndarray:
    """Coarse grayscale fingerprint of a region, cheap enough for every frame."""
    g = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY) if patch.ndim == 3 else patch
    return cv2.resize(g, (16, 16)).astype(np.float32).ravel()


def kept_segments(spec: ClipSpec, duration: float) -> list[tuple[float, float]]:
    """Complement of the cut list, in source time."""
    cuts = sorted(
        ((c.start, duration if c.end is None else min(c.end, duration))
         for c in spec.cuts),
        key=lambda p: p[0],
    )
    merged: list[list[float]] = []
    for s, e in cuts:
        if e <= s:
            continue
        if merged and s <= merged[-1][1] + 1e-6:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    segs, pos = [], 0.0
    for s, e in merged:
        if s > pos + 1e-6:
            segs.append((pos, s))
        pos = max(pos, e)
    if pos < duration - 1e-6:
        segs.append((pos, duration))
    return segs or [(0.0, duration)]


def _cut_frames(spec: ClipSpec, info: VideoInfo) -> set[int]:
    drop: set[int] = set()
    for c in spec.cuts:
        end = info.duration if c.end is None else c.end
        a = int(round(c.start * info.fps_f))
        b = int(round(end * info.fps_f))
        # valid frame indices are 0..nb_frames-1; clamping to nb_frames+1 here
        # would count a non-existent frame and make expected_frames one short
        drop.update(range(a, min(b, info.nb_frames)))
    return drop


def _resolve_static(s: StaticSpec, src: str, info: VideoInfo,
                    cache: R.VariantCache) -> tuple[FrameOp, tuple[int, int]]:
    """Pre-sample the plate and calibrate the grade for a fixed-position target."""
    mark_px = s.box.to_px(info.width, info.height)
    box_px = s.box.pad(s.pad).to_px(info.width, info.height)
    x, y, w, h = box_px
    inset = 0          # see composite_frame: a plate ring reads worse than a
                       # slightly-large mark over textured backgrounds

    # a reference frame from inside the target's own visible window
    ref_t = s.ref_t if s.ref_t is not None else s.t[0] + 0.1
    ref_t = min(max(ref_t, 0.0), max(0.0, info.duration - 0.05))
    cap = cv2.VideoCapture(src)
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(round(ref_t * info.fps_f)))
    ok, ref = cap.read()
    cap.release()
    if not ok:
        raise RuntimeError(f"{src}: cannot read reference frame for static {s.id}")

    plate = None
    if s.plate_from is not None:
        plate = R.sampled_plate(src, s.plate_from, info.fps_f, box_px)
        if plate is None:
            log.warning(f"{s.id}: plate_from={s.plate_from} unreadable, "
                        f"falling back to a flat plate")
    if plate is None:
        plate = (R.blur_plate(ref, box_px) if s.fill == FILL_BLUR
                 else R.inner_plate(ref, box_px) if s.fill == FILL_CLEAN_PLATE
                 else R.flat_plate(ref, box_px))

    cal = None
    if s.grade and s.fill in (FILL_LOGO_ROUNDED, FILL_LOGO_SQUARE):
        shape = R.SHAPE_SQUARE if s.fill == FILL_LOGO_SQUARE else R.SHAPE_ROUNDED
        premul, inv = cache.get(w, h, s.corner_radius, s.feather_px, shape)
        probe = ((premul + plate.astype(np.uint16) * inv) // 255).astype(np.uint8)
        cal = R.calibrate_grade(ref, box_px, probe)

    ref_patch = ref[y:y + h, x:x + w]
    op = FrameOp(target_id=s.id, x=x, y=y, w=w, h=h, fill=s.fill,
                 corner_radius=s.corner_radius, feather_px=s.feather_px,
                 grade=s.grade, cal=cal, plate=plate, inset=inset,
                 ref_sig=_sig(ref_patch) if ref_patch.size else None)
    frames = (int(round(s.t[0] * info.fps_f)), int(round(s.t[1] * info.fps_f)))
    return op, frames


def plan_tracked(k: TrackedSpec, src: str, info: VideoInfo,
                 cache: R.VariantCache) -> tuple[dict[int, FrameOp], dict]:
    """Track a moving mark and turn the gated track into per-frame ops."""
    seed_px = k.seed_box.to_px(info.width, info.height)
    tpl = T.seed_template(src, k.seed_t, seed_px, info.fps_f)
    tpl_h, tpl_w = tpl.shape[:2]

    raw = T.track_logo(src, tpl, seed_px, t0=k.t[0], t1=k.t[1],
                       fps=info.fps_f, cfg=k.search)
    gated = T.gate_track(raw, k.search)
    smooth = T.smooth_track(gated)
    report = T.track_report(smooth, tpl_w, tpl_h)
    log.info(f"track {k.id}: on={report.get('on_pct')}% "
             f"score_med={report.get('score_med')} runs={report.get('on_runs')} "
             f"travel={report.get('travel_x')}x{report.get('travel_y')}px")

    # The COVER is what must hide the competitor mark; the template is only what
    # we match on. Hold their offset + size fixed in template space and scale it
    # with the track, so a small distinctive template can drive a large cover.
    mark_box = k.cover_box or k.seed_box
    mark_px = mark_box.to_px(info.width, info.height)
    cover_px = mark_box.pad(k.pad).to_px(info.width, info.height)
    dx = cover_px[0] - seed_px[0]
    dy = cover_px[1] - seed_px[1]
    cw, ch = cover_px[2], cover_px[3]
    inset_base = 0     # draw across the pad; see composite_frame

    # calibrate once, on the seed frame, against the cover region
    cap = cv2.VideoCapture(src)
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(round(k.seed_t * info.fps_f)))
    ok, ref = cap.read()
    cap.release()
    cal = None
    if ok and k.grade:
        shape = R.SHAPE_SQUARE if k.fill == FILL_LOGO_SQUARE else R.SHAPE_ROUNDED
        premul, inv = cache.get(cw, ch, k.corner_radius, k.feather_px, shape)
        plate = R.flat_plate(ref, cover_px)
        probe = ((premul + plate.astype(np.uint16) * inv) // 255).astype(np.uint8)
        cal = R.calibrate_grade(ref, cover_px, probe)

    ops: dict[int, FrameOp] = {}
    for s in smooth:
        if not s.on:
            continue
        ops[s.n] = FrameOp(
            target_id=k.id,
            x=int(round(s.x + dx * s.scale)),
            y=int(round(s.y + dy * s.scale)),
            w=max(8, int(round(cw * s.scale))),
            h=max(8, int(round(ch * s.scale))),
            fill=k.fill, corner_radius=k.corner_radius,
            feather_px=k.feather_px, grade=k.grade, cal=cal, plate=None,
            inset=int(round(inset_base * s.scale)),
        )
    return ops, report


# ── compositing ──────────────────────────────────────────────────────────────

def composite_frame(frame: np.ndarray, ops: list[FrameOp],
                    cache: R.VariantCache, rng: np.random.Generator) -> np.ndarray:
    """Apply every op to `frame` in place, cover-then-draw."""
    H, W = frame.shape[:2]
    for op in ops:
        x, y = max(0, op.x), max(0, op.y)
        w, h = min(op.w, W - x), min(op.h, H - y)
        if w < 4 or h < 4:
            continue
        box = (x, y, w, h)

        if op.ref_sig is not None:
            here = frame[y:y + h, x:x + w]
            if here.size == 0:
                continue
            if float(np.abs(_sig(here) - op.ref_sig).mean()) > op.ref_tol:
                continue          # the card is not on screen in this frame

        if op.fill in (FILL_CLEAN_PLATE, FILL_BLUR):
            plate = op.plate
            if plate is None:
                plate = (R.blur_plate(frame, box) if op.fill == FILL_BLUR
                         else R.inner_plate(frame, box))
            elif plate.shape[:2] != (h, w):
                plate = cv2.resize(plate, (w, h), interpolation=cv2.INTER_AREA)
            frame[y:y + h, x:x + w] = plate
            continue

        # Composite the logo straight onto the frame -- no rectangular plate.
        # Painting a flat plate over the cover rect and drawing the rounded logo
        # on top leaves the plate visible at the four corners, where it reads as
        # a coloured border: clip 80 picked up a tan ring from the actor's hand,
        # clip 78 a pink one from the phone bezel. The marks being replaced are
        # themselves rounded app icons, so whatever sits outside our logo's
        # corners is the same background that sat outside theirs. The pad makes
        # our footprint slightly the larger of the two, which is what actually
        # guarantees coverage.
        shape = R.SHAPE_SQUARE if op.fill == FILL_LOGO_SQUARE else R.SHAPE_ROUNDED
        premul, inv = cache.get(w, h, op.corner_radius, op.feather_px, shape)
        roi = frame[y:y + h, x:x + w]
        patch = ((premul + roi.astype(np.uint16) * inv) // 255).astype(np.uint8)
        if op.grade and op.cal is not None:
            patch = R.apply_grade(patch, frame, box, op.cal, rng)
        frame[y:y + h, x:x + w] = patch
        if op.shadow:
            R.soft_shadow(frame, box, op.corner_radius)
    return frame


def _audio_args(src: str, spec: ClipSpec, info: VideoInfo,
                segs: list[tuple[float, float]]) -> tuple[list[str], list[str]]:
    """(pre-output args, map/codec args) for the audio path."""
    if not info.has_audio:
        return [], ["-an"]
    if len(segs) == 1 and abs(segs[0][0]) < 1e-6 and \
            abs(segs[0][1] - info.duration) < 1e-6:
        # no cuts -> keep the original audio bit-exact
        return [], ["-map", "1:a", "-c:a", "copy"]

    parts, labels = [], []
    for i, (s, e) in enumerate(segs):
        dur = e - s
        fades = ""
        if i > 0:                                   # follows a cut -> fade in
            fades += f",afade=t=in:st=0:d={min(AUDIO_FADE, dur/2):.3f}"
        if i < len(segs) - 1 or e < info.duration - 1e-3:   # a cut follows
            st = max(0.0, dur - AUDIO_FADE)
            fades += f",afade=t=out:st={st:.3f}:d={min(AUDIO_FADE, dur/2):.3f}"
        parts.append(f"[1:a]atrim=start={s:.4f}:end={e:.4f},"
                     f"asetpts=PTS-STARTPTS{fades}[a{i}]")
        labels.append(f"[a{i}]")
    if len(segs) == 1:
        graph = parts[0].replace(f"[a0]", "[aout]")
    else:
        graph = ";".join(parts) + ";" + "".join(labels) + \
                f"concat=n={len(segs)}:v=0:a=1[aout]"
    return (["-filter_complex", graph],
            ["-map", "[aout]", "-c:a", "aac", "-b:a", "192k"])


def render_clip(spec: ClipSpec, src: str | Path, out: str | Path, *,
                logo: str, dry_run: bool = False,
                progress=None) -> dict:
    """Apply cuts + every swap and write `out`. Returns QA metrics."""
    src, out = str(src), Path(out)
    info = probe_video(src)
    cache = R.VariantCache(R.load_logo(logo))
    rng = np.random.default_rng(abs(hash(spec.video_id)) % (2**32))

    segs = kept_segments(spec, info.duration)
    drop = _cut_frames(spec, info)
    expected_frames = max(0, info.nb_frames - len(drop))

    # ---- plan ----
    static_ops: list[tuple[FrameOp, tuple[int, int]]] = []
    for s in spec.statics:
        static_ops.append(_resolve_static(s, src, info, cache))
    tracked_ops: dict[int, list[FrameOp]] = {}
    reports: dict[str, dict] = {}
    for k in spec.tracked:
        ops, rep = plan_tracked(k, src, info, cache)
        reports[k.id] = rep
        for n, op in ops.items():
            tracked_ops.setdefault(n, []).append(op)

    qa = {
        "video_id": spec.video_id,
        "src_duration": round(info.duration, 3),
        "expected_duration": spec.kept_duration(),
        "expected_frames": expected_frames,
        "dims": f"{info.width}x{info.height}",
        "fps": str(info.fps),
        "cuts": len(spec.cuts),
        "statics": len(spec.statics),
        "tracked": len(spec.tracked),
        "tracks": reports,
        "variants_cached": len(cache),
    }
    if dry_run:
        qa["verdict"] = "DRY-RUN"
        return qa

    # ---- encode ----
    out.parent.mkdir(parents=True, exist_ok=True)
    enc = spec.encode
    pre, amap = _audio_args(src, spec, info, segs)
    cmd = [
        "ffmpeg", "-y", "-v", "error", "-nostdin",
        "-f", "rawvideo", "-pix_fmt", "bgr24",
        "-s", f"{info.width}x{info.height}", "-r", str(info.fps), "-i", "-",
        "-i", src, *pre,
        "-map", "0:v", *amap,
        "-c:v", "libx264", "-preset", str(enc.get("preset", "veryfast")),
        "-crf", str(enc.get("crf", 12)), "-g", str(enc.get("gop", 30)),
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        str(out),
    ]
    log.info(f"{spec.video_id}: {info.width}x{info.height}@{info.fps} "
             f"{info.nb_frames}f -> {expected_frames}f "
             f"({len(spec.statics)} static, {len(spec.tracked)} tracked)")

    cap = cv2.VideoCapture(src)
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    written = decoded = 0
    try:
        n = 0
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            decoded += 1
            if n not in drop:
                ops = list(tracked_ops.get(n, ()))
                for op, (f0, f1) in static_ops:
                    if f0 <= n <= f1:
                        ops.append(op)
                if ops:
                    frame = composite_frame(frame, ops, cache, rng)
                proc.stdin.write(np.ascontiguousarray(frame).tobytes())
                written += 1
                if progress and written % 200 == 0:
                    progress(written, expected_frames)
            n += 1
    except BrokenPipeError:
        pass
    finally:
        cap.release()
        try:
            proc.stdin.close()
        except (BrokenPipeError, OSError):
            pass
        _, err = proc.communicate()

    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed ({proc.returncode}) on {spec.video_id}: "
                           f"{err.decode('utf-8', 'replace')[-800:]}")

    # ---- verify ----
    # The invariant is "every decoded frame survived unless it was cut", NOT
    # "the output matches ffprobe's nb_frames". Container metadata routinely
    # disagrees with what actually decodes -- clips 59 and 73 advertise 1082
    # frames and decode 1080 -- and treating that as a failure flags a correct
    # render. The metadata gap is reported instead, since a LARGE one means a
    # damaged source worth looking at.
    got = probe_video(out)
    dropped_seen = decoded - written
    tol = max(0.05, 2.0 / info.fps_f)
    qa |= {
        "written_frames": written,
        "decoded_frames": decoded,
        "meta_frames": info.nb_frames,
        "out_duration": round(got.duration, 3),
        "dur_delta": round(got.duration - spec.kept_duration(), 3),
        "timebase_delta": round(got.duration - written / info.fps_f, 3),
    }
    fails = []
    if written != decoded - dropped_seen:
        fails.append(f"frames lost: wrote {written} of {decoded - dropped_seen}")
    if abs(qa["timebase_delta"]) > tol:
        fails.append(f"timebase {qa['timebase_delta']:+.3f}s (tol {tol:.3f})")
    if abs(decoded - info.nb_frames) > max(3, 0.01 * info.nb_frames):
        fails.append(f"source metadata off: decoded {decoded} vs nb_frames "
                     f"{info.nb_frames}")
    if info.has_audio and not got.has_audio:
        fails.append("audio lost")
    qa["fails"] = ";".join(fails)
    qa["verdict"] = "FAIL" if fails else "PASS"
    log.info(f"{spec.video_id}: {qa['verdict']} {written}f "
             f"{got.duration:.2f}s (delta {qa['dur_delta']:+.3f}s)"
             + (f" -- {qa['fails']}" if fails else ""))
    return qa
