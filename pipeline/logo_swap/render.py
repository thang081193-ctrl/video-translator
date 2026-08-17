"""Build the patch that replaces a competitor mark: cover first, then draw.

Two findings drive this module, both measured on this batch:

1. The brand logo PNG is a hard-cornered, full-bleed square with alpha 254..255.
   Every rounded corner ever seen on it was applied by the Play Store. Pasting
   it as-is over a squircle app icon leaves square corners, which is the single
   most obvious tell. The mask has to be synthesized here.

2. A naive alpha paste does NOT cover the old mark -- the competitor's white
   handset survives around the edges and reads as a double image. So every draw
   goes onto a CLEAN PLATE first. Inpainting is explicitly not used: the
   prior attempt in this repo concluded ghosting always looks worse than an
   opaque cover (see meta-ads-prepare/HANDOFF-text-reburn.md).

Sharpness matters more than it looks. Rendered at 1080 the logo measures
Laplacian variance ~379 while these upscaled 360p plates measure ~8-9 -- 40x
sharper than the video it sits on, which manufactures the "sticker" look. That
is why compositing happens at SOURCE resolution and why grade_to_plate matches
noise and sharpness, not just luma.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from pipeline.logger import get_logger

log = get_logger("LogoSwap")

SHAPE_ROUNDED = "rounded"
SHAPE_SQUARE = "square"
SHAPE_CIRCLE = "circle"


def load_logo(path: str) -> np.ndarray:
    """Load the brand logo as BGRA uint8."""
    img = cv2.imread(path, cv2.IMREAD_UNCHANGED)
    if img is None:
        raise FileNotFoundError(f"cannot read logo: {path}")
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGRA)
    elif img.shape[2] == 3:
        img = cv2.cvtColor(img, cv2.COLOR_BGR2BGRA)
    return img


def rounded_mask(w: int, h: int, radius_frac: float, feather_px: float,
                 shape: str = SHAPE_ROUNDED) -> np.ndarray:
    """A uint8 alpha mask for the container shape.

    `radius_frac` is a fraction of the SHORTER side, so a non-square box keeps
    circular corners instead of elliptical ones.
    """
    m = np.zeros((h, w), np.uint8)
    if shape == SHAPE_SQUARE:
        m[:] = 255
    elif shape == SHAPE_CIRCLE:
        cv2.ellipse(m, (w // 2, h // 2), (max(1, w // 2), max(1, h // 2)),
                    0, 0, 360, 255, -1)
    else:
        r = max(1, int(round(min(w, h) * radius_frac)))
        r = min(r, w // 2, h // 2)
        cv2.rectangle(m, (r, 0), (w - r, h), 255, -1)
        cv2.rectangle(m, (0, r), (w, h - r), 255, -1)
        for cx, cy in ((r, r), (w - r, r), (r, h - r), (w - r, h - r)):
            cv2.circle(m, (cx, cy), r, 255, -1)
    if feather_px > 0:
        # a hard alpha edge survives x264 as visible ringing
        m = cv2.GaussianBlur(m, (0, 0), float(feather_px))
    return m


class VariantCache:
    """Cache premultiplied logo variants per integer size.

    A tracked mark scales over a narrow band (0.94..1.03 measured), so a
    130px template resolves to ~13 distinct integer sizes across a whole clip.
    Caching makes the per-frame resize cost vanish.
    """

    def __init__(self, logo: np.ndarray):
        self._logo = logo
        self._cache: dict[tuple, tuple[np.ndarray, np.ndarray]] = {}

    def get(self, w: int, h: int, radius_frac: float, feather_px: float,
            shape: str = SHAPE_ROUNDED) -> tuple[np.ndarray, np.ndarray]:
        """Return (premultiplied_bgr_u16, inv_alpha_u16) sized (h, w).

        Composite with:  dst = (premul + dst * inv_alpha) // 255
        """
        key = (w, h, round(radius_frac, 4), round(feather_px, 2), shape)
        hit = self._cache.get(key)
        if hit is not None:
            return hit
        rgb = cv2.resize(self._logo[:, :, :3], (w, h), interpolation=cv2.INTER_AREA)
        a = rounded_mask(w, h, radius_frac, feather_px, shape)
        # the asset's own alpha is effectively opaque, but honour it if it is not
        if self._logo.shape[2] == 4:
            own = cv2.resize(self._logo[:, :, 3], (w, h), interpolation=cv2.INTER_AREA)
            a = cv2.min(a, own)
        a16 = a.astype(np.uint16)
        premul = rgb.astype(np.uint16) * a16[:, :, None]      # max 255*255, fits u16
        inv = (255 - a16)[:, :, None]
        out = (premul, inv)
        self._cache[key] = out
        return out

    def __len__(self) -> int:
        return len(self._cache)


def alpha_paste(dst: np.ndarray, premul: np.ndarray, inv_alpha: np.ndarray,
                x: int, y: int) -> None:
    """In-place premultiplied composite. Clips at frame edges."""
    h, w = premul.shape[:2]
    H, W = dst.shape[:2]
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(W, x + w), min(H, y + h)
    if x1 <= x0 or y1 <= y0:
        return
    sx, sy = x0 - x, y0 - y
    p = premul[sy:sy + (y1 - y0), sx:sx + (x1 - x0)]
    iv = inv_alpha[sy:sy + (y1 - y0), sx:sx + (x1 - x0)]
    roi = dst[y0:y1, x0:x1]
    dst[y0:y1, x0:x1] = ((p + roi.astype(np.uint16) * iv) // 255).astype(np.uint8)


# ── clean plate ──────────────────────────────────────────────────────────────

def ring_pixels(frame: np.ndarray, box: tuple[int, int, int, int],
                width: int = 3) -> np.ndarray:
    """Pixels in a `width`-px band just outside `box`. Shape (N, 3)."""
    x, y, w, h = box
    H, W = frame.shape[:2]
    ox0, oy0 = max(0, x - width), max(0, y - width)
    ox1, oy1 = min(W, x + w + width), min(H, y + h + width)
    outer = frame[oy0:oy1, ox0:ox1]
    mask = np.ones(outer.shape[:2], bool)
    ix0, iy0 = x - ox0, y - oy0
    mask[max(0, iy0):iy0 + h, max(0, ix0):ix0 + w] = False
    px = outer[mask]
    return px.reshape(-1, 3) if px.size else np.zeros((0, 3), np.uint8)


def flat_plate(frame: np.ndarray, box: tuple[int, int, int, int]) -> np.ndarray:
    """Fill `box` with the median colour of the ring around it.

    Good enough whenever the mark sits on a near-flat surface, which is the
    common case here: the app icon sits on a solid phone screen.
    """
    x, y, w, h = box
    ring = ring_pixels(frame, box)
    colour = (np.median(ring, axis=0) if len(ring)
              else np.array([0, 0, 0], np.float64))
    return np.full((h, w, 3), colour, np.uint8)


def inner_plate(frame: np.ndarray, box: tuple[int, int, int, int],
                uniform_std: float = 18.0) -> np.ndarray:
    """Fill `box` with the colour that should be left behind after a wipe.

    Two cases, told apart by how varied the box is:

    * **Content on a background** (text on a name card). The background is the
      majority of the area, so the box's own median IS the card colour, and
      unlike the surrounding ring it cannot pick up whatever lies outside the
      card. Sampling the ring put a grey bar across clip 1's white name pill
      and a mauve one across clip 13's, because the ring straddled the pill
      edge and caught the street behind it. Polarity does not matter -- dark
      text on light or the reverse, the text is the minority either way.

    * **A solid mark** (a filled badge). Here the box's median is the mark
      itself, which would repaint what we are trying to remove, so the ring is
      the only source of a sane colour.
    """
    x, y, w, h = box
    patch = frame[y:y + h, x:x + w]
    if patch.size == 0:
        return flat_plate(frame, box)
    if float(patch.reshape(-1, 3).std(axis=0).mean()) < uniform_std:
        return flat_plate(frame, box)          # uniform -> the box IS the mark
    colour = np.median(patch.reshape(-1, 3), axis=0)
    return np.full((h, w, 3), colour, np.uint8)


def sampled_plate(video: str, t: float, fps: float,
                  box: tuple[int, int, int, int]) -> np.ndarray | None:
    """Copy the real background from a frame where the mark is absent."""
    cap = cv2.VideoCapture(video)
    try:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(round(t * fps)))
        ok, frame = cap.read()
        if not ok:
            return None
        x, y, w, h = box
        patch = frame[y:y + h, x:x + w]
        return patch.copy() if patch.shape[:2] == (h, w) else None
    finally:
        cap.release()


def blur_plate(frame: np.ndarray, box: tuple[int, int, int, int],
               strength: int = 12) -> np.ndarray:
    """Heavy blur of the region itself -- last resort, visibly a redaction."""
    x, y, w, h = box
    patch = frame[y:y + h, x:x + w]
    k = max(3, (min(w, h) // strength) * 2 + 1)
    return cv2.GaussianBlur(patch, (k, k), 0)


# ── grading the patch into the plate ─────────────────────────────────────────

def _lap_var(bgr: np.ndarray) -> float:
    g = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(g, cv2.CV_64F).var())


def grade_to_plate(patch: np.ndarray, frame: np.ndarray,
                   box: tuple[int, int, int, int], *,
                   luma_clamp: float = 0.12,
                   match_noise: bool = True,
                   match_sharpness: bool = True,
                   rng: np.random.Generator | None = None) -> np.ndarray:
    """Make `patch` sit in the plate instead of on top of it.

    Order matters: luma, then sharpness, then noise. Blurring after adding
    noise would erase the noise we just matched.
    """
    ring = ring_pixels(frame, box, width=4)
    if len(ring) < 12:
        return patch
    out = patch.astype(np.float32)

    # 1. luma -- clamped so a dark logo on a bright screen is not washed out
    ring_y = float(cv2.cvtColor(ring.reshape(-1, 1, 3), cv2.COLOR_BGR2GRAY).mean())
    patch_y = float(cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY).mean())
    if patch_y > 1.0:
        f = float(np.clip(ring_y / patch_y, 1 - luma_clamp, 1 + luma_clamp))
        out *= f

    # 2. sharpness -- a source-res logo still reads ~2.5x sharper than these
    #    upscaled plates; pull it to within 3x of the surrounding detail
    if match_sharpness:
        target = _lap_var(frame[box[1]:box[1] + box[3], box[0]:box[0] + box[2]])
        cur = _lap_var(np.clip(out, 0, 255).astype(np.uint8))
        if target > 0.5 and cur > 3.0 * target:
            for sigma in (0.6, 0.8, 1.0, 1.3):
                cand = cv2.GaussianBlur(out, (0, 0), sigma)
                if _lap_var(np.clip(cand, 0, 255).astype(np.uint8)) <= 3.0 * target:
                    out = cand
                    break
            else:
                out = cv2.GaussianBlur(out, (0, 0), 1.3)

    # 3. noise -- a noiseless patch inside a 580kbps scrape is instantly visible
    if match_noise:
        sigma = float(ring.reshape(-1, 3).std(axis=0).mean())
        sigma = float(np.clip(sigma * 0.5, 0.0, 6.0))
        if sigma > 0.8:
            rng = rng or np.random.default_rng(0)
            out += rng.normal(0.0, sigma, out.shape).astype(np.float32)

    return np.clip(out, 0, 255).astype(np.uint8)


@dataclass(frozen=True)
class GradeCal:
    """Per-target grade constants, measured once instead of once per frame.

    Sharpness and grain are properties of the SOURCE encode, so they are stable
    for a whole clip. Only luma has to be re-matched per frame, because the
    background behind a tracked mark changes as the actor moves.
    """

    blur_sigma: float
    noise_sigma: float
    luma_clamp: float = 0.12


def calibrate_grade(frame: np.ndarray, box: tuple[int, int, int, int],
                    probe_patch: np.ndarray, *,
                    luma_clamp: float = 0.12) -> GradeCal:
    """Measure how much to soften and how much grain to add, once per target.

    Every reference is taken from the region BEING REPLACED, never from the ring
    around it. The mark we cover is another app icon, so its luma and grain are
    the right comparison. Grading against the ring is actively wrong here: the
    ring is the white phone screen, and matching to it washes a saturated blue
    logo out to pale cyan (observed on clip 100 before this fix).
    """
    x, y, w, h = box
    orig = frame[y:y + h, x:x + w]
    noise = 0.0
    if orig.size:
        # high-pass the original so its own colour structure is not read as grain
        hp = orig.astype(np.float32) - cv2.GaussianBlur(orig.astype(np.float32), (0, 0), 1.5)
        noise = float(np.clip(hp.std() * 0.6, 0.0, 5.0))

    target = _lap_var(orig) if orig.size else 0.0
    blur = 0.0
    if target > 0.5:
        cur = _lap_var(probe_patch)
        if cur > 3.0 * target:
            for sigma in (0.6, 0.8, 1.0, 1.3):
                if _lap_var(cv2.GaussianBlur(probe_patch, (0, 0), sigma)) <= 3.0 * target:
                    blur = sigma
                    break
            else:
                blur = 1.3
    log.debug(f"grade cal box={box} blur={blur:.2f} noise={noise:.2f} "
              f"lap_target={target:.1f}")
    return GradeCal(blur_sigma=blur, noise_sigma=noise, luma_clamp=luma_clamp)


def apply_grade(patch: np.ndarray, frame: np.ndarray,
                box: tuple[int, int, int, int], cal: GradeCal,
                rng: np.random.Generator) -> np.ndarray:
    """Cheap per-frame grade using pre-measured constants.

    `frame` must still hold the ORIGINAL pixels at `box` -- compose.py calls this
    before writing the patch back, so the mark being replaced is the luma
    reference. The clamp keeps this from ever becoming a big shift; it only
    transfers scene exposure (a dim room, a blown-out window).
    """
    x, y, w, h = box
    orig = frame[y:y + h, x:x + w]
    out = patch.astype(np.float32)
    if orig.size:
        ref_y = float(cv2.cvtColor(orig, cv2.COLOR_BGR2GRAY).mean())
        patch_y = float(cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY).mean())
        if patch_y > 1.0 and ref_y > 1.0:
            out *= float(np.clip(ref_y / patch_y,
                                 1 - cal.luma_clamp, 1 + cal.luma_clamp))
    if cal.blur_sigma > 0:
        out = cv2.GaussianBlur(out, (0, 0), cal.blur_sigma)
    if cal.noise_sigma > 0.8:
        out += rng.normal(0.0, cal.noise_sigma, out.shape).astype(np.float32)
    return np.clip(out, 0, 255).astype(np.uint8)


def soft_shadow(dst: np.ndarray, box: tuple[int, int, int, int],
                radius_frac: float, opacity: float = 0.12,
                blur: float = 2.0, dy: int = 1) -> None:
    """Re-draw a drop shadow under a covered app-icon tile, in place.

    Needed when the source tile had one: covering it flat makes the replacement
    read as a layer floating above the page.
    """
    x, y, w, h = box
    H, W = dst.shape[:2]
    m = rounded_mask(w, h, radius_frac, blur)
    y0, y1 = max(0, y + dy), min(H, y + dy + h)
    x0, x1 = max(0, x), min(W, x + w)
    if y1 <= y0 or x1 <= x0:
        return
    sub = m[:y1 - y0, :x1 - x0].astype(np.float32) * (opacity / 255.0)
    roi = dst[y0:y1, x0:x1].astype(np.float32)
    dst[y0:y1, x0:x1] = (roi * (1.0 - sub[:, :, None])).astype(np.uint8)
