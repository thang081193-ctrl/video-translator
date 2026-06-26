# Post-mortem — 2026-06-26 Vast 3-app batch: missing-outro defect + QA/cleanup mistakes

Batch: TradeBuddy / DecoAI / ScoreDeck, voiced (V2) + BGM-only (V1) packs, rendered on
rented Vast.ai GPUs, delivered locally to `_deliverables_2606/`. 1869 mp4 total.

Outcome: deliverables are correct **after remediation**. This doc records the mistakes so
they never recur. Read before any "render-on-rented-box → QA → destroy → deliver" run.

---

## Mistake 1 (CRITICAL): destroyed the rented instances before VISUAL QA

**What happened.** The destroy gate was run on AUDIO QA only — `qa_voice_mix.py --whisper`
(voice present / language / loudness), file integrity (ffprobe), and file counts. All passed,
so both Vast instances were destroyed. That deleted the **only copy of the source videos**.

**The defect this missed.** ScoreDeck (100%) and TradeBuddy (93%) were **missing the brand
outro** at the end. The user caught it after delivery. Because the source was gone with the
boxes, a clean re-run was impossible — recovery was a lossy local patch (trim + re-append
outro on the finished files).

**Rule — DESTROY GATE = audio QA + VISUAL QA + source preserved.**
- Build an **end-frame montage of EVERY pack** (last frame of a representative sample across
  every lang/folder) and eyeball that the brand outro + branding are correct. Audio QA never
  detects a missing/competitor outro.
- Never `vastai destroy` until the visual check passes **and** the deliverables are downloaded
  **and** the source videos exist somewhere other than the ephemeral box.
- Prefer: download + visual QA + (ideally) user sign-off, THEN destroy.

## Mistake 2: `retrim_endcards` strips the appended brand outro

The V2/V1 chain ran `retrim_endcards.py all $DST` **after** brandpass had already done
`--trim-endcard --outro-video`. The post-brandpass retrim detected the content→outro
transition as an "end-card" and trimmed the brand outro off — **inconsistently** (TradeBuddy
lost it on 93% of files, DecoAI lost it on 0%, depending on whether the transition crossed the
scene threshold).

**Rule.** Do **not** run a separate `retrim_endcards` after brandpass has appended the outro.
`brandpass --trim-endcard` already removes the *source* competitor end-card. If a separate
retrim must run, make it outro-aware (never trim the trailing brand outro). After any run,
verify the outro survived (end-frame check).

## Mistake 3: broken/truncated outro asset shipped silently

The ScoreDeck `outro.mp4` on the box was **138 KB / truncated**; the real local original was
**400 KB / 3.0 s**. brandpass could not append the broken file, so ScoreDeck outputs kept the
*competitor* end-cards and had no ScoreDeck outro.

**Rule.** Before a render run, validate every outro/asset: `ffprobe` duration > 0, plays, and a
sane size. A sub-~200 KB "outro.mp4" is a red flag — compare against the source-of-truth copy.

## Mistake 4: deleted the job's working dir + renamed outputs while jobs might still be running

During cleanup, `rm -rf _patch` (the running patch jobs' input list, outro, done-log, and
completion monitor) and renamed the output folders **before confirming the background jobs had
finished**. It turned out the jobs had already completed (no real damage — `os.replace` is
atomic, no half-written files), but the done-logs/monitor were deleted before their completion
status was read, causing a false alarm + wasted recovery effort.

**Rule.** Before deleting a job's working dir or renaming its output targets, confirm the
background jobs are done — check the completion marker / that `ffmpeg`/worker procs are gone.
Read the completion status BEFORE deleting the logs/monitor that report it.

## Not a bug (avoid the false alarm): `--expect-voice` flags BGM masters as NO-SPEECH

`qa_voice_mix.py --whisper --expect-voice` on a `_out_v2` tree ALWAYS flags the
`BGM_UNIVERSAL/` music-only masters as NO-SPEECH — they are intentionally voiceless (the
reusable VO source). Verify: NO-SPEECH count == BGM_UNIVERSAL master count AND zero NO-SPEECH
under `VOICED_*/`. Then it's not a failure.

---

## What worked (keep doing)
- DecoAI was 100% clean (outro on all 770) — same pipeline, so the defect was asset/retrim
  specific, not universal.
- Migrating to a 64-vCPU box when the 2.7-vCPU box couldn't hit the deadline (render is
  CPU-bound, not GPU-bound — pick boxes by `cpu_cores_effective`).
- Local patch recovery without source: `append_outro_list.py` (append) and
  `score_trim_append.py` (trim end-card + append), concat-filter re-encode, idempotent
  done-log. Kept in the batch tooling.

## One-line gate to remember
**No `vastai destroy` until: deliverables downloaded + end-frame montage of every pack
eyeballed (outro/branding correct) + source preserved off-box.**
