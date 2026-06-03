"""
prep_campaign_split.py — build per-video contact sheets + ad-copy manifest
so an LLM (Claude Opus, in-chat) can VIEW the videos and split them into
creative angles. No Gemini / external API — ffmpeg + PIL only.

Usage:
    python prep_campaign_split.py <root> [--frames 6] [--cols 3]
                                  [--width 360] [--out _campaign_prep] [--limit 0]

<root> has language subfolders, each holding *.mp4 (+ optional sibling .txt
ad-library metadata). For each video it extracts `frames` keyframes evenly
across the timeline, tiles them into one labeled contact sheet jpg, and parses
the .txt for Primary Text + Headline/CTA. Outputs:

    <root>/<out>/<lang>__<base>.jpg     one contact sheet per video
    <root>/<out>/manifest.md            per-video: file, lang, ad-copy
"""
import argparse, os, subprocess, sys, tempfile, glob

sys.stdout.reconfigure(encoding="utf-8")
from PIL import Image, ImageDraw, ImageFont


def font(size):
    for p in (r"C:\Windows\Fonts\segoeui.ttf", r"C:\Windows\Fonts\arial.ttf"):
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()


def ffprobe_dur(path):
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except Exception:
        return 0.0


def extract_frame(path, t, out_jpg):
    subprocess.run(
        ["ffmpeg", "-y", "-ss", f"{t:.2f}", "-i", path,
         "-frames:v", "1", "-q:v", "3", out_jpg],
        capture_output=True, text=True)
    return os.path.isfile(out_jpg) and os.path.getsize(out_jpg) > 0


def parse_txt(txt_path):
    """Return (primary_text, headline_cta) from ad-library .txt."""
    if not os.path.isfile(txt_path):
        return "", ""
    try:
        lines = open(txt_path, encoding="utf-8", errors="ignore").read().splitlines()
    except Exception:
        return "", ""
    primary, headline, section = [], [], None
    for ln in lines:
        s = ln.strip()
        up = s.upper()
        if up.startswith("PRIMARY TEXT"):
            section = "p"; continue
        if up.startswith("HEADLINE") or up.startswith("CTA"):
            section = "h"; continue
        if not s:
            continue
        if section == "p":
            primary.append(s)
        elif section == "h":
            headline.append(s)
    skip = {"this ad has multiple versions", "eu transparency", "open dropdown",
            "quality", "hd", "sd", "0:01", "0:15"}
    primary = [x for x in primary if x.lower() not in skip]
    headline = [x for x in headline if x.lower() not in skip]
    return " / ".join(primary), " / ".join(headline)


def make_sheet(path, lang, base, frames, cols, thumb_w, out_jpg):
    dur = ffprobe_dur(path)
    if dur <= 0:
        return False
    # evenly spaced timestamps inside [5%, 95%]
    ts = [dur * (0.05 + 0.90 * i / max(1, frames - 1)) for i in range(frames)]
    tmpdir = tempfile.mkdtemp(prefix="csheet_")
    thumbs = []
    for i, t in enumerate(ts):
        fp = os.path.join(tmpdir, f"f{i}.jpg")
        if extract_frame(path, t, fp):
            thumbs.append((t, fp))
    if not thumbs:
        return False

    im0 = Image.open(thumbs[0][1])
    ar = im0.height / im0.width
    tw, th = thumb_w, int(thumb_w * ar)
    rows = (len(thumbs) + cols - 1) // cols
    pad, header = 8, 64
    W = cols * tw + (cols + 1) * pad
    Hh = header + rows * (th + 22) + (rows + 1) * pad
    sheet = Image.new("RGB", (W, Hh), (24, 24, 28))
    draw = ImageDraw.Draw(sheet)
    draw.text((pad, 10), f"{lang}  |  {base}", font=font(28), fill=(255, 255, 255))
    draw.text((pad, 40), f"duration {dur:.1f}s", font=font(18), fill=(160, 160, 170))

    for idx, (t, fp) in enumerate(thumbs):
        r, c = divmod(idx, cols)
        x = pad + c * (tw + pad)
        y = header + pad + r * (th + 22 + pad)
        try:
            im = Image.open(fp).convert("RGB").resize((tw, th), Image.LANCZOS)
            sheet.paste(im, (x, y))
        except Exception:
            continue
        draw.text((x + 4, y + th + 2), f"{t:.1f}s", font=font(16), fill=(200, 200, 210))

    sheet.save(out_jpg, quality=88)
    for _, fp in thumbs:
        try: os.remove(fp)
        except Exception: pass
    try: os.rmdir(tmpdir)
    except Exception: pass
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--frames", type=int, default=6)
    ap.add_argument("--cols", type=int, default=3)
    ap.add_argument("--width", type=int, default=360)
    ap.add_argument("--out", default="_campaign_prep")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()

    root = os.path.abspath(a.root)
    outdir = os.path.join(root, a.out)
    os.makedirs(outdir, exist_ok=True)

    vids = []
    for lang in sorted(os.listdir(root)):
        ld = os.path.join(root, lang)
        if not os.path.isdir(ld) or lang.startswith("_"):
            continue
        for mp4 in sorted(glob.glob(os.path.join(ld, "*.mp4"))):
            vids.append((lang, mp4))
    if a.limit:
        vids = vids[:a.limit]

    print(f"[prep] {len(vids)} videos under {root}")
    manifest = ["# Campaign split — input manifest", ""]
    done = 0
    for lang, mp4 in vids:
        base = os.path.splitext(os.path.basename(mp4))[0]
        sheet = os.path.join(outdir, f"{lang}__{base}.jpg")
        if not (os.path.isfile(sheet) and os.path.getsize(sheet) > 0):
            ok = make_sheet(mp4, lang, base, a.frames, a.cols, a.width, sheet)
            status = "OK " if ok else "ERR"
        else:
            status = "skip"
        primary, headline = parse_txt(os.path.splitext(mp4)[0] + ".txt")
        done += 1
        print(f"[{done}/{len(vids)}] {status} {lang}__{base}")
        manifest.append(f"## {lang}__{base}")
        manifest.append(f"- sheet: `{os.path.basename(sheet)}`")
        manifest.append(f"- primary: {primary or '(none)'}")
        manifest.append(f"- headline: {headline or '(none)'}")
        manifest.append("")

    open(os.path.join(outdir, "manifest.md"), "w", encoding="utf-8").write("\n".join(manifest))
    print(f"[prep] done. sheets + manifest.md in {outdir}")


if __name__ == "__main__":
    main()
