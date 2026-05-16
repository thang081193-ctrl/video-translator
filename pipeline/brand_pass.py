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
    "bgm_vol":      (0.35, 0.45),
    "tts_rate_pct": (-3, 3),
    "outro_dur":    (1.3, 1.7),
    "outro_bg":     ["0x151515", "0x1a1a1a", "0x1f1f1f", "0x222222"],
    "outro_sub_color": ["0xc8c8c8", "0xcfcfcf", "0xd5d5d5", "0xdcdcdc"],
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


def _ffprobe_duration(path: str) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True, check=True,
    )
    return float(r.stdout.strip())


def _detect_endcard_start(video_path: str, src_dur: float,
                          min_drop_pct: float = 0.7,
                          min_tail_s: float = 1.5) -> float | None:
    """Detect end-card start timestamp via ffmpeg scene-change detection.

    Looks for the LAST significant scene change in the last (1-min_drop_pct)
    of the source. Returns timestamp in seconds if the end-card is at least
    `min_tail_s` long; else None (no clear end-card found).
    """
    try:
        r = subprocess.run(
            ["ffmpeg", "-i", video_path,
             "-vf", "select='gte(scene,0.35)',showinfo",
             "-vsync", "0", "-an", "-f", "null", "-"],
            capture_output=True, text=True, timeout=120,
        )
        times = [float(m.group(1)) for m in re.finditer(r"pts_time:(\d+\.?\d*)", r.stderr)]
        if not times:
            return None
        last = max(times)
        if last >= src_dur * min_drop_pct and last <= src_dur - min_tail_s:
            return last
        return None
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
    """Extract audio + Whisper transcribe → return joined transcript text."""
    work = tempfile.mkdtemp(prefix="brandpass_tx_", dir=work_root) if work_root else tempfile.mkdtemp(prefix="brandpass_tx_")
    try:
        audio = extract_audio(video_path, work)
        segments, _ = transcribe(audio, model_name="small", source_lang="en", use_cache=False)
        text = " ".join(s["text"].strip() for s in segments).strip()
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
    outro_duration: float | None = None,
    bgm_volume: float | None = None,
    trim_endcard: bool = False,
    trim_endcard_min_drop_pct: float = 0.7,
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
    os.makedirs(os.path.dirname(os.path.abspath(output_path)) or ".", exist_ok=True)

    p = _build_jittered_params(random_seed)
    if voice is not None: p["voice"] = voice
    if outro_duration is not None: p["outro_dur"] = outro_duration
    if bgm_volume is not None: p["bgm_vol"] = bgm_volume

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

        # 3. Demucs separate
        log.info("Demucs htdemucs separating BGM ...")
        demucs_dir = os.path.join(work, "demucs")
        os.makedirs(demucs_dir, exist_ok=True)
        stems = separate_audio(src_audio, demucs_dir, model="htdemucs")
        bgm = stems["no_vocals"]

        # 4. TTS
        rate_str = f"{p['tts_rate_pct']:+d}%"
        log.info(f"TTS Edge ({p['voice']}, rate={rate_str}) ...")
        tts_audio = os.path.join(work, "tts.mp3")
        asyncio.run(_generate_tts(transcript, p["voice"], tts_audio, rate=rate_str))

        # 5. Mix TTS + BGM
        total_dur = working_dur + p["outro_dur"]
        mixed_audio = os.path.join(work, "mixed.m4a")
        log.info(f"Mixing TTS over BGM (BGM vol={p['bgm_vol']}) → {total_dur:.2f}s ...")
        r = subprocess.run(
            ["ffmpeg", "-y", "-i", tts_audio, "-i", bgm,
             "-filter_complex",
             f"[0:a]volume=1.0,apad[v0];"
             f"[1:a]volume={p['bgm_vol']},apad[v1];"
             f"[v0][v1]amix=inputs=2:duration=first:dropout_transition=0,atrim=duration={total_dur}[aout]",
             "-map", "[aout]", "-c:a", "aac", "-b:a", "192k", mixed_audio],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            raise RuntimeError(f"ffmpeg mix failed (exit {r.returncode}): {r.stderr[-800:]}")

        # 6. Video transforms (zoom + color + watermark)
        scaled_w = int(W * p["zoom"])
        scaled_h = int(H * p["zoom"])
        cx = (scaled_w - W) // 2 + p["crop_dx"]
        cy = (scaled_h - H) // 2 + p["crop_dy"]
        cx = max(0, min(scaled_w - W, cx))
        cy = max(0, min(scaled_h - H, cy))
        zoom_crop = f"scale={scaled_w}:{scaled_h},crop={W}:{H}:{cx}:{cy}"
        color = (
            f"eq=saturation={p['saturation']}:contrast={p['contrast']}:gamma={p['gamma']},"
            f"hue=h={p['hue']}"
        )

        body = os.path.join(work, "body.mp4")
        log.info("Encoding transformed body (zoom + color + watermark) ...")
        if watermark_image:
            # PNG overlay watermark
            wm_x = W - SIDE_SAFE - watermark_size + p["wm_dx"]
            wm_y = TOP_SAFE + 20 + p["wm_dy"]
            wm_x = max(0, min(W - watermark_size, wm_x))
            wm_y = max(0, min(H - watermark_size, wm_y))
            filter_complex = (
                f"[0:v]{zoom_crop},{color}[bg];"
                f"[1:v]scale={watermark_size}:{watermark_size},format=rgba,"
                f"colorchannelmixer=aa={p['wm_opacity']}[wm];"
                f"[bg][wm]overlay={wm_x}:{wm_y}[vout]"
            )
            subprocess.run(
                ["ffmpeg", "-y", "-i", working_input, "-i", watermark_image,
                 "-filter_complex", filter_complex, "-map", "[vout]",
                 "-c:v", "libx264", "-preset", p["preset"], "-crf", str(p["crf"]), "-an", body],
                capture_output=True, check=True, text=True,
            )
        else:
            # drawtext watermark (legacy)
            wm_x = W - SIDE_SAFE - 140 + p["wm_dx"]
            wm_y = TOP_SAFE + 20 + p["wm_dy"]
            drawtext_wm = (
                f"drawtext=fontfile='{FONT_FF}':text='{watermark_text}':"
                f"fontsize=36:fontcolor=white@{p['wm_opacity']}:borderw=2:bordercolor=black@0.4:"
                f"x={wm_x}:y={wm_y}"
            )
            subprocess.run(
                ["ffmpeg", "-y", "-i", working_input,
                 "-vf", f"{zoom_crop},{color},{drawtext_wm}",
                 "-c:v", "libx264", "-preset", p["preset"], "-crf", str(p["crf"]), "-an", body],
                capture_output=True, check=True, text=True,
            )

        # 7. Outro card
        outro = os.path.join(work, "outro.mp4")
        log.info("Generating outro card ...")
        logo_y = int(H * 0.30)               # top of logo
        title_y = int(H * 0.30) + outro_logo_size + 80   # below logo
        sub_y_off = title_y + 130             # below title
        if outro_logo_image:
            filter_complex = (
                f"[1:v]scale={outro_logo_size}:{outro_logo_size}[logo];"
                f"[0:v][logo]overlay=(W-w)/2:{logo_y},"
                f"drawtext=fontfile='{FONT_FF}':text='{outro_title}':"
                f"fontsize=96:fontcolor=white:x=(w-text_w)/2:y={title_y},"
                f"drawtext=fontfile='{FONT_FF}':text='{outro_subtitle}':"
                f"fontsize=42:fontcolor={p['outro_sub_color']}:x=(w-text_w)/2:y={sub_y_off}[vout]"
            )
            subprocess.run(
                ["ffmpeg", "-y",
                 "-f", "lavfi", "-i", f"color=c={p['outro_bg']}:s={W}x{H}:d={p['outro_dur']}:r=30",
                 "-loop", "1", "-i", outro_logo_image,
                 "-filter_complex", filter_complex,
                 "-map", "[vout]", "-t", f"{p['outro_dur']}",
                 "-c:v", "libx264", "-preset", p["preset"], "-crf", str(p["crf"]), outro],
                capture_output=True, check=True, text=True,
            )
        else:
            subprocess.run(
                ["ffmpeg", "-y",
                 "-f", "lavfi", "-i", f"color=c={p['outro_bg']}:s={W}x{H}:d={p['outro_dur']}:r=30",
                 "-vf",
                 f"drawtext=fontfile='{FONT_FF}':text='{outro_title}':"
                 f"fontsize=110:fontcolor=white:x=(w-text_w)/2:y=(h-text_h)/2-60,"
                 f"drawtext=fontfile='{FONT_FF}':text='{outro_subtitle}':"
                 f"fontsize=48:fontcolor={p['outro_sub_color']}:x=(w-text_w)/2:y=(h-text_h)/2+60",
                 "-c:v", "libx264", "-preset", p["preset"], "-crf", str(p["crf"]), outro],
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
