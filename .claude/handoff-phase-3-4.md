# Handoff — Phase 3 + Phase 4 Complete

> **Last commit**: `d63aa0c` (pushed to `origin/main` 2026-04-07)
> **Branch**: `main` (clean, up to date)
> **Test status**: 337/337 pass

---

## What Was Built

### Phase 3 — Architectural Cleanup & Quality Gates (P3.0–P3.7)

| Sub-phase | Deliverable |
|---|---|
| **P3.0** | `docs/execution/phase-status.md`, `docs/execution/phase-report-template.md`, `.claude/skills/dev-standards.md` (3 pillars), `.claude/skills/architecture-rules.md` (7 rules) |
| **P3.1** | `pipeline/config.py` (10 frozen dataclasses) + `pipeline/logger.py` (CLI/Web formatters). Migrated 11 modules. 0 hardcoded values, 0 print() in pipeline/ |
| **P3.2** | `pipeline/providers/` package with `TranslationProvider` ABC + Grok/Gemini/Vertex implementations + factory + KeyRotator |
| **P3.3** | `web/` package: `app.py` (FastAPI factory), `models.py` (Pydantic), `routes.py`, `worker.py` (job queue), `pipeline_runner.py` (shared CLI/Web logic). `web_app.py` reduced 594 → ~40 LOC |
| **P3.4** | Split `pipeline/ocr.py` (819 LOC) → 4 modules; split `pipeline/dub.py` (424 LOC) → 3 modules. All re-exported via package `__init__.py` |
| **P3.5** | `pipeline/errors.py` (TransientError/FatalError/DegradedError) + `pipeline/utils.py` (TempDir context manager, disk check, retry decorator with exponential backoff) |
| **P3.6** | `tests/` infrastructure (pytest + httpx + asyncio). 60 baseline tests. `requirements-dev.txt` |
| **P3.7** | `Dockerfile` cleanup + Noto font packages + `HEALTHCHECK` directive + `/api/health` endpoint |

### Phase 4 — Top 30 World Languages (Hardened Rollout) (P4.0–P4.6)

| Sub-phase | Deliverable |
|---|---|
| **P4.0** | `.claude/skills/phase-workflow.md` — 5 mandatory gates (Drift Check, SOT, Module Granularity, CLI/Web Parity, Phase Completion). Updates `dev-standards.md` Pillar 3. Adds Rule 3a/3b to `architecture-rules.md` |
| **P4.1** | `pipeline/languages.py` — `LanguageSpec` dataclass + `LANGUAGES` registry + derived `DEFAULT_VOICES`/`DRAWTEXT_UNSAFE_LANGS`/`ALL_LANGUAGE_CODES`. Refactored 4 consumers to import from registry. **Fixed CLI drift in `video_translator.py:158`** (P3.1 miss) |
| **P4.2** | OCR FONT_MAP expanded: bn/ur/fa/mr/te/ta + el. Glyph coverage warning when < 80%. Dockerfile installs `fonts-noto-ui-core fonts-noto-unhinted` |
| **P4.3** | `scripts/verify_edge_tts_voices.py` (pre-release gate). Early validation: `target_lang`/`source_lang` checked against `ALL_LANGUAGE_CODES` in API (400 response) and CLI (argparse `choices=`) |
| **P4.4** | `tests/test_languages_registry.py` — parameterized contract tests (uniqueness, voice format regex, ordering, drawtext policy). Stronger `tests/test_web_api.py` with set/order/regex checks. 60 → 176 tests |
| **P4.5** | Registry expanded 15 → 30 languages. New: bn, ur, mr, te, tr, ta, fa, pl, uk, nl, ro, el, cs, hu, sv. All 30 voices verified by script. 176 → 266 tests |
| **P4.6** | 5 new test files (71 tests): `test_pipeline_runner.py`, `test_cross_mode_parity.py` (auto-detects future CLI drift), `test_negative_paths.py`, `test_audio_edge.py`, `test_pipeline_integration.py`. 266 → 337 tests |

---

## Test Suite Growth

```
Before P3:   0 tests (no infrastructure)
After P3.6:  60 tests (baseline)
After P4.4: 176 tests (contract hardening)
After P4.5: 266 tests (per-language parameterized)
After P4.6: 337 tests (edge cases + integration)
```

Run with: `pytest tests/ -v`

---

## Architecture Diagram (Current State)

```
video-translator/
├── video_translator.py        ← CLI entry point (uses cfg, languages registry)
├── web_app.py                 ← Web entry point (~40 LOC, delegates to web/)
├── pipeline/
│   ├── config.py              ← 10 frozen dataclasses, all constants
│   ├── languages.py           ← 30 LanguageSpec + derived constants (SINGLE SOURCE OF TRUTH)
│   ├── logger.py              ← CLI/Web formatters
│   ├── errors.py              ← TransientError / FatalError / DegradedError
│   ├── utils.py               ← TempDir, retry, disk check
│   ├── audio.py               ← Extract WAV (ffmpeg)
│   ├── transcribe.py          ← Whisper STT + model cache
│   ├── translate.py           ← Public API → providers/
│   ├── subtitle.py            ← SRT generation
│   ├── burn.py                ← ffmpeg drawtext + filter_script
│   ├── preflight.py           ← Startup checks
│   ├── ocr_render.py          ← Pillow PNG overlay rendering
│   ├── providers/             ← Translation providers (P3.2)
│   │   ├── base.py            (TranslationProvider ABC)
│   │   ├── grok.py / gemini.py / vertex.py
│   │   └── factory.py         (load_keys, build_rotator)
│   ├── dub/                   ← Dubbing pipeline (P3.4 split)
│   │   ├── tts.py             (edge-tts, DEFAULT_VOICES re-exported from languages)
│   │   ├── mixer.py           (BGM mix, fade, limiter)
│   │   └── separator.py       (Demucs)
│   └── ocr/                   ← OCR pipeline (P3.4 split)
│       ├── detector.py        (EasyOCR + frame extraction)
│       ├── grouper.py         (Kalman tracking + persistence)
│       ├── translator.py      (OCR text translation)
│       └── filter.py          (drawtext + overlay generation)
├── web/                       ← Web layer (P3.3)
│   ├── app.py                 (FastAPI factory)
│   ├── models.py              (Pydantic schemas)
│   ├── routes.py              (API endpoints, uses languages registry)
│   ├── worker.py              (job queue + background thread)
│   └── pipeline_runner.py     (SHARED CLI/Web pipeline)
├── tests/                     ← 337 tests (P3.6 + P4.4 + P4.6)
│   ├── test_audio_edge.py            (11)
│   ├── test_config.py                (13)
│   ├── test_cross_mode_parity.py     (14) ← Drift prevention
│   ├── test_errors.py                (14)
│   ├── test_languages_registry.py    (199, parameterized per-spec)
│   ├── test_negative_paths.py        (29)
│   ├── test_pipeline_integration.py  (6)
│   ├── test_pipeline_runner.py       (11)
│   ├── test_providers.py             (14)
│   ├── test_subtitle.py              (13)
│   └── test_web_api.py               (13)
├── scripts/
│   └── verify_edge_tts_voices.py    ← Pre-release voice gate
├── docs/execution/
│   ├── phase-status.md              ← Source of truth for progress
│   ├── phase-report-template.md
│   └── reports/                     (empty — phase reports not yet written)
└── .claude/
    ├── skills/
    │   ├── dev-standards.md         (3 pillars)
    │   ├── architecture-rules.md    (7 rules + Rule 3a/3b)
    │   └── phase-workflow.md        (5 mandatory gates)
    └── handoff-phase-3-4.md         (this file)
```

---

## Known Issues & Tech Debt (Not Blocking)

### From P4.6 audit
1. **`has_audio_track` silent failure** — `pipeline/audio.py:23-39` swallows ALL ffprobe errors and returns `False`. P3.5 should have caught this. Tests in `test_audio_edge.py::TestHasAudioTrack` document current behavior. **Future fix**: raise `FatalError` instead.

2. **OCR import error → silent skip** — `web/pipeline_runner.py:122-167` and `video_translator.py` catch `ImportError` and log warning. This is intended (DegradedError pattern) but should use the new `DegradedError` class explicitly.

3. **Config `drawtext_unsafe_langs` access pattern** — Currently uses `field(default_factory=_load_drawtext_unsafe_langs)` which works but isn't tested for circular import resistance. If `pipeline.languages` ever imports from `pipeline.config`, this breaks. Test `test_pipeline_languages_has_no_dependencies` in `test_cross_mode_parity.py` enforces this rule.

### From security review
4. **Old PAT exposed** — Bro nên revoke fine-grained PAT cũ tại https://github.com/settings/tokens (token bắt đầu `github_pat_11B7S5...`). Token này read-only nên không gây hại nhưng đã hiện ra terminal.

5. **`.claude/settings.local.json`** — đã gitignored nhưng vẫn còn trên máy local. File này chứa Bash command allowlist cho Claude Code, không có secrets nhưng là user-local.

### From Phase 3 design (chưa làm)
6. **Pipeline modules chưa dùng `TempDir`** — `pipeline/dub/mixer.py` và `pipeline/ocr/detector.py` vẫn tự `os.makedirs("_tts_temp")` và rely on success-path cleanup. Nên migrate sang `with temp_dir("tts", base_dir=output_dir) as tmp:` từ `pipeline/utils.py`.

7. **Custom exceptions chưa được throw** — `pipeline/errors.py` đã có nhưng pipeline modules vẫn dùng `RuntimeError`/`ValueError`. Nên thay dần (TransientError cho rate limit/timeout, FatalError cho input/config, DegradedError cho missing OCR).

8. **Retry decorator chưa được áp dụng** — `pipeline/utils.py::retry` đã có nhưng `pipeline/translate.py` vẫn có inline retry loop. Có thể refactor để dùng decorator.

---

## Next Session — Priority Order

### P0 — Verification on office PC
1. **Pull latest**: `git pull origin main` — should be at commit `d63aa0c`
2. **Install deps**: `pip install -r requirements.txt -r requirements-dev.txt`
3. **Run tests**: `pytest tests/ -v` — must show 337 passed
4. **Smoke test web**: `python web_app.py` → http://localhost:3456 → check 30 languages dropdown
5. **Smoke test CLI**: `python video_translator.py <test_video> -t vi --whisper-model tiny` → check SRT output uses registry
6. **Drift check**: `grep -rn "_DRAWTEXT_UNSAFE\b" --include="*.py" .` → should return 0

### P1 — Vast.ai deployment update
1. SSH into Vast.ai instance (or open Jupyter Terminal)
2. `cd /workspace/video-translator`
3. `git pull origin main`
4. `pip install -r requirements-dev.txt` (if want tests)
5. Verify Noto fonts installed: `dpkg -l | grep fonts-noto`
6. Restart server: use `vastai-restart` skill or kill+restart manually
7. Test 1-2 new languages (vd Bengali, Turkish) end-to-end via web UI

### P2 — Future Phase 5 candidates (chọn 1-2 cái)

**From original Phase 3 plan TODO** (renamed because user prioritized langs first):
- [ ] **YouTube URL support** — `yt-dlp` integration, accept URL instead of file upload (Medium effort)
- [ ] **Batch processing** — multiple videos via folder upload or CSV list (Medium effort)
- [ ] **Dual subtitles** — burn original + translated together (Low effort, just SRT formatting)
- [ ] **Speaker diarization** — pyannote-audio for "who said what" → multi-voice TTS (High effort, GPU heavy)
- [ ] **Voice cloning** — ElevenLabs provider for high-quality dubbing (Medium effort, paid API)
- [ ] **Progress bar** — real-time WebSocket updates instead of polling (Medium effort)

**Tech debt cleanup** (no new features):
- [ ] **P5.0 — TempDir migration** — migrate dub/ocr modules to use `pipeline.utils.temp_dir` context manager
- [ ] **P5.1 — Custom exception adoption** — replace RuntimeError/ValueError with TransientError/FatalError/DegradedError
- [ ] **P5.2 — Retry decorator adoption** — replace inline retry loops with `@retry` decorator
- [ ] **P5.3 — Fix `has_audio_track` silent failure** — raise FatalError instead of returning False
- [ ] **P5.4 — Phase report writing** — write actual phase reports for P3.0–P4.6 to `docs/execution/reports/` (currently empty)

---

## Key Files for Quick Reference

| Concern | File | Lines |
|---|---|---|
| Single source of truth (languages) | `pipeline/languages.py` | 1-110 |
| All constants | `pipeline/config.py` | 1-160 |
| Phase tracker | `docs/execution/phase-status.md` | 1-60 |
| Workflow gates | `.claude/skills/phase-workflow.md` | 1-260 |
| Architecture rules | `.claude/skills/architecture-rules.md` | 1-150 |
| Dev standards | `.claude/skills/dev-standards.md` | 1-380 |
| Drift prevention tests | `tests/test_cross_mode_parity.py` | 1-150 |
| Voice verification | `scripts/verify_edge_tts_voices.py` | 1-60 |

---

## Session Kickoff Commands (Copy-Paste)

```bash
# 1. Verify state
cd "D:/Dev/Tools/Video Translator"   # or office PC path
git pull origin main
git log --oneline -5
# Expected: d63aa0c at top

# 2. Read context
cat docs/execution/phase-status.md
cat .claude/handoff-phase-3-4.md          # this file
cat .claude/skills/phase-workflow.md      # mandatory gates

# 3. Verify tests still pass
pytest tests/ -v
# Expected: 337 passed

# 4. Verify drift check
grep -rn "_DRAWTEXT_UNSAFE\b" --include="*.py" .
grep -rn "^DEFAULT_VOICES\s*=\s*{" --include="*.py" .
# Expected: 0 matches

# 5. Verify voices
python scripts/verify_edge_tts_voices.py
# Expected: All 30 voices verified successfully.

# 6. Start server
python web_app.py
# Then: curl http://localhost:3456/api/languages | python -m json.tool | head
```

---

## Conventions to Follow Next Session

1. **Read `phase-workflow.md` first** — 5 mandatory gates apply to every phase
2. **Use `phase-report-template.md`** for documenting any new phase
3. **Update `phase-status.md`** at start (In Progress) and end (Done) of each phase
4. **Commit prefix conventions**: `feat:`, `refactor:`, `fix:`, `test:`, `docs:`, `chore:`
5. **Co-author tag**: include `Co-Authored-By: Claude Opus 4.6 (1M context)` in commits
6. **Drift check** before/after any constant migration: `grep -rn "<value>" --include="*.py" .`
7. **Single source of truth** — if metadata appears in 2+ files, consolidate to a registry module first
