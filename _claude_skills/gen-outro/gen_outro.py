"""
gen_outro.py — standalone outro card generator.
Usage:
    python gen_outro.py --logo <path> --theme <theme> --title <title>
                        --subtitle <subtitle> --out <output.png>
                        [--rating "4.8 / 2M+ Downloads"]

Themes: baby | tech | minimal
"""
import argparse, os, math, random
from PIL import Image, ImageDraw, ImageFont

W, H = 1080, 1920

# ── Font loader ───────────────────────────────────────────────────────────────
def font(size, bold=True):
    candidates = [
        r"C:\Windows\Fonts\seguisb.ttf" if bold else r"C:\Windows\Fonts\segoeui.ttf",
        r"C:\Windows\Fonts\segoeui.ttf",
        r"C:\Windows\Fonts\arialbd.ttf" if bold else r"C:\Windows\Fonts\arial.ttf",
        r"C:\Windows\Fonts\arial.ttf",
    ]
    for p in candidates:
        if os.path.exists(p):
            try:    return ImageFont.truetype(p, size)
            except: pass
    return ImageFont.load_default()

# ── Theme definitions ─────────────────────────────────────────────────────────
THEMES = {
    "baby": {
        "bg_top":       (255, 248, 252),
        "bg_bot":       (245, 228, 252),
        "icon_bg":      (255, 255, 255),
        "icon_border":  (255,  90, 140),
        "shadow_color": (255,  90, 140),
        "text_title":   ( 60,  30,  80),
        "text_sub":     (140,  90, 170),
        "pill_fill":    (255,  90, 140),
        "pill_shadow":  (230,  55, 110),
        "pill_text":    (255, 255, 255),
        "rating_color": (180, 130, 220),
        "deco_hearts":  (255,  90, 140),
        "deco_stars":   (180, 130, 220),
        "decorations":  True,
    },
    "tech": {
        "bg_top":       (  6,   4,  18),
        "bg_bot":       ( 18,   8,  48),
        "icon_bg":      ( 65,  30, 160),
        "icon_border":  (148, 100, 250),
        "shadow_color": (  0,   0,   0),
        "text_title":   (255, 255, 255),
        "text_sub":     (200, 190, 240),
        "pill_fill":    (105,  55, 210),
        "pill_shadow":  ( 65,  30, 160),
        "pill_text":    (255, 255, 255),
        "rating_color": (148, 100, 250),
        "deco_hearts":  (105,  55, 210),
        "deco_stars":   (148, 100, 250),
        "decorations":  False,
    },
    "minimal": {
        "bg_top":       (250, 250, 252),
        "bg_bot":       (240, 240, 248),
        "icon_bg":      (255, 255, 255),
        "icon_border":  (200, 200, 220),
        "shadow_color": (200, 200, 220),
        "text_title":   ( 30,  30,  60),
        "text_sub":     (120, 120, 160),
        "pill_fill":    ( 60,  60, 180),
        "pill_shadow":  ( 40,  40, 140),
        "pill_text":    (255, 255, 255),
        "rating_color": (160, 160, 200),
        "deco_hearts":  (200, 200, 220),
        "deco_stars":   (180, 180, 210),
        "decorations":  False,
    },
}

def draw_centered(draw, img_w, text, fnt, y, fill, shadow=None):
    if not fnt or not text:
        return 0
    bb = fnt.getbbox(text)
    tw, th = bb[2]-bb[0], bb[3]-bb[1]
    tx = (img_w - tw) // 2 - bb[0]
    if shadow:
        draw.text((tx+2, y+2), text, font=fnt, fill=(*shadow, 130))
    draw.text((tx, y), text, font=fnt, fill=fill)
    return th

def draw_decorations(draw, t, rng):
    """Scatter hearts and stars around canvas."""
    positions = [
        (120,  220, "heart", 28, 180), (940,  180, "star",  22, 160),
        ( 80,  550, "star",  18, 140), (980,  480, "heart", 24, 170),
        (160,  900, "dot",   16, 120), (930,  860, "star",  20, 150),
        (100, 1280, "heart", 22, 140), (960, 1220, "dot",   18, 130),
        (140, 1580, "star",  24, 160), (930, 1540, "heart", 20, 150),
        (200, 1800, "dot",   14, 100), (870, 1780, "star",  16, 120),
    ]
    PINK    = t["deco_hearts"]
    LAVEND  = t["deco_stars"]
    for (dx, dy, dtype, dsz, dalpha) in positions:
        dx += rng.randint(-20, 20)
        dy += rng.randint(-20, 20)
        col = (*PINK, dalpha) if dtype == "heart" else (*LAVEND, dalpha)
        if dtype == "dot":
            draw.ellipse([dx-dsz//2, dy-dsz//2, dx+dsz//2, dy+dsz//2], fill=col)
        elif dtype == "star":
            for angle in [0, 45]:
                pts = []
                for a in [0, 90, 180, 270]:
                    r2   = math.radians(a + angle)
                    r2b  = math.radians(a + 45 + angle)
                    ro, ri = dsz//2, dsz//5
                    pts.append((dx + ro*math.cos(r2),  dy + ro*math.sin(r2)))
                    pts.append((dx + ri*math.cos(r2b), dy + ri*math.sin(r2b)))
                draw.polygon(pts, fill=col)
        else:  # heart
            hw = dsz
            draw.ellipse([dx-hw, dy-hw//2, dx,    dy+hw//2], fill=col)
            draw.ellipse([dx,    dy-hw//2, dx+hw, dy+hw//2], fill=col)
            draw.polygon([(dx-hw, dy+hw//4), (dx+hw, dy+hw//4), (dx, dy+hw)], fill=col)

def generate(logo_path, theme_name, title, subtitle, out_path,
             rating="4.8  /  2M+ Downloads", seed=None):
    t   = THEMES.get(theme_name, THEMES["baby"])
    rng = random.Random(seed)

    # ── Canvas + gradient bg ──────────────────────────────────────────────────
    img  = Image.new("RGBA", (W, H), (*t["bg_top"], 255))
    draw = ImageDraw.Draw(img, "RGBA")
    for y in range(H):
        p = y / H
        draw.line([(0, y), (W, y)], fill=(
            int(t["bg_top"][0] + (t["bg_bot"][0]-t["bg_top"][0])*p),
            int(t["bg_top"][1] + (t["bg_bot"][1]-t["bg_top"][1])*p),
            int(t["bg_top"][2] + (t["bg_bot"][2]-t["bg_top"][2])*p), 255))

    # ── Decorations ───────────────────────────────────────────────────────────
    if t["decorations"]:
        draw_decorations(draw, t, rng)

    # ── Fonts ─────────────────────────────────────────────────────────────────
    fnt_title = font(110, bold=True)
    fnt_sub   = font( 66, bold=False)
    fnt_cta   = font( 76, bold=True)
    fnt_sm    = font( 42, bold=False)

    # ── App icon ─────────────────────────────────────────────────────────────
    ICON_SZ = 440
    ICON_R  = 98
    icon_x  = (W - ICON_SZ) // 2
    icon_y  = int(H * 0.25)

    SC = t["shadow_color"]
    for i in range(22, 0, -2):
        a = int(30 * (1 - i/22))
        draw.rounded_rectangle(
            [icon_x-i//3, icon_y+i//2, icon_x+ICON_SZ+i//3, icon_y+ICON_SZ+i],
            radius=ICON_R+i//3, fill=(*SC, a))

    draw.rounded_rectangle([icon_x, icon_y, icon_x+ICON_SZ, icon_y+ICON_SZ],
                            radius=ICON_R, fill=(*t["icon_bg"], 255))
    IB = t["icon_border"]
    draw.rounded_rectangle([icon_x, icon_y, icon_x+ICON_SZ, icon_y+ICON_SZ],
                            radius=ICON_R, outline=(*IB, 80), width=3, fill=None)

    if logo_path and os.path.isfile(logo_path):
        pad = 48
        lsz = ICON_SZ - pad*2
        li  = Image.open(logo_path).convert("RGBA").resize((lsz, lsz), Image.LANCZOS)
        img.paste(li, (icon_x+pad, icon_y+pad), li)

    # ── Text ──────────────────────────────────────────────────────────────────
    ty = icon_y + ICON_SZ + 70

    draw_centered(draw, W, title, fnt_title, ty,
                  fill=(*t["text_title"], 255),
                  shadow=t["bg_bot"] if t["text_title"][0] > 200 else None)
    bb1 = fnt_title.getbbox(title)
    ty += (bb1[3]-bb1[1]) + 20

    draw_centered(draw, W, subtitle, fnt_sub, ty, fill=(*t["text_sub"], 230))
    bb2 = fnt_sub.getbbox(subtitle)
    ty += (bb2[3]-bb2[1]) + 56

    # ── CTA pill button ───────────────────────────────────────────────────────
    CTA = "Download Now!"
    bb3 = fnt_cta.getbbox(CTA)
    ctw, cth = bb3[2]-bb3[0], bb3[3]-bb3[1]
    PW, PH = ctw + 120, cth + 48
    px = (W - PW) // 2
    PS = t["pill_shadow"]
    draw.rounded_rectangle([px+4, ty+6, px+PW+4, ty+PH+6],
                            radius=PH//2, fill=(*PS, 120))
    draw.rounded_rectangle([px, ty, px+PW, ty+PH],
                            radius=PH//2, fill=(*t["pill_fill"], 255))
    tx3 = px + (PW-ctw)//2 - bb3[0]
    ty3 = ty + (PH-cth)//2 - bb3[1]
    draw.text((tx3, ty3), CTA, font=fnt_cta, fill=(*t["pill_text"], 255))
    ty += PH + 50

    # ── Rating ────────────────────────────────────────────────────────────────
    if rating:
        draw_centered(draw, W, rating, fnt_sm, ty, fill=(*t["rating_color"], 210))

    img.convert("RGB").save(out_path, quality=96)
    print(f"Saved: {out_path}")

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--logo",     required=True)
    ap.add_argument("--theme",    default="baby", choices=list(THEMES))
    ap.add_argument("--title",    default="Chatify")
    ap.add_argument("--subtitle", default="Your AI Companion")
    ap.add_argument("--rating",   default="4.8  /  2M+ Downloads")
    ap.add_argument("--out",      default="outro_preview.png")
    ap.add_argument("--seed",     type=int, default=None)
    args = ap.parse_args()
    generate(args.logo, args.theme, args.title, args.subtitle,
             args.out, args.rating, args.seed)
