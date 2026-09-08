# End-cards that open on a bright burst defeat both existing detectors

Measured 2026-09-08 on a 25-clip scraped batch of AI-companion ads (one advertiser,
mixed live-action / 3D / anime footage, 360x640 and 360x360).

## Symptom

Both detectors in `pipeline/brand_pass.py` cut **late** — by 0.5 s to 3.4 s — leaving
a chunk of competitor branding at the head of the trimmed tail.

## Cause

The card is a fixed ~6.0 s template that **opens on a full-frame fire/glitch burst**
and only then settles into a dark card with the app icon and store badges.

* `_detect_endcard_start_v2` compares tail frames to the **last** frame. The burst
  frames look nothing like the settled card, so the boundary lands after the burst.
* `_scan_freeze` needs stillness. A burst is not still, so it fails identically.

Neither is a bug in the detectors — both answer "where does the card settle?", which
is simply a different question from "where does the body end?".

## What worked

Ask about the **subject**, not the card: ad body footage is a person filling the
frame; the card is graphics plus a small icon. `pipeline/endcard_subject.py`
implements this using the YCrCb skin locus.

Measured separation across the batch:

| | skin fraction |
|---|---|
| body footage | 0.51 .. 0.95 |
| end-card | 0.00 .. 0.044 |

`SKIN_THRESH = 0.15` sits mid-gap. **23/25 correct on the first pass.**

Colour-space skin detection was chosen over face detection on purpose: it survives
profile shots, motion blur, partial crops and stylised (3D / anime) faces, all of
which are common in scraped creative and all of which break Haar/DNN face detectors.

## Known blind spots

Both fail *early* (safe direction), and both were caught by the verification montage:

* **Dark scene, fully-covered subject** — a model in long-sleeve black in a dim neon
  room read 0.04 and tripped the gate at 3.8 s on a 29.2 s clip.
* **Stylised character on a saturated background** — an anime figure against hot pink
  never crossed the threshold, returning `"none"`.

Both clips still matched the batch's fixed 6.0 s card length, which is the cheap
recovery: **when a batch uses one card template, `cut = dur - card_len` recovers any
clip the detector misses.** Confirm the template by checking that most of the batch
agrees — here 22/25 landed within 5.95..6.02 s.

## Process notes

* Always render a **before/after montage for the whole batch** and look at it before
  encoding anything. Here it caught both detector misses *and* four clips left ending
  on a black frame.
* After cutting, walk back off any **fade-to-black** (`mean gray < 38`, capped at
  1.2 s). 7/25 clips needed 0.04–0.08 s of walkback; without it the render ends on a
  black frame.
* A scraped batch's corner watermark is **not at a fixed position** — in this batch 20
  clips had it top-left, 4 bottom-left, 1 inside a banner. Multi-scale
  `cv2.matchTemplate` (scale 0.55–1.85, `TM_CCOEFF_NORMED`, probed at 3 timestamps)
  resolved 24/25; treat score < 0.65 as "look at it by hand".

## Wiring in

`endcard_subject.cross_check()` reconciles a frame-matching cut with the subject cut
and prefers the earlier boundary when they disagree by > 0.4 s. It is **not** wired
into `_detect_endcard_start` yet — doing so changes trim behaviour for every batch, so
it needs a regression pass over previously-shipped batches first.
