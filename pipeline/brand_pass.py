"""Brand-pass video transformer — Reels upscale + Andromeda dedup evasion.

Applies V4c-equivalent transforms to any source video, producing a Reels-ready
1080x1920 mp4 with PER-RUN JITTERED params (different fingerprint each call):
- TTS re-dub via Edge TTS: voice rotated from 7-male-English pool, rate ±3%
- Demucs htdemucs BGM separation, mixed under TTS (gain 0.35-0.45)
- Zoom 1.03-1.06x + crop with ±15px offset (alters spatial fingerprint)
- Color LUT: saturation 1.12-1.18, contrast 1.07-1.13, gamma 0.93-0.97, hue 5-11°
- Corner watermark in Reels safe zone: opacity 0.55-0.65, position jittered ±30/±15px
  (text OR PNG logo overlay)
- Optional end-card auto-trim (cuts competitor app-card from source)
- Outro card 1.3-1.7s at end (4 dark grey bg variants + brand text + optional logo)
- Encoding: CRF 19-21, preset rotated, metadata stripped + fake creation_time

All ranges are SAFE — viewer won't notice degradation. Mirror/flip, aggressive
noise, vignette, speed change, large hue shift, FPS change are EXCLUDED because
they degrade quality or break creative text overlays.

Expected Andromeda dedup score vs source: ~0.40 (passes 0.5 threshold), with
per-run hash diversity so 100 runs of same input → 100 unique fingerprints.

Usage:
    from pipeline.brand_pass import brand_pass_video
    brand_pass_video(
        input_path="EN_1205001.mp4",
        output_path="EN_1205001_branded.mp4",
        watermark_image="/path/Logo.png",     # PNG overlay (preferred over text)
        outro_logo_image="/path/Logo.png",    # same logo on outro card
        outro_title="Artify Gen",
        outro_subtitle="AI Photo Studio",
        trim_endcard=True,                    # auto-cut competitor end-card
        random_seed=None,                     # None=random per run, int=deterministic
    )
"""
from __future__ import annotations

import asyncio
import datetime
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile

from pipeline.audio import extract_audio
from pipeline.dub.separator import separate_audio
from pipeline.logger import get_logger
from pipeline.transcribe import transcribe

log = get_logger("BrandPass")

# Reels safe-zone constants
W, H = 1080, 1920
TOP_SAFE = int(H * 0.14)   # 268
SIDE_SAFE = int(W * 0.06)  # 64

# Default brand-pass parameters (used when no jitter)
DEFAULT_WATERMARK = "DecoAI"
DEFAULT_OUTRO_TITLE = "DecoAI"
DEFAULT_OUTRO_SUB = "Free AI Home Design"

# Safe jitter ranges — viewer will not perceive any quality drop.
SAFE_JITTER = {
    "zoom":         (1.03, 1.06),
    "crop_dx":      (-15, 15),
    "crop_dy":      (-15, 15),
    "saturation":   (1.12, 1.18),
    "contrast":     (1.07, 1.13),
    "gamma":        (0.93, 0.97),
    "hue":          (5.0, 11.0),
    "wm_opacity":   (0.55, 0.65),
    "wm_dx":        (-30, 30),
    "wm_dy":        (-15, 15),
    "bgm_vol":      (0.65, 0.80),
    "tts_rate_pct": (-3, 3),
    "outro_dur":    (2.0, 2.5),
    "outro_bg":     ["0x7B2FBE", "0x6020A8", "0x8B40CF", "0x4A90D9", "0x3070C0", "0x5518A0"],
    "outro_sub_color": ["0xFFFFFF", "0xF0F0FF", "0xE8E8FF", "0xDDE8FF"],
    "crf":          [19, 20, 21],
    "preset":       ["fast", "medium"],
}

# Male English neural voices, similar tone/pace.
TTS_VOICE_POOL_MALE = [
    "en-AU-WilliamNeural",
    "en-US-GuyNeural",
    "en-US-AndrewNeural",
    "en-US-EricNeural",
    "en-US-RogerNeural",
    "en-GB-ThomasNeural",
    "en-CA-LiamNeural",
]
DEFAULT_VOICE = TTS_VOICE_POOL_MALE[0]
DEFAULT_OUTRO_DUR = 1.5
DEFAULT_BGM_VOL = 0.4

# Find system font
_FONT = None
for _f in [r"C:\Windows\Fonts\seguisb.ttf", r"C:\Windows\Fonts\segoeui.ttf",
           r"C:\Windows\Fonts\arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]:
    if os.path.exists(_f):
        _FONT = _f
        break
if not _FONT:
    raise RuntimeError("No system font found for drawtext filter")
FONT_FF = _FONT.replace("\\", "/").replace(":", "\\:")


def _hex_to_rgb(hex_str: str) -> tuple:
    h = hex_str.lstrip("0x").lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _generate_outro_frame(
    canvas_w: int, canvas_h: int,
    title: str, subtitle: str,
    logo_path: str | None, logo_size: int,
    accent_hex: str, rng: "random.Random",
) -> "PIL.Image.Image":
    """Render a clean 2026-style outro card.

    Minimal layout: dark gradient bg → large rounded-square app icon →
    bold app name → subtitle → 'Download Now' CTA.
    No decorations, no phone mockup — clarity over complexity.
    """
    from PIL import Image, ImageDraw, ImageFont

    ar, ag, ab = _hex_to_rgb(accent_hex)

    # ── Background: soft pastel pink-lavender gradient (baby/chibi theme) ───
    img  = Image.new("RGBA", (canvas_w, canvas_h), (255, 245, 250, 255))
    draw = ImageDraw.Draw(img, "RGBA")
    # top: warm cream-white → bottom: soft lavender-pink
    top_c = (255, 248, 252)
    bot_c = (245, 228, 252)
    for y in range(canvas_h):
        t = y / canvas_h
        draw.line([(0, y), (canvas_w, y)], fill=(
            int(top_c[0] + (bot_c[0]-top_c[0])*t),
            int(top_c[1] + (bot_c[1]-top_c[1])*t),
            int(top_c[2] + (bot_c[2]-top_c[2])*t), 255))

    # Theme colors (baby/chibi — warm & soft)
    PINK      = (255,  90, 140)   # hot pink CTA
    PINK_D    = (230,  55, 110)   # deeper pink (shadow/divider)
    LAVENDER  = (180, 130, 220)   # soft purple accent
    TEXT_D    = ( 60,  30,  80)   # dark plum text (readable on light bg)
    TEXT_M    = (140,  90, 170)   # mid lavender subtitle
    WHITE     = (255, 255, 255)

    # ── Fonts ────────────────────────────────────────────────────────────────
    font_bold = font_med = font_cta = font_sm = None
    for fb, fr in [
        (r"C:\Windows\Fonts\seguisb.ttf", r"C:\Windows\Fonts\segoeui.ttf"),
        (r"C:\Windows\Fonts\arialbd.ttf",  r"C:\Windows\Fonts\arial.ttf"),
    ]:
        if os.path.exists(fb):
            font_bold = ImageFont.truetype(fb, 110)
            font_med  = ImageFont.truetype(fr if os.path.exists(fr) else fb, 66)
            font_cta  = ImageFont.truetype(fb, 76)
            font_sm   = ImageFont.truetype(fr if os.path.exists(fr) else fb, 42)
            break

    def draw_centered(text, fnt, y, fill, shadow_fill=None):
        if not fnt:
            return 0
        bb = fnt.getbbox(text)
        tw, th = bb[2]-bb[0], bb[3]-bb[1]
        tx = (canvas_w - tw) // 2 - bb[0]
        if shadow_fill:
            draw.text((tx+2, y+2), text, font=fnt, fill=shadow_fill)
        draw.text((tx, y), text, font=fnt, fill=fill)
        return th

    # ── Scattered baby decorations ───────────────────────────────────────────
    import math
    decorations = [
        # (x, y, type, size, alpha)
        (120, 220,  "heart",  28, 180),
        (940, 180,  "star",   22, 160),
        (80,  550,  "star",   18, 140),
        (980, 480,  "heart",  24, 170),
        (160, 900,  "dot",    16, 120),
        (930, 860,  "star",   20, 150),
        (100, 1280, "heart",  22, 140),
        (960, 1220, "dot",    18, 130),
        (140, 1580, "star",   24, 160),
        (930, 1540, "heart",  20, 150),
        (200, 1800, "dot",    14, 100),
        (870, 1780, "star",   16, 120),
    ]
    for (dx, dy, dtype, dsz, dalpha) in decorations:
        dx += rng.randint(-20, 20)
        dy += rng.randint(-20, 20)
        col = (*PINK, dalpha) if dtype == "heart" else (*LAVENDER, dalpha)
        if dtype == "dot":
            draw.ellipse([dx-dsz//2, dy-dsz//2, dx+dsz//2, dy+dsz//2], fill=col)
        elif dtype == "star":
            # 4-point star using two rotated rectangles
            for angle in [0, 45]:
                rad = math.radians(angle)
                pts = []
                for a in [0, 90, 180, 270]:
                    r2 = math.radians(a + angle)
                    r_outer = dsz // 2
                    r_inner = dsz // 5
                    pts.append((dx + r_outer * math.cos(r2), dy + r_outer * math.sin(r2)))
                    r2b = math.radians(a + 45 + angle)
                    pts.append((dx + r_inner * math.cos(r2b), dy + r_inner * math.sin(r2b)))
                draw.polygon(pts, fill=col)
        else:  # heart — two overlapping circles + triangle
            hw = dsz
            draw.ellipse([dx-hw, dy-hw//2, dx, dy+hw//2], fill=col)
            draw.ellipse([dx, dy-hw//2, dx+hw, dy+hw//2], fill=col)
            draw.polygon([(dx-hw, dy+hw//4), (dx+hw, dy+hw//4),
                          (dx, dy+hw)], fill=col)

    # ── Layout constants ──────────────────────────────────────────────────────
    ICON_SZ  = 440
    ICON_R   = 98
    icon_x   = (canvas_w - ICON_SZ) // 2
    icon_y   = int(canvas_h * 0.25)   # optical centre — icon top at 25%

    # ── App icon ──────────────────────────────────────────────────────────────
    # Soft pink drop shadow (not black)
    for i in range(22, 0, -2):
        a = int(30 * (1 - i/22))
        draw.rounded_rectangle(
            [icon_x-i//3, icon_y+i//2, icon_x+ICON_SZ+i//3, icon_y+ICON_SZ+i],
            radius=ICON_R+i//3, fill=(*PINK, a))

    # White icon background
    draw.rounded_rectangle(
        [icon_x, icon_y, icon_x+ICON_SZ, icon_y+ICON_SZ],
        radius=ICON_R, fill=(255, 255, 255, 255))
    # Thin pink border
    draw.rounded_rectangle(
        [icon_x, icon_y, icon_x+ICON_SZ, icon_y+ICON_SZ],
        radius=ICON_R, outline=(*PINK, 80), width=3, fill=None)

    if logo_path and os.path.isfile(logo_path):
        pad = 48
        lsz = ICON_SZ - pad*2
        li  = Image.open(logo_path).convert("RGBA").resize((lsz, lsz), Image.LANCZOS)
        img.paste(li, (icon_x+pad, icon_y+pad), li)

    # ── Text block ────────────────────────────────────────────────────────────
    text_y = icon_y + ICON_SZ + 70

    # App name — dark plum on light bg
    draw_centered(title, font_bold, text_y,
                  fill=(*TEXT_D, 255), shadow_fill=(255,255,255,120))
    bb1     = font_bold.getbbox(title) if font_bold else (0,0,0,110)
    text_y += (bb1[3]-bb1[1]) + 20

    # Subtitle
    draw_centered(subtitle, font_med, text_y, fill=(*TEXT_M, 230))
    bb2     = font_med.getbbox(subtitle) if font_med else (0,0,0,66)
    text_y += (bb2[3]-bb2[1]) + 56

    # Hot-pink pill CTA button (like Baby Journey reference)
    CTA_TEXT = "Download Now!"
    bb3  = font_cta.getbbox(CTA_TEXT) if font_cta else (0,0,0,80)
    ctw  = bb3[2]-bb3[0]
    cth  = bb3[3]-bb3[1]
    P_W, P_H = ctw + 120, cth + 48
    px   = (canvas_w - P_W) // 2
    # Pill shadow
    draw.rounded_rectangle([px+4, text_y+6, px+P_W+4, text_y+P_H+6],
                            radius=P_H//2, fill=(*PINK_D, 120))
    # Pill fill
    draw.rounded_rectangle([px, text_y, px+P_W, text_y+P_H],
                            radius=P_H//2, fill=(*PINK, 255))
    # No shimmer — clean flat pill on light bg
    # CTA text
    tx3 = px + (P_W-ctw)//2 - bb3[0]
    ty3 = text_y + (P_H-cth)//2 - bb3[1]
    draw.text((tx3+2, ty3+2), CTA_TEXT, font=font_cta, fill=(180,30,70,150))
    draw.text((tx3,   ty3),   CTA_TEXT, font=font_cta, fill=(*WHITE, 255))
    text_y += P_H + 50

    # Rating — social proof (no star emoji, plain text)
    draw_centered("4.8  /  2M+ Downloads", font_sm, text_y,
                  fill=(*LAVENDER, 210))

    return img.convert("RGB")


def _ffprobe_duration(path: str) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True, check=True,
    )
    return float(r.stdout.strip())


def _ffprobe_dims(path: str) -> tuple[int, int]:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0", path],
        capture_output=True, text=True, check=True,
    )
    w, h = r.stdout.strip().split(",")
    return int(w), int(h)


def _is_target_aspect(w: int, h: int, target_w: int = W, target_h: int = H, tol: float = 0.05) -> bool:
    """True if source aspect is within tol of target (default ±5% of 9:16)."""
    return abs((w / h) - (target_w / target_h)) <= tol


def _compute_pad_layout(src_w: int, src_h: int) -> dict:
    """Compute visible bands when scaling source fit-within 1080x1920.

    Returns disp_w/disp_h (rendered video size), top_h/bot_h (visible
    pad bands above/below the video), side_w (visible pad bands left/right).
    Used to fit brand elements (logo top, text bottom) without overlap.
    """
    scale = min(W / src_w, H / src_h)
    disp_w = int(src_w * scale)
    disp_h = int(src_h * scale)
    # Force even dims (libx264 requirement when overlay positions matter)
    disp_w -= disp_w % 2
    disp_h -= disp_h % 2
    top_h = (H - disp_h) // 2
    bot_h = H - disp_h - top_h
    side_w = (W - disp_w) // 2
    return {
        "disp_w": disp_w, "disp_h": disp_h,
        "top_h": top_h, "bot_h": bot_h, "side_w": side_w,
    }


def _detect_side_blur(video_path: str, src_w: int, src_h: int,
                      n_frames: int = 5) -> tuple[int, int] | None:
    """Detect baked-in side-blur padding via per-column edge density.

    Many ad sources are originally 1:1 or narrower content that was padded
    to 9:16 / 4:5 by adding a BLURRED extension of the same content on
    left+right sides. ffmpeg cropdetect only catches dark/solid bars and
    misses this pattern entirely. This detector samples N frames evenly,
    computes per-column Sobel-x energy (vertical-edge density), and finds
    the horizontal range where sharp content lives.

    Returns (content_x, content_w) or None if no significant side-blur
    detected (sharp content already spans ≥92% of source width).
    """
    try:
        import cv2
        import numpy as np
    except ImportError:
        log.warning("cv2/numpy not available; skipping side-blur detection")
        return None
    try:
        # Sample frames evenly between 15% and 85% of duration
        dur = _ffprobe_duration(video_path)
        densities_list = []
        with tempfile.TemporaryDirectory() as tmp:
            for i in range(n_frames):
                t = dur * (0.15 + 0.70 * i / max(n_frames - 1, 1))
                fpath = os.path.join(tmp, f"f{i}.png")
                r = subprocess.run(
                    ["ffmpeg", "-y", "-ss", f"{t:.2f}", "-i", video_path,
                     "-frames:v", "1", fpath],
                    capture_output=True, timeout=30,
                )
                if r.returncode != 0 or not os.path.isfile(fpath):
                    continue
                gray = cv2.imread(fpath, cv2.IMREAD_GRAYSCALE)
                if gray is None:
                    continue
                sx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
                densities_list.append(np.abs(sx).mean(axis=0))
        if not densities_list:
            return None
        densities = np.mean(densities_list, axis=0)
        # Smooth to ignore single-column spikes
        win = max(5, src_w // 100)
        smooth = np.convolve(densities, np.ones(win) / win, mode="same")
        threshold = smooth.max() * 0.30
        above = smooth > threshold
        if not above.any():
            return None
        left = int(np.argmax(above))
        right = int(len(smooth) - np.argmax(above[::-1]))
        content_w = right - left
        # No significant side-blur if sharp region spans ≥92% of source width
        if content_w >= src_w * 0.92:
            return None
        # Reject absurdly narrow detection (<25% width = false positive)
        if content_w < src_w * 0.25:
            return None

        # Laplacian-variance verification — real blur has very low pixel
        # variation (smoothed), natural low-detail content (dark sky, walls)
        # has higher variation. Without this check, EN_2405_01 (native 9:16
        # with dark sides but sharp content) falsely fired detection at 74%
        # width. Sample one mid-video frame, compute Lap variance per region.
        try:
            with tempfile.TemporaryDirectory() as tmp:
                fpath = os.path.join(tmp, "verify.png")
                t_mid = dur * 0.5
                r2 = subprocess.run(
                    ["ffmpeg", "-y", "-ss", f"{t_mid:.2f}", "-i", video_path,
                     "-frames:v", "1", fpath],
                    capture_output=True, timeout=20,
                )
                if r2.returncode == 0 and os.path.isfile(fpath):
                    gray = cv2.imread(fpath, cv2.IMREAD_GRAYSCALE)
                    if gray is not None and left > 8 and right < src_w - 8:
                        side_left = gray[:, :left]
                        center = gray[:, left:right]
                        if side_left.size > 0 and center.size > 0:
                            side_var = cv2.Laplacian(side_left, cv2.CV_64F).var()
                            center_var = cv2.Laplacian(center, cv2.CV_64F).var()
                            # If sides are not significantly blurrier than
                            # center, this is false positive (natural low-
                            # detail, not baked-in blur).
                            if center_var <= 1.0 or (side_var / center_var) > 0.40:
                                log.info(
                                    f"side-blur verify FAIL: side_lap={side_var:.1f} "
                                    f"center_lap={center_var:.1f} ratio="
                                    f"{(side_var/max(center_var,1e-9)):.2f} (need ≤0.40)"
                                )
                                return None
        except Exception as e:
            log.warning(f"side-blur Laplacian verify failed: {e}")
            # Fall through — trust the edge-density detection alone

        # Snap to even pixels (libx264 requirement)
        left -= left % 2
        content_w -= content_w % 2
        return left, content_w
    except Exception as e:
        log.warning(f"side-blur detection failed: {e}")
        return None


def _detect_content_crop(video_path: str, src_w: int, src_h: int,
                         limit: int = 40) -> tuple[int, int, int, int] | None:
    """Detect baked-in black/dark bars via ffmpeg cropdetect.

    Samples ~60 frames after t=1s with intensity threshold `limit` (default 40
    catches dark-grey bars like #1a1a1a / #2a2a2a — pure-black-only default 24
    misses these). Returns (W, H, X, Y) content region, or None if no
    significant bars detected (content fills source within ±8px slop).
    """
    try:
        r = subprocess.run(
            ["ffmpeg", "-ss", "1", "-i", video_path,
             "-vf", f"cropdetect={limit}:16:0",
             "-frames:v", "60", "-f", "null", "-"],
            capture_output=True, text=True, timeout=60,
        )
        crops = re.findall(r"crop=(\d+):(\d+):(\d+):(\d+)", r.stderr)
        if not crops:
            return None
        cw, ch, cx, cy = map(int, crops[-1])
        # If detected crop is essentially full frame (±8px slop), no bars
        if cw >= src_w - 8 and ch >= src_h - 8:
            return None
        # Skip if detected crop is absurdly small (cropdetect false alarm)
        if cw < src_w // 4 or ch < src_h // 4:
            return None
        return (cw, ch, cx, cy)
    except Exception as e:
        log.warning(f"cropdetect failed: {e}")
        return None


def _detect_endcard_start(video_path: str, src_dur: float,
                          min_drop_pct: float = 0.65,
                          min_tail_s: float = 0.3) -> float | None:
    """Detect end-card start timestamp via ffmpeg scene-change detection.

    Looks for the EARLIEST significant scene change in the last (1-min_drop_pct)
    of the source. Using min() instead of max() handles multi-card outros (e.g.
    a "TRY NOW!" card followed by a "Download Now" card — we want to cut at the
    first card, not the transition between them).

    Uses a low threshold (0.15) to catch soft fades into outro cards, not just
    hard cuts.

    Returns timestamp in seconds if the end-card tail is at least
    `min_tail_s` long; else None (no clear end-card found).
    """
    try:
        r = subprocess.run(
            ["ffmpeg", "-i", video_path,
             "-vf", "select='gte(scene,0.08)',showinfo",
             "-vsync", "0", "-an", "-f", "null", "-"],
            capture_output=True, text=True, timeout=120,
        )
        times = [float(m.group(1)) for m in re.finditer(r"pts_time:(\d+\.?\d*)", r.stderr)]
        if not times:
            return None
        # Keep only times in the valid outro window
        window_start = src_dur * min_drop_pct
        window_end   = src_dur - min_tail_s
        valid = [t for t in times if window_start <= t <= window_end]
        if not valid:
            return None
        # Return the EARLIEST scene change in the window — that's where the outro begins
        return min(valid)
    except Exception as e:
        log.warning(f"endcard detection failed: {e}")
        return None


async def _generate_tts(text: str, voice: str, output_path: str, *, rate: str = "+0%") -> None:
    import edge_tts
    comm = edge_tts.Communicate(text, voice, rate=rate)
    await comm.save(output_path)


def _build_jittered_params(seed: int | None) -> dict:
    """Pick viewer-safe randomized params for one brand-pass run.

    seed=None → fresh random each call (system entropy)
    seed=<int> → deterministic; same input + same seed → byte-identical output
    """
    if seed is None:
        seed = random.SystemRandom().randint(0, 2**32 - 1)
    rng = random.Random(seed)

    def U(key):
        a, b = SAFE_JITTER[key]
        return rng.uniform(a, b)
    def I(key):
        a, b = SAFE_JITTER[key]
        return rng.randint(a, b)
    def C(key):
        return rng.choice(SAFE_JITTER[key])

    now = datetime.datetime.now(datetime.timezone.utc)
    fake_dt = now + datetime.timedelta(seconds=rng.uniform(-7 * 86400, 0))
    fake_creation_time = fake_dt.strftime("%Y-%m-%dT%H:%M:%S.000000Z")

    return {
        "seed":          seed,
        "zoom":          round(U("zoom"), 4),
        "crop_dx":       I("crop_dx"),
        "crop_dy":       I("crop_dy"),
        "saturation":    round(U("saturation"), 3),
        "contrast":      round(U("contrast"), 3),
        "gamma":         round(U("gamma"), 3),
        "hue":           round(U("hue"), 1),
        "wm_opacity":    round(U("wm_opacity"), 2),
        "wm_dx":         I("wm_dx"),
        "wm_dy":         I("wm_dy"),
        "bgm_vol":       round(U("bgm_vol"), 2),
        "tts_rate_pct":  I("tts_rate_pct"),
        "outro_dur":     round(U("outro_dur"), 2),
        "outro_bg":      C("outro_bg"),
        "outro_sub_color": C("outro_sub_color"),
        "crf":           C("crf"),
        "preset":        C("preset"),
        "voice":         rng.choice(TTS_VOICE_POOL_MALE),
        "fake_creation_time": fake_creation_time,
    }


def _transcribe_video(video_path: str, work_root: str | None = None) -> str:
    """Extract audio + Whisper transcribe with non-speech gating.

    Returns "" (empty) when the source is music-only / no real speech so the
    caller can substitute a silent placeholder and keep the original BGM instead
    of TTS-reading Whisper's hallucinations on lyrics or music.

    Gate rules (per faster-whisper segment):
      - drop segments with empty text
      - drop segments with avg_logprob <= -0.5 (rejects hallucinations like
        "Thank you.", "Jimmy is buying" — Whisper invents low-confidence text
        when run on music-only audio with language="en" forced)
      - if remaining speech duration < max(1.5s, 5% of video) → music-only

    Why this threshold:
      Empirical on this project's ad sources — music-only hallucinations have
      logp in [-1.0, -0.7]; real brief hooks like "What doesn't kill you?"
      have logp ≥ -0.3. The -0.5 split is well clear of both clusters.
      no_speech_prob is NOT used: it fires high (~0.7+) on ALL music-dominant
      content including real short hooks, so it's unreliable as a per-segment
      filter.
    """
    work = tempfile.mkdtemp(prefix="brandpass_tx_", dir=work_root) if work_root else tempfile.mkdtemp(prefix="brandpass_tx_")
    try:
        audio = extract_audio(video_path, work)
        video_dur = _ffprobe_duration(video_path)
        # Use raw WhisperModel directly to access no_speech_prob + avg_logprob
        # (the project transcribe() drops these fields). _get_model reuses the
        # process-wide LRU cache so we don't re-load `small` per call.
        from pipeline.transcribe import _get_model
        model = _get_model("small")
        try:
            raw_segments, _info = model.transcribe(
                audio, language="en", vad_filter=True, beam_size=1,
            )
            seg_list = list(raw_segments)
        except Exception as e:
            log.warning(f"GPU transcribe failed ({e}); falling back to CPU")
            from pipeline.transcribe import _fallback_to_cpu
            model = _fallback_to_cpu("small")
            raw_segments, _info = model.transcribe(
                audio, language="en", vad_filter=True, beam_size=1,
            )
            seg_list = list(raw_segments)

        real = [s for s in seg_list if s.text.strip() and s.avg_logprob > -0.5]
        speech_dur = sum(s.end - s.start for s in real)
        min_speech = max(1.5, video_dur * 0.05)
        if speech_dur < min_speech:
            log.info(
                f"No real speech: kept {len(real)}/{len(seg_list)} segs, "
                f"speech={speech_dur:.1f}s < gate={min_speech:.1f}s — treating as music-only"
            )
            return ""
        text = " ".join(s.text.strip() for s in real).strip()
        log.info(
            f"Real speech detected: {len(real)}/{len(seg_list)} segs, "
            f"{speech_dur:.1f}s, transcript={len(text)}ch"
        )
        return text
    finally:
        shutil.rmtree(work, ignore_errors=True)


def brand_pass_video(
    input_path: str,
    output_path: str,
    *,
    transcript: str | None = None,
    voice: str | None = None,
    watermark_text: str = DEFAULT_WATERMARK,
    watermark_image: str | None = None,
    watermark_size: int = 120,
    outro_title: str = DEFAULT_OUTRO_TITLE,
    outro_subtitle: str = DEFAULT_OUTRO_SUB,
    outro_logo_image: str | None = None,
    outro_logo_size: int = 260,
    outro_video: str | None = None,
    outro_duration: float | None = None,
    bgm_volume: float | None = None,
    trim_endcard: bool = False,
    trim_endcard_min_drop_pct: float = 0.7,
    pad_bg_image: str | None = None,
    bgm_replace_path: str | None = None,
    work_root: str | None = None,
    random_seed: int | None = None,
) -> str:
    """Apply V4c brand-pass to input video, write Reels-ready output.

    Watermark: pass `watermark_image=<png_path>` to overlay a logo PNG (preferred);
    falls back to `watermark_text` drawtext when no image given.

    Outro: pass `outro_logo_image=<png_path>` to add a logo above the brand text
    on the outro card.

    End-card trim: with `trim_endcard=True`, runs ffmpeg scene-change detection
    on the source and trims away the last static end-card scene (e.g. a
    competitor's app catalog screen). Only trims if a clear scene change is
    found in the last (1-min_drop_pct) of duration.

    `random_seed=None` → fresh random each call. `random_seed=<int>` → deterministic.
    Explicit `voice` / `outro_duration` / `bgm_volume` override the jittered pick.

    Returns output_path on success.
    """
    if not os.path.isfile(input_path):
        raise FileNotFoundError(input_path)
    if watermark_image and not os.path.isfile(watermark_image):
        raise FileNotFoundError(f"watermark_image: {watermark_image}")
    if outro_logo_image and not os.path.isfile(outro_logo_image):
        raise FileNotFoundError(f"outro_logo_image: {outro_logo_image}")
    if outro_video and not os.path.isfile(outro_video):
        raise FileNotFoundError(f"outro_video: {outro_video}")
    if pad_bg_image and not os.path.isfile(pad_bg_image):
        raise FileNotFoundError(f"pad_bg_image: {pad_bg_image}")
    os.makedirs(os.path.dirname(os.path.abspath(output_path)) or ".", exist_ok=True)

    p = _build_jittered_params(random_seed)
    rng = random.Random(random_seed)   # second rng for outro frame jitter
    if voice is not None: p["voice"] = voice
    if outro_duration is not None: p["outro_dur"] = outro_duration
    if bgm_volume is not None: p["bgm_vol"] = bgm_volume

    if outro_video:
        try:
            _probe = subprocess.run(
                ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", outro_video],
                capture_output=True, text=True, check=True,
            )
            p["outro_dur"] = round(float(_probe.stdout.strip()), 2)
        except Exception as _e:
            log.warning(f"ffprobe outro_video failed ({_e}); keeping jittered outro_dur")

    if work_root:
        os.makedirs(work_root, exist_ok=True)
        work = tempfile.mkdtemp(prefix="brandpass_", dir=work_root)
    else:
        work = tempfile.mkdtemp(prefix="brandpass_")
    log.info(f"Brand-pass: {os.path.basename(input_path)} → {os.path.basename(output_path)}")
    log.info(
        f"Seed={p['seed']}  voice={p['voice']}  rate={p['tts_rate_pct']:+d}%  "
        f"zoom={p['zoom']}  crop_off=({p['crop_dx']},{p['crop_dy']})  "
        f"color=(sat={p['saturation']},con={p['contrast']},gam={p['gamma']},hue={p['hue']}°)  "
        f"wm=(op={p['wm_opacity']},dx={p['wm_dx']},dy={p['wm_dy']})  "
        f"bgm={p['bgm_vol']}  outro=({p['outro_dur']}s, bg={p['outro_bg']})  "
        f"enc=(crf={p['crf']}, preset={p['preset']})"
    )
    try:
        # 0. Optional: detect + trim end-card from source
        working_input = input_path
        working_dur = _ffprobe_duration(input_path)
        if trim_endcard:
            endcard_t = _detect_endcard_start(input_path, working_dur, trim_endcard_min_drop_pct)
            if endcard_t:
                trimmed = os.path.join(work, "src_trimmed.mp4")
                subprocess.run(
                    ["ffmpeg", "-y", "-i", input_path, "-t", f"{endcard_t:.3f}",
                     "-c", "copy", "-avoid_negative_ts", "make_zero", trimmed],
                    capture_output=True, check=True, text=True,
                )
                log.info(f"End-card trim: {working_dur:.2f}s → {endcard_t:.2f}s (cut {working_dur - endcard_t:.2f}s)")
                working_input = trimmed
                working_dur = endcard_t
            else:
                log.info("No clear end-card detected; using full source")

        # 1. Transcript (on working_input so end-card narration is excluded)
        if transcript is None:
            log.info("Transcribing (Whisper small) ...")
            transcript = _transcribe_video(working_input, work_root=work_root)
        log.info(f"Transcript: {transcript[:80]}{'...' if len(transcript) > 80 else ''}")

        # 2. Extract source audio
        src_audio_dir = os.path.join(work, "src_audio")
        os.makedirs(src_audio_dir, exist_ok=True)
        log.info("Extracting source audio ...")
        from pipeline.audio import extract_audio_hq
        src_audio = extract_audio_hq(working_input, src_audio_dir)

        total_dur = working_dur + p["outro_dur"]
        mixed_audio = os.path.join(work, "mixed.m4a")
        has_voice = bool(transcript and transcript.strip())

        # Pick BGM source: replacement track > Demucs-separated > raw source
        if bgm_replace_path:
            bgm = bgm_replace_path
            log.info(f"BGM: replacement={os.path.basename(bgm)} (skip Demucs)")
        elif has_voice:
            log.info("Demucs htdemucs separating BGM ...")
            demucs_dir = os.path.join(work, "demucs")
            os.makedirs(demucs_dir, exist_ok=True)
            stems = separate_audio(src_audio, demucs_dir, model="htdemucs")
            bgm = stems["no_vocals"]
        else:
            bgm = src_audio  # passthrough — no separation needed

        if has_voice:
            # VOICE PATH: TTS over BGM (ducked), bgm loops to cover full duration
            rate_str = f"{p['tts_rate_pct']:+d}%"
            tts_audio = os.path.join(work, "tts.mp3")
            log.info(f"TTS Edge ({p['voice']}, rate={rate_str}) ...")
            asyncio.run(_generate_tts(transcript, p["voice"], tts_audio, rate=rate_str))

            log.info(f"Mixing TTS over BGM (BGM vol={p['bgm_vol']}) → {total_dur:.2f}s ...")
            r = subprocess.run(
                ["ffmpeg", "-y",
                 "-stream_loop", "-1", "-i", bgm,
                 "-i", tts_audio,
                 "-filter_complex",
                 f"[1:a]volume=1.0,apad[v0];"
                 f"[0:a]volume={p['bgm_vol']},apad[v1];"
                 f"[v0][v1]amix=inputs=2:duration=first:dropout_transition=0,atrim=duration={total_dur}[aout]",
                 "-map", "[aout]", "-c:a", "aac", "-b:a", "192k", mixed_audio],
                capture_output=True, text=True,
            )
            if r.returncode != 0:
                raise RuntimeError(f"ffmpeg mix failed (exit {r.returncode}): {r.stderr[-800:]}")
        else:
            # MUSIC-ONLY PATH: BGM @ 100% vol, looped/padded to total_dur.
            # If bgm_replace_path is set, this is the new track. Otherwise
            # the source's original mix is passed through as-is.
            log.info(f"Music-only: {os.path.basename(bgm)} @ 100% vol → {total_dur:.2f}s")
            r = subprocess.run(
                ["ffmpeg", "-y",
                 "-stream_loop", "-1", "-i", bgm,
                 "-af", f"apad=whole_dur={total_dur:.3f}",
                 "-t", f"{total_dur:.3f}",
                 "-c:a", "aac", "-b:a", "192k", mixed_audio],
                capture_output=True, text=True,
            )
            if r.returncode != 0:
                raise RuntimeError(f"ffmpeg audio failed (exit {r.returncode}): {r.stderr[-800:]}")

        # 6. Video transforms (side-blur aware, aspect-aware)
        # Order: detect baked padding first → compute EFFECTIVE aspect on real
        # content → branch on that. Sources that are 9:16 on disk but contain
        # baked-in side blur (a narrower original padded with blurred edges)
        # have effective aspect closer to 1:1 / 4:5 once stripped, and many
        # 4:5 / 1:1 sources are actually 9:16 content after stripping.
        src_w, src_h = _ffprobe_dims(working_input)

        side_blur = _detect_side_blur(working_input, src_w, src_h)
        if side_blur:
            sb_x, sb_w = side_blur
            pre_crop = f"crop={sb_w}:{src_h}:{sb_x}:0,"
            eff_w, eff_h = sb_w, src_h
            log.info(f"Side-blur stripped: {src_w}x{src_h} → {sb_w}x{src_h} at x={sb_x}")
        else:
            crop_region = _detect_content_crop(working_input, src_w, src_h)
            if crop_region:
                cw, ch, cx, cy = crop_region
                pre_crop = f"crop={cw}:{ch}:{cx}:{cy},"
                eff_w, eff_h = cw, ch
                log.info(f"Auto-strip baked bars: {src_w}x{src_h} → {cw}x{ch} at ({cx},{cy})")
            else:
                pre_crop = ""
                eff_w, eff_h = src_w, src_h

        is_target = _is_target_aspect(eff_w, eff_h)
        log.info(f"Effective aspect: {eff_w}x{eff_h}  is_9:16={is_target}  "
                 f"pad_bg={'yes' if pad_bg_image else 'no'}")

        color = (
            f"eq=saturation={p['saturation']}:contrast={p['contrast']}:gamma={p['gamma']},"
            f"hue=h={p['hue']}"
        )

        if is_target:
            # === Branch 6a: pre-crop (if any) → zoom + crop (jittered) ===
            scaled_w = int(W * p["zoom"])
            scaled_h = int(H * p["zoom"])
            zoom_crop = (
                f"{pre_crop}"
                f"scale={scaled_w}:{scaled_h}:force_original_aspect_ratio=increase,"
                f"crop={W}:{H}:(in_w-{W})/2+({p['crop_dx']}):(in_h-{H})/2+({p['crop_dy']})"
            )
            bg_filter = f"{zoom_crop},{color}"
            bg_inputs = ["-i", working_input]
            bg_label = "[0:v]"
            n_bg_inputs = 1
        else:
            # === Branch 6b: blur-pad bg + fit-within content (max content) ===
            # pre_crop was computed above (side-blur or dark-bar strip).
            # Background = scaled-COVER + cropped + boxblur (modern Reels look).
            # Foreground = scaled fit-within full 1080x1920 (no safe-zone trim).
            # Brand visibility via corner watermark (step 7) + outro card (step 8).
            # If pad_bg_image given, use it as bg (legacy band layout).

            if pad_bg_image:
                bg_chunk = f"[1:v]scale={W}:{H},setsar=1[bgblur]"
                bg_inputs_extra = ["-loop", "1", "-framerate", "30", "-i", pad_bg_image]
                n_extra = 1
                log.info("Bg: pad_bg_image (legacy bands layout)")
            else:
                bg_chunk = (
                    f"[0:v]{pre_crop}scale={W}:{H}:force_original_aspect_ratio=increase,"
                    f"crop={W}:{H},boxblur=20:5,setsar=1[bgblur]"
                )
                bg_inputs_extra = []
                n_extra = 0
                log.info("Bg: blur-pad (max content)")

            fg_chunk = (
                f"[0:v]{pre_crop}scale={W}:{H}:force_original_aspect_ratio=decrease,"
                f"setsar=1,{color}[fg]"
            )
            overlay_chunk = "[bgblur][fg]overlay=(W-w)/2:(H-h)/2:shortest=1"
            bg_filter = ";".join([bg_chunk, fg_chunk, overlay_chunk])
            bg_inputs = ["-i", working_input] + bg_inputs_extra
            n_bg_inputs = 1 + n_extra
            bg_label = ""

        body = os.path.join(work, "body.mp4")
        log.info("Encoding transformed body (zoom/pad + color + watermark) ...")
        if watermark_image:
            wm_x = SIDE_SAFE + p["wm_dx"]
            wm_y = TOP_SAFE + 20 + p["wm_dy"]
            wm_x = max(0, min(W - watermark_size, wm_x))
            wm_y = max(0, min(H - watermark_size, wm_y))
            # Watermark image is appended at the next input index after bg inputs
            wm_idx = n_bg_inputs
            filter_complex = (
                f"{bg_label}{bg_filter}[bg];"
                f"[{wm_idx}:v]scale={watermark_size}:{watermark_size},format=rgba,"
                f"colorchannelmixer=aa={p['wm_opacity']}[wm];"
                f"[bg][wm]overlay={wm_x}:{wm_y}[vout]"
            )
            wm_inputs = ["-i", watermark_image]
            subprocess.run(
                ["ffmpeg", "-y"] + bg_inputs + wm_inputs +
                ["-filter_complex", filter_complex, "-map", "[vout]",
                 "-c:v", "libx264", "-preset", p["preset"], "-crf", str(p["crf"]),
                 "-an", "-shortest", body],
                capture_output=True, check=True, text=True,
            )
        else:
            # drawtext watermark (legacy text-based)
            wm_x = SIDE_SAFE + p["wm_dx"]
            wm_y = TOP_SAFE + 20 + p["wm_dy"]
            drawtext_wm = (
                f"drawtext=fontfile='{FONT_FF}':text='{watermark_text}':"
                f"fontsize=36:fontcolor=white@{p['wm_opacity']}:borderw=2:bordercolor=black@0.4:"
                f"x={wm_x}:y={wm_y}"
            )
            if is_target:
                # Branch 6a: single input, simple -vf
                subprocess.run(
                    ["ffmpeg", "-y", "-i", working_input,
                     "-vf", f"{bg_filter},{drawtext_wm}",
                     "-c:v", "libx264", "-preset", p["preset"], "-crf", str(p["crf"]),
                     "-an", body],
                    capture_output=True, check=True, text=True,
                )
            else:
                # Branch 6b: multi-input filter_complex with drawtext_wm appended
                filter_complex = f"{bg_filter},{drawtext_wm}[vout]"
                subprocess.run(
                    ["ffmpeg", "-y"] + bg_inputs +
                    ["-filter_complex", filter_complex, "-map", "[vout]",
                     "-c:v", "libx264", "-preset", p["preset"], "-crf", str(p["crf"]),
                     "-an", "-shortest", body],
                    capture_output=True, check=True, text=True,
                )

        # 7. Outro card — user-supplied mp4 OR Pillow-generated 2026 designed card
        outro = os.path.join(work, "outro.mp4")
        if outro_video:
            log.info(f"Normalizing supplied outro video → {W}x{H}@30fps, no audio ...")
            subprocess.run(
                ["ffmpeg", "-y", "-i", outro_video,
                 "-vf", f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
                        f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:black,setsar=1,fps=30",
                 "-c:v", "libx264", "-preset", p["preset"], "-crf", str(p["crf"]),
                 "-pix_fmt", "yuv420p", "-an", outro],
                capture_output=True, check=True, text=True,
            )
        else:
            outro_frame_png = os.path.join(work, "outro_frame.png")
            log.info("Generating outro card (Pillow) ...")
            frame = _generate_outro_frame(
                W, H, outro_title, outro_subtitle,
                outro_logo_image, outro_logo_size,
                p["outro_bg"], rng,
            )
            frame.save(outro_frame_png)
            subprocess.run(
                ["ffmpeg", "-y",
                 "-loop", "1", "-framerate", "30", "-i", outro_frame_png,
                 "-t", str(p["outro_dur"]),
                 "-c:v", "libx264", "-preset", p["preset"], "-crf", str(p["crf"]),
                 "-pix_fmt", "yuv420p", outro],
                capture_output=True, check=True, text=True,
            )

        # 8. Concat body + outro, mux with mixed audio
        concat_list = os.path.join(work, "list.txt")
        with open(concat_list, "w", encoding="utf-8") as f:
            f.write(f"file '{body}'\nfile '{outro}'\n")

        log.info("Concat + mux mixed audio → final ...")
        subprocess.run(
            ["ffmpeg", "-y",
             "-f", "concat", "-safe", "0", "-i", concat_list,
             "-i", mixed_audio,
             "-map", "0:v", "-map", "1:a",
             "-c:v", "libx264", "-preset", p["preset"], "-crf", str(p["crf"]),
             "-c:a", "copy",
             "-map_metadata", "-1",
             "-metadata", f"creation_time={p['fake_creation_time']}",
             "-movflags", "+faststart",
             output_path],
            capture_output=True, check=True, text=True,
        )

        log.info(f"Brand-pass output: {output_path} ({os.path.getsize(output_path) // 1024} KB)")
        return output_path
    finally:
        shutil.rmtree(work, ignore_errors=True)
