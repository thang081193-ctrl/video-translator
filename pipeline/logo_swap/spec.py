"""Per-clip logo-swap spec -- the sidecar JSON that drives cover-then-draw.

Geometry lives in NORMALIZED 0..1 coordinates, never pixels. Scraped ad batches
carry the same creative at several resolutions (e.g. 100/91/97 are one creative
at 404x720, 360x640@25 and 360x640@30), so normalized boxes let one authored
geometry serve every sibling. Pixel boxes would silently mis-place on the twins.

Specs are hand-authored (see `propose` for a pre-fill) and live in
`<src>/_logoswap/<video_id>.json`. `video_id` is `Path(mp4).stem`, the same id
space as manifest.video_id(), so the two can be joined without coupling: the
manifest's OPUS_FIELDS is a fixed tuple and has no geometry slot, and widening
it would touch the scan/merge path for no benefit.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from fractions import Fraction
from pathlib import Path

from pipeline.logger import get_logger

log = get_logger("LogoSwap")

SCHEMA_VERSION = 1

# fill modes for a covered region
FILL_LOGO_ROUNDED = "logo_rounded"   # squircle-masked brand logo (app-icon look)
FILL_LOGO_SQUARE = "logo_square"     # full-bleed, for sources that show a square tile
FILL_CLEAN_PLATE = "clean_plate"     # cover only -- no mark drawn back
FILL_BLUR = "blur"                   # last resort when no clean plate exists
FILLS = (FILL_LOGO_ROUNDED, FILL_LOGO_SQUARE, FILL_CLEAN_PLATE, FILL_BLUR)


@dataclass(frozen=True)
class Box:
    """A rectangle in normalized 0..1 coordinates (x, y = top-left)."""

    x: float
    y: float
    w: float
    h: float

    def to_px(self, width: int, height: int) -> tuple[int, int, int, int]:
        """Snap to integer pixels, clamped inside the frame.

        Sub-pixel placement is deliberately not offered: resampling the logo
        every frame makes it shimmer against an otherwise static plate.
        """
        x = max(0, min(width - 1, round(self.x * width)))
        y = max(0, min(height - 1, round(self.y * height)))
        w = max(1, min(width - x, round(self.w * width)))
        h = max(1, min(height - y, round(self.h * height)))
        return x, y, w, h

    def pad(self, frac: float) -> "Box":
        """Grow by `frac` of the box's own size on every side, clamped to frame.

        The cover must be a superset of the competitor mark -- a surviving 1px
        ring of their tile colour is exactly what the eye catches on a static
        shot -- so callers pad before deriving the mask.
        """
        dx, dy = self.w * frac, self.h * frac
        x, y = max(0.0, self.x - dx), max(0.0, self.y - dy)
        return Box(x, y, min(1.0 - x, self.w + 2 * dx), min(1.0 - y, self.h + 2 * dy))

    def center(self) -> tuple[float, float]:
        return self.x + self.w / 2, self.y + self.h / 2

    @classmethod
    def from_px(cls, x: float, y: float, w: float, h: float,
                width: int, height: int) -> "Box":
        return cls(x / width, y / height, w / width, h / height)

    def as_list(self) -> list[float]:
        return [round(v, 6) for v in (self.x, self.y, self.w, self.h)]


@dataclass(frozen=True)
class SearchCfg:
    """Tracker search + gate knobs. Defaults are the measured-good values."""

    scale_lo: float = 0.75
    scale_hi: float = 1.30
    scale_step: float = 0.03
    # ROI search halves the cost from ~430ms/frame (full-frame, 19 scales) to
    # ~9ms/frame. Full-frame acquire runs only at seed_t and on re-acquisition.
    roi_pad_frac: float = 0.06
    accept: float = 0.72     # >= this counts as a hit (observed medians 0.95-1.00)
    drop: float = 0.55       # < this counts as a miss
    hold_frames: int = 6     # consecutive misses before turning OFF (~0.2s)
    min_run_frames: int = 8  # delete shorter ON/OFF runs -- a 0.3s flash reads worse
    # A scene change means the mark is genuinely ABSENT (endcard / store page)
    # and must turn OFF at once; a low score without one means OCCLUDED (a hand
    # swept over the icon) and must hold. The match score alone cannot tell
    # these apart, and they need opposite answers.
    scene_diff: float = 25.0

    def scales(self) -> list[float]:
        out, s = [], self.scale_lo
        while s <= self.scale_hi + 1e-9:
            out.append(round(s, 4))
            s += self.scale_step
        return out


@dataclass(frozen=True)
class CutSpec:
    """A time range to remove. `end=None` means "to EOF"."""

    kind: str            # endcard | storepage | shot
    start: float
    end: float | None = None


@dataclass(frozen=True)
class StaticSpec:
    """A fixed-position overlay: corner logo card, name pill, store badge."""

    id: str
    box: Box
    t: tuple[float, float]
    fill: str = FILL_LOGO_ROUNDED
    kind: str = "logo"           # logo | name_pill | badge
    corner_radius: float = 0.22  # fraction of box size; Android/iOS icon convention
    plate_from: float | None = None  # sample the clean plate at this timestamp
    feather_px: float = 2.0
    pad: float = 0.06
    grade: bool = True
    # A frame the mark is definitely on screen. The renderer fingerprints the
    # box here and skips frames that no longer match, which lets `t` be a loose
    # bracket instead of an exact window -- necessary for cards that slide in
    # and out, where "exact" clips the fade and leaves the card showing.
    ref_t: float | None = None


@dataclass(frozen=True)
class TrackedSpec:
    """The on-phone app icon: moves and scales with the actor's hand.

    `seed_box` and `cover_box` are deliberately separate. The tracker wants a
    SMALL, high-contrast patch to match on -- the inner glyph is ideal. The
    cover has to be the WHOLE competitor mark including its tile and shadow, or
    a dark rim survives underneath the replacement. Measured on clip 100: the
    inner circle is 45x44 while the dark tile behind it is 68x71.

    `cover_box` is authored in the same frame as `seed_box`; the offset and
    size ratio between them are held fixed and scaled with the track.
    """

    id: str
    seed_t: float
    seed_box: Box
    t: tuple[float, float]
    cover_box: Box | None = None     # None -> cover exactly the seed box
    search: SearchCfg = field(default_factory=SearchCfg)
    fill: str = FILL_LOGO_ROUNDED
    corner_radius: float = 0.22
    pad: float = 0.04                # small safety margin on top of cover_box
    feather_px: float = 2.0
    grade: bool = True


@dataclass
class ClipSpec:
    video_id: str
    authored_dims: tuple[int, int]
    fps: Fraction
    duration: float
    creative_group: str = ""
    cuts: list[CutSpec] = field(default_factory=list)
    statics: list[StaticSpec] = field(default_factory=list)
    tracked: list[TrackedSpec] = field(default_factory=list)
    encode: dict = field(default_factory=lambda: {"crf": 12, "preset": "veryfast", "gop": 30})
    reviewed_by: str = ""
    reviewed_at: str = ""

    def kept_duration(self) -> float:
        """Output duration after cuts."""
        dropped = 0.0
        for c in self.cuts:
            end = self.duration if c.end is None else min(c.end, self.duration)
            dropped += max(0.0, end - c.start)
        return round(max(0.0, self.duration - dropped), 3)

    def is_cut(self, t: float) -> bool:
        for c in self.cuts:
            end = self.duration if c.end is None else c.end
            if c.start <= t < end:
                return True
        return False


# ── serialization ────────────────────────────────────────────────────────────

def _box(v) -> Box:
    return Box(*[float(x) for x in v])


def spec_path(src_root: str | Path, video_id: str) -> Path:
    return Path(src_root) / "_logoswap" / f"{video_id}.json"


def load_spec(path: str | Path) -> ClipSpec:
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    ver = d.get("schema", 1)
    if ver != SCHEMA_VERSION:
        raise ValueError(f"{path}: schema {ver}, expected {SCHEMA_VERSION}")
    return ClipSpec(
        video_id=d["video_id"],
        authored_dims=tuple(d["authored_dims"]),          # type: ignore[arg-type]
        fps=Fraction(d["fps"]),
        duration=float(d["duration"]),
        creative_group=d.get("creative_group", ""),
        cuts=[CutSpec(c["kind"], float(c["start"]),
                      None if c.get("end") is None else float(c["end"]))
              for c in d.get("cuts", [])],
        statics=[StaticSpec(
            id=s["id"], box=_box(s["box"]), t=(float(s["t"][0]), float(s["t"][1])),
            fill=s.get("fill", FILL_LOGO_ROUNDED), kind=s.get("kind", "logo"),
            corner_radius=float(s.get("corner_radius", 0.22)),
            plate_from=(None if s.get("plate_from") is None else float(s["plate_from"])),
            feather_px=float(s.get("feather_px", 2.0)),
            pad=float(s.get("pad", 0.06)), grade=bool(s.get("grade", True)),
            ref_t=(None if s.get("ref_t") is None else float(s["ref_t"])),
        ) for s in d.get("statics", [])],
        tracked=[TrackedSpec(
            id=k["id"], seed_t=float(k["seed_t"]), seed_box=_box(k["seed_box"]),
            t=(float(k["t"][0]), float(k["t"][1])),
            cover_box=(None if k.get("cover_box") is None else _box(k["cover_box"])),
            search=SearchCfg(**k.get("search", {})),
            fill=k.get("fill", FILL_LOGO_ROUNDED),
            corner_radius=float(k.get("corner_radius", 0.22)),
            pad=float(k.get("pad", 0.04)),
            feather_px=float(k.get("feather_px", 2.0)),
            grade=bool(k.get("grade", True)),
        ) for k in d.get("tracked", [])],
        encode=d.get("encode", {"crf": 12, "preset": "veryfast", "gop": 30}),
        reviewed_by=d.get("reviewed_by", ""), reviewed_at=d.get("reviewed_at", ""),
    )


def save_spec(spec: ClipSpec, path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    d = {
        "schema": SCHEMA_VERSION,
        "video_id": spec.video_id,
        "creative_group": spec.creative_group,
        "authored_dims": list(spec.authored_dims),
        "fps": str(spec.fps),
        "duration": spec.duration,
        "reviewed_by": spec.reviewed_by,
        "reviewed_at": spec.reviewed_at,
        "cuts": [{"kind": c.kind, "start": c.start, "end": c.end} for c in spec.cuts],
        "statics": [{**{k: v for k, v in asdict(s).items() if k != "box"},
                     "box": s.box.as_list(), "t": list(s.t)} for s in spec.statics],
        "tracked": [{**{k: v for k, v in asdict(k_).items()
                        if k not in ("seed_box", "cover_box", "search")},
                     "seed_box": k_.seed_box.as_list(), "t": list(k_.t),
                     "cover_box": (None if k_.cover_box is None
                                   else k_.cover_box.as_list()),
                     "search": asdict(k_.search)} for k_ in spec.tracked],
        "encode": spec.encode,
    }
    # atomic -- a half-written spec would be loaded by a concurrent render
    tmp = p.with_suffix(f".tmp{id(spec) & 0xffff}")
    tmp.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)
    return p


def validate_spec(spec: ClipSpec, width: int, height: int,
                  duration: float) -> list[str]:
    """Return human-readable problems; empty list means the spec is usable."""
    errs: list[str] = []

    aw, ah = spec.authored_dims
    if (aw, ah) != (width, height):
        # Not fatal -- normalized geometry is the whole point -- but the aspect
        # must match or the boxes are stretched relative to what was authored.
        if abs(aw / ah - width / height) > 0.01:
            errs.append(
                f"authored aspect {aw}x{ah} ({aw/ah:.4f}) != actual {width}x{height} "
                f"({width/height:.4f}) -- boxes will be distorted"
            )
    if abs(spec.duration - duration) > 0.15:
        errs.append(f"spec duration {spec.duration:.2f} != actual {duration:.2f}")

    ids = [t.id for t in spec.tracked] + [s.id for s in spec.statics]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        errs.append(f"duplicate target ids: {sorted(dupes)}")

    for c in spec.cuts:
        end = duration if c.end is None else c.end
        if not (0 <= c.start < end <= duration + 0.05):
            errs.append(f"cut {c.kind} [{c.start}, {c.end}] outside [0, {duration:.2f}]")

    for s in spec.statics:
        errs += _check_box(f"static {s.id}", s.box, s.t, s.fill, duration)
        if s.plate_from is not None and spec.is_cut(s.plate_from):
            errs.append(f"static {s.id}: plate_from={s.plate_from} is inside a cut")
    for k in spec.tracked:
        errs += _check_box(f"tracked {k.id}", k.seed_box, k.t, k.fill, duration)
        if not (k.t[0] <= k.seed_t <= k.t[1]):
            errs.append(f"tracked {k.id}: seed_t={k.seed_t} outside t={k.t}")
        if spec.is_cut(k.seed_t):
            errs.append(f"tracked {k.id}: seed_t={k.seed_t} is inside a cut")
        _, _, bw, bh = k.seed_box.to_px(width, height)
        if bw < 12 or bh < 12:
            errs.append(f"tracked {k.id}: template {bw}x{bh}px too small to match")
    return errs


def _check_box(label: str, box: Box, t: tuple[float, float],
               fill: str, duration: float) -> list[str]:
    errs = []
    if fill not in FILLS:
        errs.append(f"{label}: unknown fill {fill!r} (want one of {FILLS})")
    for name, v in (("x", box.x), ("y", box.y), ("w", box.w), ("h", box.h)):
        if not (0.0 <= v <= 1.0):
            errs.append(f"{label}: box.{name}={v} not normalized to 0..1")
    if box.x + box.w > 1.001 or box.y + box.h > 1.001:
        errs.append(f"{label}: box extends past the frame edge")
    if not (0 <= t[0] < t[1] <= duration + 0.05):
        errs.append(f"{label}: t={t} outside [0, {duration:.2f}]")
    return errs
