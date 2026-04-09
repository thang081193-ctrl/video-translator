# Phase Status Tracker

This file is the single source of truth for implementation progress across sessions.

## 1) How To Use In Every New Session
1. Read this file first.
2. Read the latest completed phase report in `docs/execution/reports/`.
3. Read `docs/execution/phase-report-template.md` before writing any new phase report.
4. At the start of a phase, fill the `In Progress` row in the table below.
5. At the end of a phase, add a full report file and update this table.
6. Do not start the next phase unless the current phase gates are PASS or have an explicit approved exception.

## 2) Current Program Snapshot
- Program: Video Translator — Phase 5 Tech Debt Cleanup
- Last Updated: 2026-04-08
- Current Owner: Claude + User
- Current Branch: main
- Current Focus: P6.A + P6.B Done (409/409 tests pass). P6.C In Progress (cost / GPU mgmt). Post-merge manual action: flip GHCR package visibility to Public after first GHA build.
- Overall Health: Green
- Key Blockers: None
- Next Milestone Date: TBD

## 3) Phase Backlog Status
| Phase ID | Phase Name | Status | Owner | Start Date | End Date | QA Gate | Regression | Report Link | Notes |
|---|---|---|---|---|---|---|---|---|---|
| P3.0 | Foundation: Standards + Tracking | Done | Claude+User | 2026-04-06 | 2026-04-06 | N/A | N/A | — | Docs-only: phase-status, report template, dev-standards, architecture-rules |
| P3.1 | Config Centralization + Structured Logging | Done | Claude+User | 2026-04-06 | 2026-04-06 | PASS | PASS | — | Config dataclass + logger module + all 11 pipeline modules migrated, 0 print() left |
| P3.2 | API Provider Abstraction | Done | Claude+User | 2026-04-06 | 2026-04-06 | PASS | PASS | — | 6 files: providers/ package (base, grok, gemini, vertex, factory), translate.py rewritten |
| P3.3 | Web App Modularization | Done | Claude+User | 2026-04-06 | 2026-04-06 | PASS | PASS | — | 5 files: web/ package (app, models, routes, worker, pipeline_runner), web_app.py → thin entry |
| P3.4 | Large Module Splitting (OCR + Dub) | Done | Claude+User | 2026-04-06 | 2026-04-06 | PASS | PASS | — | OCR 819→4 files (detector/grouper/translator/filter), Dub 424→3 files (tts/mixer/separator) |
| P3.5 | Error Handling & Resource Safety | Done | Claude+User | 2026-04-06 | 2026-04-06 | PASS | N/A | — | errors.py (3 exception classes) + utils.py (TempDir, disk check, retry decorator) |
| P3.6 | Testing Infrastructure | Done | Claude+User | 2026-04-06 | 2026-04-06 | PASS | N/A | — | pytest, 60 tests, 5 test files, 100% pass. requirements-dev.txt |
| P3.7 | Docker & Deployment Hardening | Done | Claude+User | 2026-04-06 | 2026-04-06 | PASS | N/A | — | Dockerfile fix, /api/health endpoint, HEALTHCHECK directive |
| **P4.0** | **Workflow Skill Enforcement (META)** | Done | Claude+User | 2026-04-07 | 2026-04-07 | N/A | N/A | — | phase-workflow.md skill + dev-standards Pillar 3 update + architecture-rules Rule 3a/3b |
| **P4.1** | **Language Registry (Single SOT) + CLI drift fix** | Done | Claude+User | 2026-04-07 | 2026-04-07 | PASS | PASS | — | pipeline/languages.py with LanguageSpec; consolidates 3 SOT issue; **fixes P3.1 miss** in video_translator.py:158 |
| **P4.2** | **OCR Font Hardening** | Done | Claude+User | 2026-04-07 | 2026-04-07 | PASS | PASS | — | FONT_MAP for 6 new scripts (bn/ur/fa/mr/te/ta) + Greek; glyph coverage warnings; Dockerfile Noto packages |
| **P4.3** | **Validation & Voice Verification** | Done | Claude+User | 2026-04-07 | 2026-04-07 | PASS | PASS | — | scripts/verify_edge_tts_voices.py + early API/CLI validation; argparse choices |
| **P4.4** | **Test Contract Hardening** | Done | Claude+User | 2026-04-07 | 2026-04-07 | PASS | PASS | — | tests/test_languages_registry.py (parameterized contract tests); tighter test_web_api.py; 60 → 176 → 266 tests |
| **P4.5** | **Add 15 New Languages (top 30)** | Done | Claude+User | 2026-04-07 | 2026-04-07 | PASS | PASS | — | Registry expanded 15 → 30; all 30 voices verified by script; 266/266 tests pass |
| **P4.6** | **Edge Case Test Suite** | Done | Claude+User | 2026-04-07 | 2026-04-07 | PASS | PASS | — | 5 new test files + 71 new tests: pipeline_runner, cross_mode_parity (CLI/Web drift prevention), negative_paths, audio_edge (mocked subprocess), pipeline_integration (full pipeline mocked). 337/337 tests pass |
| **P5.0** | **TempDir Migration + Mixer Refactor** | Done | Claude+User | 2026-04-08 | 2026-04-08 | PASS | PASS | [P5.0](reports/P5.0-tempdir-migration.md) | Replaced `os.makedirs("_tts_temp"/"_demucs_temp")` with `temp_dir()` context manager; extracted 4 helpers from 165 LOC `build_dubbed_audio` (3 under 50 LOC, orchestrator 58). 337/337 tests pass. Rule 5 enforced. |
| **P5.1** | **Custom Exception Adoption + has_audio_track Fix** | Done | Claude+User | 2026-04-08 | 2026-04-08 | PASS | PASS | [P5.1](reports/P5.1-exception-adoption.md) | 22 raises migrated (16 FatalError, 2 TransientError split in grok.py, 1 DegradedError wrapper in separator.py, 3 in translate.py/tts.py after retry exhaustion); `has_audio_track` silent failure fixed (P3.5 miss closed). 337/337 tests pass. Rule 4 enforced. |
| **P6.A** | **Pipeline Throughput / GPU Utilization** | Done | Claude+User | 2026-04-08 | 2026-04-08 | PASS | PASS | [P6.A](reports/P6.A-pipeline-throughput.md) | New `pipeline/gpu_state.py` (sticky GPU/CPU + empty_cuda_cache); Whisper LRU `OrderedDict` cache (medium+large-v3, default size 2); Demucs in-process `Separator` cache + subprocess fallback; EasyOCR `_get_ocr_reader` drops gpu arg; `empty_cuda_cache()` post-job; new `GPUConfig` dataclass with 3 env overrides. 28 new tests, 337 → 365 pass. |
| **P6.B** | **Cold-Start + Setup Automation** | Done | Claude+User | 2026-04-08 | 2026-04-09 | PASS | PASS | [P6.B](reports/P6.B-cold-start.md) | Multi-stage Dockerfile (pre-bakes medium+large-v3+EasyOCR+Demucs+ngrok+cloudflared); GHA workflow → GHCR auto-build; bash installer (9 steps, resume, env-var secrets); dual-tunnel start script (ngrok + cloudflared trycloudflare parallel); 4 skill markdown updates; 44 deploy lint tests. 365 → 409 tests pass. |
| **P6.C** | **Cost / GPU Management** | In Progress | Claude+User | 2026-04-09 | — | — | — | — | `/api/gpu` endpoint; VRAM guard fail-loud; ETA estimator; auto-stop verification + tests |

Status values: `Not Started`, `In Progress`, `Blocked`, `Done`, `Done w/ Exception`.

## 4) Gate Definitions (Mandatory)
- **QA Gate PASS** requires:
  - Senior Review 1 (Architecture) pass for changed code.
  - QA Functional Testing pass for affected features.
  - Senior Review 2 (Sign-off) pass with regression check.
- **Regression PASS** requires:
  - CLI: `python video_translator.py test.mp4 -t vi` produces valid SRT.
  - CLI: `--dub --bgm` produces valid dubbed video.
  - Web: Upload → process → download flow works.
  - Cache: re-run hits cache, faster completion.
- **Exception policy:**
  - If a gate fails but phase must move, note exact exception, owner, risk, and due date for fix.

## 5) Latest Report Index
| Date | Phase ID | Report File | Result | Verified By |
|---|---|---|---|---|
| 2026-04-08 | P5.0 | [P5.0-tempdir-migration.md](reports/P5.0-tempdir-migration.md) | PASS | Claude Opus 4.6 |
| 2026-04-08 | P5.1 | [P5.1-exception-adoption.md](reports/P5.1-exception-adoption.md) | PASS | Claude Opus 4.6 |
| 2026-04-08 | P6.A | [P6.A-pipeline-throughput.md](reports/P6.A-pipeline-throughput.md) | PASS | Claude Opus 4.6 |
| 2026-04-09 | P6.B | [P6.B-cold-start.md](reports/P6.B-cold-start.md) | PASS | Claude Opus 4.6 |

## 6) Session Kickoff Checklist (Copy To New Session)
```text
1) git pull origin main  (must be at d63aa0c or later)
2) Read .claude/handoff-phase-3-4.md  (full session summary)
3) Read docs/execution/phase-status.md  (this file)
4) Read .claude/skills/phase-workflow.md  (5 mandatory gates)
5) Read .claude/skills/dev-standards.md  (3 pillars)
6) Read .claude/skills/architecture-rules.md  (7 rules + Rule 3a/3b)
7) Run pytest tests/ -v  (expect 337 passed)
8) Execute only current phase scope
9) Write phase report using docs/execution/phase-report-template.md
10) Update phase-status.md before ending session
```

## 7) Next Session Priorities (from .claude/handoff-phase-3-4.md)

### P0 — Verification on new machine
- [ ] git pull → at d63aa0c
- [ ] pip install -r requirements.txt -r requirements-dev.txt
- [ ] pytest tests/ → 337 passed
- [ ] python web_app.py → http://localhost:3456 → 30 langs in dropdown
- [ ] grep _DRAWTEXT_UNSAFE → 0 results (drift check)

### P1 — Vast.ai deployment update
- [ ] git pull on Vast.ai instance
- [ ] Verify Noto fonts: dpkg -l | grep fonts-noto
- [ ] Restart server, smoke test 1-2 new langs (bn, tr) end-to-end

### P2 — Pick ONE feature direction (Phase 5):
**Option A — Tech debt cleanup** (low risk, high quality):
- P5.0: TempDir migration (dub/ocr modules)
- P5.1: Custom exception adoption (replace RuntimeError/ValueError)
- P5.2: Retry decorator adoption (replace inline retry loops)
- P5.3: Fix has_audio_track silent failure
- P5.4: Write phase reports for P3/P4 to docs/execution/reports/

**Option B — New features**:
- YouTube URL support (yt-dlp integration) — Medium effort
- Batch processing (folder upload) — Medium effort
- Dual subtitles (orig + translated) — Low effort
- Speaker diarization (pyannote-audio) — High effort
- Voice cloning (ElevenLabs) — Medium effort, paid API
- Real-time progress (WebSocket) — Medium effort

## 8) Known Issues / Tech Debt
- ~~`pipeline/audio.py:23-39` — `has_audio_track()` silently returns False on ANY ffprobe error~~ — **FIXED in P5.1**
- ~~`pipeline/dub/mixer.py` — still uses raw `os.makedirs("_temp")`~~ — **FIXED in P5.0**
- ~~All pipeline modules — still raise `RuntimeError`/`ValueError`~~ — **FIXED in P5.1** (22 raises migrated)
- `pipeline/ocr/detector.py` — not audited in P5.0; may still have raw `os.makedirs` (TBD in future phase)
- `pipeline/translate.py` — still uses inline retry loop instead of `@retry` decorator (Issue #8, future phase). P5.1 changed only the terminal raise types to enable future decorator adoption.
- `video_translator.py:227,259,311` + `web/pipeline_runner.py:270` — dead `_tts_temp`/`_demucs_temp` cleanup loops still present (defensive, harmless; cleanup is a future cosmetic commit)
- Phase reports for P3.0–P4.6 not yet written (only P5.0, P5.1 reports exist)
- Old GitHub PAT exposed — revoke at https://github.com/settings/tokens

## 9) Handoff Notes
- Pipeline entry points: `video_translator.py` (CLI), `web_app.py` (Web, ~40 LOC thin entry)
- Shared logic: `web/pipeline_runner.py` (used by both CLI and Web)
- Deployment: Docker + NVIDIA CUDA 12.3 on Vast.ai GPU instances
- API providers: Grok (primary), Gemini, Vertex — keys in `.env`, loaded by `pipeline/providers/factory.py`
- TTS: edge-tts (322+ voices, free Microsoft); voices verified by `scripts/verify_edge_tts_voices.py`
- OCR: EasyOCR + Pillow overlay rendering; auto-upgrade fast→quality for non-CJK languages
- Source separation: Demucs (GPU-accelerated)
- Single source of truth for languages: `pipeline/languages.py` (do NOT duplicate metadata anywhere else)
- Test suite: 337 tests in `tests/`, run with `pytest tests/ -v`
- Last commit: `d63aa0c` on `main`, pushed to GitHub 2026-04-07
