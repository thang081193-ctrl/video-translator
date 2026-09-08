"""End-card detection by SUBJECT PRESENCE, for cards that open on a bright burst.

Why a third detector when brand_pass already has two:

`_detect_endcard_start_v2` compares tail frames to the LAST frame of the clip, so
it finds where the card SETTLES. When a card opens on a bright animated burst
(fire, glitch bars, particle wipe) those opening frames look nothing like the
settled card, so v2 places the boundary after the burst and the cut lands LATE --
leaving a second of competitor branding at the head of the tail. `_scan_freeze`
fails the same way for the same reason: a burst is not still.

This detector ignores the card's animation entirely and asks a different question:
is a PERSON on screen? Ad body footage is a person filling the frame; an app
end-card is graphics with, at most, a small icon. Measured on a 25-clip scraped
batch (AI-companion ads, mixed live-action / 3D / anime):

    body     skin fraction 0.51 .. 0.95
    end-card skin fraction 0.00 .. 0.044

so SKIN_THRESH=0.15 sits in a very wide empty gap. 23/25 correct on the first
pass; both misses were predictable and are described in `detect()` below.

This is a COMPLEMENT, not a replacement -- it is blind to cards that follow
footage with no people in it. Cross-check the two and take the earlier boundary
only when `confident` is True.
"""

from __future__ import annotations

from dataclasses import dataclass

from pipeline.logger import get_logger

log = get_logger("EndcardSubject")

SKIN_THRESH = 0.15      # measured gap is 0.044 .. 0.51; this sits mid-gap
DARK_MEAN = 38.0        # below this a frame is a fade, not content
MAX_FADE_WALKBACK = 1.2  # seconds; never eat more than this off the tail


@dataclass
class SubjectCut:
    status: str                 # "ok" | "none" | "unavail"
    cut: float | None = None
    body_frac: float = 0.0      # skin fraction at the last body frame
    walked_back: float = 0.0    # seconds trimmed off a fade-to-black


def _skin_fraction(frame) -> float:
    """Fraction of pixels in the standard YCrCb skin locus.

    Deliberately colour-space based rather than a face detector: it survives
    profile shots, motion blur, partial crops and stylised (3D / anime) faces,
    all of which are common in scraped ad creative and all of which break
    Haar/DNN face detection.
    """
    import cv2
    ycc = cv2.cvtColor(frame, cv2.COLOR_BGR2YCrCb)
    y, cr, cb = ycc[:, :, 0], ycc[:, :, 1], ycc[:, :, 2]
    return float(
        ((cr >= 133) & (cr <= 173) & (cb >= 77) & (cb <= 127) & (y > 55)).mean()
    )


def detect(video_path: str, *, skin_thresh: float = SKIN_THRESH,
           probe_step_s: float = 0.1, trim_fade: bool = True) -> SubjectCut:
    """Find where the subject leaves frame for good; that is the card boundary.

    Walks BACKWARDS from EOF so a clip whose body contains graphic inserts
    (chat-UI cutaways, screen recordings) is not cut at the first insert.

    Known blind spots, both of which return a cut that is too early rather than
    too late -- always eyeball a before/after montage across the batch:
      * dark scenes with a fully-covered subject (measured: a goth model in
        long-sleeve black in a dim neon room read 0.04 and tripped the gate)
      * stylised characters on saturated backgrounds (an anime figure against
        hot pink read below threshold for the whole clip -> "none")
    """
    try:
        import cv2
    except ImportError:
        return SubjectCut("unavail")

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return SubjectCut("unavail")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if n <= 1:
        cap.release()
        return SubjectCut("unavail")
    dur = n / fps

    step = max(1, int(round(fps * probe_step_s)))
    last_body, body_frac = None, 0.0
    for i in range(n - 1, 0, -step):
        cap.set(cv2.CAP_PROP_POS_FRAMES, i)
        ok, fr = cap.read()
        if not ok:
            continue
        f = _skin_fraction(fr)
        if f > skin_thresh:
            last_body, body_frac = i, f
            break

    if last_body is None:
        cap.release()
        log.debug(f"{video_path}: no subject found in any frame")
        return SubjectCut("none")

    cut = min(dur, (last_body + 1) / fps + 2.0 / fps)

    walked = 0.0
    if trim_fade:
        # Cards are usually preceded by a short fade; without this the clip ends
        # on a black frame, which reads as a broken render.
        t = cut
        while t > cut - MAX_FADE_WALKBACK and t > 1.0 / fps:
            cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, int(t * fps) - 1))
            ok, fr = cap.read()
            if not ok:
                break
            if cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY).mean() >= DARK_MEAN:
                break
            t -= 1.0 / fps
        walked = round(cut - t, 3)
        cut = t
    cap.release()

    if cut >= dur - 0.05:
        return SubjectCut("none", body_frac=body_frac)
    return SubjectCut("ok", round(cut, 2), body_frac, walked)


def cross_check(video_path: str, v2_cut: float | None,
                src_dur: float) -> tuple[float | None, str]:
    """Reconcile a frame-matching cut with the subject cut.

    Prefers the EARLIER boundary when the two disagree by more than 0.4 s and the
    subject detector is confident, because a late cut ships competitor branding
    while an early cut only loses a beat of footage.

    Returns (cut, reason) — `reason` is for logging, not control flow.
    """
    s = detect(video_path)
    if s.status != "ok":
        return v2_cut, f"subject={s.status}, keeping frame-match cut"
    if v2_cut is None:
        return s.cut, "frame-match found no card; using subject cut"
    delta = v2_cut - s.cut
    if delta > 0.4 and s.body_frac > 0.35:
        return s.cut, (f"subject cut {s.cut:.2f}s is {delta:.2f}s earlier than "
                       f"frame-match {v2_cut:.2f}s (burst-opening card?)")
    if delta < -0.4:
        return v2_cut, (f"subject cut {s.cut:.2f}s is later than frame-match "
                        f"{v2_cut:.2f}s; deferring to frame-match")
    return v2_cut, "detectors agree"
