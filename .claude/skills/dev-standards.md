# Video Translator — Development Standards

> This skill defines how Claude thinks, codes, and reviews for the Video Translator project.
> 3 pillars: **Product Thinking** (Content Creator) → **Engineering** (Pipeline Architecture) → **Review Process** (QA Gate).

---

## PILLAR 1: Product Thinking — Content Creator Perspective

### 1.1 Business Model

Video Translator helps content creators reach global audiences by translating and dubbing videos automatically. Users upload a video, choose a target language, and get back subtitles + dubbed audio + OCR-translated text overlays.

```
Value = Output Quality × Language Coverage × Processing Speed / Cost
```

The user pays for GPU compute (Vast.ai) and API calls (Grok/Gemini). Every feature must make the translation faster, better, or cheaper.

### 1.2 Key Quality Metrics — What The User Cares About

| Metric | What It Means | Good / Bad |
|--------|---------------|------------|
| **Translation Accuracy** | Does the translated text convey the original meaning? | Native-sounding = good; literal/robotic = bad |
| **TTS Naturalness** | Does the dubbed audio sound natural? | Correct prosody/tone = good; monotone = bad |
| **Timing Sync** | Do subtitles/audio match the video? | Within 200ms = good; noticeable lag = bad |
| **OCR Coverage** | Are on-screen texts detected and translated? | >90% detected = good; missed titles = bad |
| **Processing Speed** | Time from upload to output | <2min/min of video on GPU = good |
| **Output Formats** | SRT + dubbed video + OCR overlay | All available = good; missing options = bad |

### 1.3 User Workflow

```
Upload Video → Choose Language → [Options: dub, OCR, burn subs, BGM]
    → Transcribe (Whisper) → Translate (Grok/Gemini) → Generate SRT
    → [Optional] TTS + Audio Mix → Dubbed Video
    → [Optional] OCR Detect + Translate → Overlay Video
    → Download Results (SRT, dubbed video, OCR video)
```

### 1.4 Feature Design Rules

Before building ANY feature, answer these:

- [ ] **1-Click Rule**: Can the user get results without reading documentation?
- [ ] **Quality First**: Does this improve output quality (translation, audio, timing)?
- [ ] **Speed Matters**: Does this make processing faster, not slower?
- [ ] **Graceful Degradation**: If a feature fails, does the rest of the pipeline still work?
- [ ] **Language Coverage**: Does this work for ALL supported languages, not just English/Vietnamese?
- [ ] **CLI/Web Parity**: Is this feature available in BOTH CLI and Web UI?

---

## PILLAR 2: Engineering Standards — Pipeline Architecture

### 2.1 Directory Structure

```
video-translator/
├── video_translator.py       # CLI entry point (argparse)
├── web_app.py                # Web entry point (FastAPI)
├── pipeline/                 # Processing pipeline (core logic)
│   ├── config.py             # Centralized config (ALL constants here)
│   ├── logger.py             # Structured logging
│   ├── errors.py             # Custom exceptions
│   ├── utils.py              # Shared utilities (TempDir, disk check)
│   ├── audio.py              # Step 1: Extract audio (ffmpeg)
│   ├── transcribe.py         # Step 2: Whisper STT
│   ├── translate.py          # Step 3: Translation (public API)
│   ├── providers/            # Translation provider implementations
│   │   ├── base.py           # ABC: TranslationProvider
│   │   ├── grok.py           # Grok provider
│   │   ├── gemini.py         # Gemini provider
│   │   └── vertex.py         # Vertex AI provider
│   ├── subtitle.py           # Step 4: SRT generation
│   ├── burn.py               # Step 5: Burn subtitles into video
│   ├── dub/                  # Step 6: Dubbing pipeline
│   │   ├── tts.py            # TTS generation (edge-tts)
│   │   ├── mixer.py          # Audio mixing (BGM, fade, limiter)
│   │   └── separator.py      # Source separation (Demucs)
│   ├── ocr/                  # Step 7: OCR pipeline
│   │   ├── detector.py       # Text detection (EasyOCR)
│   │   ├── grouper.py        # Text grouping + tracking
│   │   ├── translator.py     # OCR text translation
│   │   └── filter.py         # Noise/region filtering
│   ├── ocr_render.py         # OCR overlay rendering (Pillow)
│   └── preflight.py          # Startup checks (GPU, API keys)
├── web/                      # Web app modules
│   ├── app.py                # FastAPI app factory
│   ├── models.py             # Pydantic request/response models
│   ├── routes.py             # API routes
│   ├── worker.py             # Job queue + background worker
│   └── pipeline_runner.py    # Shared pipeline execution (CLI + Web)
├── static/                   # Web UI assets
│   └── index.html            # Single-page app
├── deploy/                   # Deployment configs
│   ├── nginx.conf            # Reverse proxy
│   └── start.sh              # Docker entrypoint
├── tests/                    # Test suite
│   ├── conftest.py           # Shared fixtures
│   ├── test_*.py             # Test files
│   └── fixtures/             # Sample data
├── docs/execution/           # Phase tracking
│   ├── phase-status.md       # Progress tracker
│   ├── phase-report-template.md
│   └── reports/              # Completed phase reports
├── Dockerfile
├── requirements.txt
├── requirements-dev.txt
└── STATUS.md
```

### 2.2 Pipeline Patterns

**Config-Driven**: All constants in `pipeline/config.py`. No magic numbers in code.
```python
from pipeline.config import Config
cfg = Config()
# cfg.audio_sample_rate, cfg.batch_size, cfg.tts_concurrency, etc.
```

**Structured Logging**: Use `pipeline/logger.py`, never `print()`.
```python
from pipeline.logger import get_logger
log = get_logger("translate")
log.info("Translating batch", batch_num=3, total=10, provider="grok")
```

**Error Classification**: Use `pipeline/errors.py`.
```python
from pipeline.errors import TransientError, FatalError, DegradedError
# TransientError → retry with backoff (rate limit, timeout)
# FatalError → abort job (invalid input, bad config)
# DegradedError → skip feature, continue (OCR unavailable, single TTS fail)
```

**Temp Directory Safety**: Always use context managers.
```python
from pipeline.utils import TempDir
with TempDir("tts", base_dir=output_dir) as tmp:
    # work with tmp.path
    # auto-cleanup on exit (including exceptions)
```

### 2.3 Module Isolation Rules

1. **One file = one responsibility**. If a file does 2 things, split it.
2. **Max 400 LOC per file**. Beyond this, find a natural split point.
3. **No circular imports**. Pipeline modules depend downward, never upward.
4. **Public API via `__init__.py`**. Packages export only what callers need.
5. **CLI and Web share `PipelineRunner`**. No duplicate pipeline logic.

### 2.4 Dependency Graph (Allowed)

```
video_translator.py ──→ pipeline/* (direct)
web_app.py ──→ web/* ──→ pipeline/* (via PipelineRunner)

pipeline/translate.py ──→ pipeline/providers/*
pipeline/dub/__init__.py ──→ pipeline/dub/tts.py, mixer.py, separator.py
pipeline/ocr/__init__.py ──→ pipeline/ocr/detector.py, grouper.py, etc.

All pipeline/* ──→ pipeline/config.py, pipeline/logger.py, pipeline/errors.py
```

**Forbidden:**
- `pipeline/*` importing from `web/*`
- `pipeline/audio.py` importing from `pipeline/ocr.py`
- Any module importing from `video_translator.py` or `web_app.py`

### 2.5 CLI/Web Parity

Every pipeline feature must work identically in CLI and Web:
- Same output files (SRT, dubbed video, OCR overlay)
- Same processing steps and order
- Same error messages
- Same caching behavior

Differences allowed:
- Progress reporting format (CLI: `[Step X/Y]`, Web: JSON status updates)
- Logging format (CLI: human-readable, Web: JSON)
- File paths (CLI: local paths, Web: upload directory)

### 2.6 Code Style

- Type hints on all function signatures
- Docstrings on all public functions (one-liner for simple, multi-line for complex)
- `snake_case` for functions/variables, `PascalCase` for classes, `UPPER_CASE` for constants
- Early returns for guard clauses
- `[Module]` prefix in log messages: `[Translate]`, `[TTS]`, `[OCR]`
- Vietnamese in UI strings, English in code/comments/logs

---

## PILLAR 3: Review Process — Senior → QA → Senior

### 3.0 Pre-Implementation

Before writing code:
- [ ] Requirements documented (plan file or conversation)
- [ ] Affected files listed
- [ ] Data flow traced (input → pipeline steps → output)
- [ ] Existing patterns found (reuse, don't reinvent)
- [ ] Edge cases listed (empty input, GPU OOM, API failure, disk full)

### 3.1 Senior Review 1 — Architecture

After code is written, before testing:

**Structure:**
- [ ] Feature-based directory structure followed
- [ ] Each file < 400 LOC
- [ ] Pipeline modules don't import from web/ or entry points
- [ ] No duplicate logic between CLI and Web

**Safety:**
- [ ] All subprocess calls have timeouts
- [ ] All temp directories use context managers
- [ ] All API calls have retry logic for transient errors
- [ ] No hardcoded values (use Config)
- [ ] No `print()` statements (use logger)
- [ ] No catch-all `except Exception` that swallows errors silently

**Data:**
- [ ] Cache keys include all relevant parameters (model, language, version)
- [ ] Temp files cleaned up on both success and failure
- [ ] Large files not loaded into memory unnecessarily

**Drift Check Gate** (mandatory — see `phase-workflow.md` Gate 1):
- [ ] Grep repo-wide for any hardcoded value just migrated → 0 stray copies
- [ ] Grep both CLI (`video_translator.py`) AND Web (`web/`, `web_app.py`) entry points for the same pattern
- [ ] If a constant existed in both → extracted to shared `pipeline/` module

**Single Source of Truth Gate** (mandatory — see `phase-workflow.md` Gate 2):
- [ ] No new dict/list/set of metadata duplicates an existing registry
- [ ] If similar registry exists (`*_MAP`, `DEFAULT_*`, `*_REGISTRY`), extended it instead of creating parallel
- [ ] All consumers of metadata import from the registry, not from another consumer

**CLI/Web Parity Gate** (mandatory — see `phase-workflow.md` Gate 4):
- [ ] CLI and Web both use shared `web/pipeline_runner.py` (or equivalent)
- [ ] No constant exists in `video_translator.py` that also exists in `web/`
- [ ] Behavior decisions (auto-upgrades, validations, defaults) live in pipeline modules, not entry points

### 3.2 QA — Functional Testing

**Core scenarios:**
- [ ] **Happy path**: Video → SRT + dubbed video (correct output)
- [ ] **Empty state**: No speech → "No segments found" message, not crash
- [ ] **Error state**: Bad video / API error → clear error message
- [ ] **Edge cases**: Very short video, very long video, no audio track
- [ ] **Cross-mode**: CLI and Web produce same output for same input

**Pipeline quality:**
- [ ] Translation accuracy: spot-check 3-5 segments for natural language
- [ ] TTS timing: dubbed audio aligns with subtitle timestamps
- [ ] OCR: on-screen text detected and translated correctly
- [ ] Audio: no clipping, proper fade, BGM volume correct

**Technical:**
- [ ] No Python tracebacks in output (errors are user-friendly)
- [ ] No orphan temp files after processing
- [ ] Cache hit on re-run (faster processing)
- [ ] Memory usage reasonable (no leaks on multi-job web processing)

### 3.3 Regression Testing

For any code change, verify existing features still work:

**CLI regression suite:**
```bash
# Basic translation
python video_translator.py test.mp4 -t vi

# Translation + burn subtitles
python video_translator.py test.mp4 -t vi --burn

# Full dubbing
python video_translator.py test.mp4 -t vi --dub --bgm music.mp3

# Dubbing + OCR
python video_translator.py test.mp4 -t vi --dub --ocr

# Transcribe only
python video_translator.py test.mp4 --transcribe-only

# Force re-process
python video_translator.py test.mp4 -t vi --no-cache
```

**Web regression suite:**
- Upload video → job created → status polling → download files
- Multiple jobs → sequential queue → all complete
- Job cleanup after 24h → old files removed

### 3.4 Senior Review 2 — Sign-off

Final review against original requirements:

- [ ] **All acceptance criteria met** — every item in phase scope is done
- [ ] **No regression** — existing CLI and Web features still work
- [ ] **Commit message** follows convention: `refactor:`, `feat:`, `fix:`, `chore:`, `test:`, `docs:`
- [ ] **Phase report** written with evidence (commands executed, outputs verified)
- [ ] **phase-status.md** updated with completion status and gate results
