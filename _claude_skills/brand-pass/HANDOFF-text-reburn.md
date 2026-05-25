# Handoff — Text Re-burn for brand-pass

**Status:** brand-pass v3 ships with audio fingerprint + visual pHash diversity. Creative DNA (overlay text + scenes) is still source-identical, which is the largest remaining Meta detection risk. This doc is the runbook for the next phase: OCR-detect overlay text and re-burn with new copy.

## Why this matters

Meta's duplicate-ad detection clusters videos by overlay text + visual frames + hook timing. If competitor ad has `"I asked AI to design my garden 🏡"` and we re-upload with brand-pass, the audio fingerprint passes Rights Manager but the **text + visual** still cluster with the source. Result: low delivery, "duplicate creative" flag, or rejection at scale.

Audio bypass = solved (BGM pool). Visual fingerprint = partially solved (LUT + zoom jitter, but core scenes identical). **Text bypass = unsolved.** This is the highest-value next step.

## First attempt — what failed (May 2026)

`D:/tmp/text_reburn_test.py` (session artifact, deleted) — straightforward OCR + ffmpeg drawbox+drawtext on EN_2405_02 (garden video). Output had 3 issues:

1. **Text overflow** — new caption "Watch AI redesign my backyard" was longer than the original pill width. Drawbox covered original area; drawtext rendered new text WIDER than the box → chữ tràn ra ngoài pill, bị cắt 2 đầu (`"tch AI redesign my backy"`).
2. **Original "Before / after" not masked** — only drew new text on top, didn't drawbox white pill underneath → both old and new visible (ghost overlay).
3. **Wrong cluster picked as secondary** — OCR also detected in-app text ("Cartoon", "Mid Century", "Modern" — style buttons inside the app screen recording). Sort by bbox-area picked one of those as the second overlay to replace, instead of "Before / after".

Sample broken output frame is gone, but the failure modes are documented above. Don't repeat them.

## Iterate plan — 3 critical fixes

### Fix 1 — Auto-fit pill width to new text

Measure new text width BEFORE building the ffmpeg filter, resize the pill to accommodate.

```python
from PIL import ImageFont
font = ImageFont.truetype(r"C:\Windows\Fonts\seguisb.ttf", size=fs)
# Pillow ≥10: use getbbox; old: getsize
bbox = font.getbbox(new_text)
text_w = bbox[2] - bbox[0]
text_h = bbox[3] - bbox[1]
pill_w = text_w + 2 * pad_x        # pad_x ≈ 28
pill_h = text_h + 2 * pad_y        # pad_y ≈ 18
pill_x = original_center_x - pill_w / 2     # center on original's center
pill_y = original_top_y                       # keep top edge aligned
```

If `pill_x < 24` or `pill_x + pill_w > frame_w - 24`, shrink font size by 1 step and re-measure. Iterate down until it fits within safe margin (Reels safe zone ~24 px from edges).

### Fix 2 — Cluster filtering (only overlay text, never in-app text)

OCR returns EVERY text region including in-app UI. Filter rules in priority order:

1. **Position**: `y_center < 0.50 * frame_h` (top half) OR `y_center > 0.85 * frame_h` (bottom CTA zone). In-app UI typically lives in 0.50-0.85 range.
2. **Persistence**: must appear in ≥ 2 of 3 sampled frames at the same position (in-app text changes between frames; overlay text stays).
3. **Background**: check pixel variance in the bbox. A solid-color pill (white/black) has very low variance. In-app text is on natural content (high variance). Compute `cv2.Laplacian(roi).var()` — if < ~50 → solid pill → likely overlay.
4. **Whitelist size**: bbox height > 4% of frame_h AND < 12% (overlay captions are larger than UI labels but not full-screen).

Test on EN_2405_02 in particular — it must reject "Cartoon/Modern/Bohemian" style-button cluster but keep "I asked AI..." and "Before / after".

### Fix 3 — Mask EVERY qualifying cluster, then redraw

Don't assume the secondary text is white-on-content (no mask needed). Some templates use a black pill, some have shadow. **Always** drawbox under the new text:

- For each qualifying cluster: drawbox(filled, opaque, color sampled from original bbox center pixel) → drawtext(centered, font matched to original height)
- Sample the original bbox center pixel via OpenCV to get the pill color (most often `#FFFFFF` white, sometimes `#1A1A1A` near-black). Match text color to inverse (black-on-white or white-on-black).

```python
roi = source_frame[y:y+h, x:x+w]
bg_color = tuple(int(c) for c in roi.mean(axis=(0,1)))    # RGB average
text_color = "white" if sum(bg_color) < 384 else "black"
```

## Suggested copy variants (for replacement text)

Don't pull from LLM at runtime — use a curated pool. The text must be:
- Same emotional register as source (curiosity hook, before/after promise)
- Within ±20% character count of original (so pill resize is minimal)
- Free of trademarked phrasing
- DecoAI brand voice

Starter pool (12 variants for the "I asked AI to design X" template — pick by RNG keyed on source filename):

```
1.  "Let AI restyle my living room"
2.  "Watch AI redesign this space"
3.  "I let AI plan my home reno"
4.  "AI redid my bedroom — wild result"
5.  "What if AI styled your home"
6.  "AI gave my space a glow-up"
7.  "Tried AI on my dead corner"
8.  "AI vs. my actual living room"
9.  "Letting AI decorate for me"
10. "AI's take on my bare wall"
11. "Home design but AI did it"
12. "Asked AI to fix my room"
```

For the "Before / after" sub-label, keep it as-is (universal, generic — Meta doesn't cluster on it).

## Implementation outline (drop into a new file)

Suggested location: `_claude_skills/brand-pass/text_reburn.py` (new helper) + import from `pipeline/brand_pass.py` before the encode step.

```python
def detect_overlay_text(video_path, n_frames=3):
    """Sample frames, OCR each, return list of qualifying overlay clusters."""
    # 1. ffmpeg sample n_frames evenly
    # 2. EasyOCR per frame
    # 3. Cluster by bbox center (within 50px)
    # 4. Apply filters (position, persistence, bg variance, size)
    # 5. Return list of dicts with {bbox, original_text, bg_color, font_size}

def build_reburn_filter(detections, copy_pool, seed):
    """Build ffmpeg -vf filter to mask + redraw each detection."""
    # 1. RNG-seeded pick from copy_pool per detection
    # 2. Pillow measure text width at font_size
    # 3. Iterate-shrink font if pill exceeds safe margin
    # 4. Build drawbox + drawtext per detection
    # 5. Chain with comma, return single filter string
```

Wire into `brand_pass_video()` after step 6 (video transforms) and before step 7 (watermark) — or compose into the same `-vf` chain to avoid extra encode pass.

## Test harness — minimum viable verification

Before integrating into pipeline, verify on these source files (each represents a failure mode):

| File | Source aspect | Overlay text count | What it tests |
|---|---|---|---|
| `EN_2405_02.mp4` (720x900, 4:5) | "I asked AI to design my garden" + "Before/after" + in-app style buttons | Cluster filtering must reject style buttons |
| `EN_2405_13.mp4` (720x720, 1:1) | Single overlay over varied content | Auto-fit pill on different aspect |
| `EN_2405_01.mp4` (720x1280, 9:16 native) | Multiple overlays in split-screen | Persistence filter must not pick split-screen labels |

For each: render output → extract 3 frames at 25%/50%/75% → eyeball for:
- New text fits cleanly inside pill (no overflow, no cropping)
- No ghost overlay (original fully masked)
- In-app UI / style buttons untouched
- Color scheme matches source pill style

## Dependencies (add to repo .venv)

```bash
pip install easyocr pillow opencv-python
```

`easyocr` is already used by `pipeline/ocr.py` in the project. `pillow` for text measurement. `opencv-python` (cv2) for color sampling.

## Out-of-scope for this phase (don't expand)

- Multi-language OCR (only English for now — DecoAI's market is EN)
- Animated text (text that slides/fades in mid-video). For v1, snapshot-detect on a few keyframes and assume static throughout. Animated overlays are <5% of ad templates.
- Auto-translate the new copy for non-EN markets. Keep it EN-only for first ship.
- Inpainting the original text region. **Don't try** — ghosting always looks worse than a clean opaque pill overlay.

## Quality target

After this phase:
- ~80% of source ads with overlay text → clean text-swapped output, ready for re-upload
- ~15% need manual touch-up (font mismatch or rare layout)
- ~5% should be auto-rejected (multi-language overlay, rotated text, vertical text strips)

Add a `--skip-on-reburn-fail` flag to brand-pass so unrecognized layouts fall back to the un-reburned output rather than crashing.

## Where this fits in the pipeline

```
Source MP4
  → end-card trim
  → speech detect (Whisper logp > -0.5)
  → audio path (TTS or passthrough or BGM-pool swap)
  → side-blur strip + Laplacian verify
  → effective-aspect branch (Branch A fill or Branch B blur-pad)
  → color LUT
  → ★ TEXT REBURN (this phase) ★
  → watermark
  → outro card
  → encode (jittered CRF + preset)
```

Insert between color LUT and watermark — the LUT may shift colors which we want to sample from the LUT'd frame, not the raw source.

## Estimated effort

- Detect + cluster filtering: 4-6 hours (most of the work — getting filters right)
- Auto-fit pill rendering: 2-3 hours
- Wire into brand_pass.py: 1 hour
- Test harness + iteration on 3 source files: 2-3 hours

Total ~10-13 hours focused work, plus copy-pool curation per ad theme.
