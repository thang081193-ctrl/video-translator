# Video Translator — Architecture Rules

> Mandatory rules for module structure, dependencies, and code organization.
> Every code change must comply. No exceptions without explicit approval.

---

## Rule 1: Pipeline = Core Logic Layer

All video/audio processing logic lives in `pipeline/`. Pipeline modules are self-contained and never depend on entry points (`video_translator.py`, `web_app.py`) or web layer (`web/`).

**Allowed:** `pipeline/translate.py` imports from `pipeline/providers/grok.py`
**Forbidden:** `pipeline/translate.py` imports from `web/routes.py`

---

## Rule 2: Single Entry, Shared Runner

CLI (`video_translator.py`) and Web (`web_app.py`) are thin entry points. Both delegate to `web/pipeline_runner.py` (or equivalent shared runner) for actual processing.

**Rule:** If you change pipeline behavior, change it in ONE place. Never duplicate logic.

---

## Rule 3: Config = Single Source of Truth

All constants, thresholds, timeouts, and magic numbers live in `pipeline/config.py`.

**Allowed:** `Config.audio_sample_rate = 16000`
**Forbidden:** `subprocess.run(["ffmpeg", "-ar", "16000", ...])` with inline 16000

Environment variables are loaded once in Config and accessed via the Config object.

### Rule 3a: Metadata Registries

For domain-specific metadata that's broader than scalar config (language lists, voice mappings, font maps, supported formats), create a dedicated **registry module** under `pipeline/`:

- `pipeline/languages.py` — language codes, native names, voices, OCR safety, script families
- `pipeline/config.py` — scalar constants only (timeouts, thresholds, sample rates)

**MUST**: If the same metadata appears in 2+ files independently, consolidate to a registry **before** any other change. This is a P0 refactor.

**Example violation (Phase 4 motivation):**
```python
# web/routes.py            — list of 15 language dicts
# pipeline/dub/tts.py      — DEFAULT_VOICES dict of 15 codes
# pipeline/config.py       — drawtext_unsafe_langs subset
# video_translator.py:158  — local _DRAWTEXT_UNSAFE set (drift!)
```
**Fix**: Create `pipeline/languages.py` with `LanguageSpec` dataclass; derive `DEFAULT_VOICES`, `DRAWTEXT_UNSAFE_LANGS`, and UI list from a single `LANGUAGES_TOP30` source.

### Rule 3b: Drift Check Before Migration

Before running any "migration" phase (replacing N hardcoded values across the codebase), grep for the value AND its semantic siblings repo-wide:

```bash
grep -rn "<value_or_pattern>" --include="*.py" .
```

Both CLI (`video_translator.py`) and Web (`web/`, `web_app.py`) entry points MUST be checked. P3.1 missed `video_translator.py:158` because the grep was scoped to `pipeline/` only.

See `.claude/skills/phase-workflow.md` Gate 1 for the full procedure.

---

## Rule 4: No Silent Failures

Every function that can fail must either:
1. Raise a typed exception (`TransientError`, `FatalError`, `DegradedError`)
2. Return a result that the caller explicitly checks

**Forbidden:**
```python
# BAD: swallows all errors, returns misleading default
def has_audio(path):
    try:
        # ffprobe check
    except:
        return False
```

**Required:**
```python
# GOOD: explicit error handling
def has_audio(path):
    try:
        result = run_ffprobe(path)
        return "audio" in result.streams
    except subprocess.CalledProcessError as e:
        raise FatalError(f"ffprobe failed on {path}: {e}")
```

---

## Rule 5: Temp Files = Context Managers

All temporary directories/files must use the `TempDir` context manager from `pipeline/utils.py`. This ensures cleanup on both success and failure paths.

**Forbidden:** `os.makedirs("_tts_temp", exist_ok=True)` ... `shutil.rmtree("_tts_temp")`
**Required:** `with TempDir("tts") as tmp: ...`

---

## Rule 6: Max 400 LOC Per File

If a file exceeds 400 lines, find a natural split point and extract a sub-module. Use packages (`__init__.py`) to maintain backward-compatible public API.

---

## Rule 7: New Module Checklist

Before creating a new module file, answer:
1. Does an existing module already handle this? → Extend it, don't create new.
2. Does this have a single, clear responsibility? → If not, rethink the split.
3. Does this introduce a new dependency? → Justify it in the phase report.
4. Is this importable without side effects? → Module-level code must not run on import.

---

## Module Map

### Core Infrastructure
| Module | Responsibility | Max LOC |
|--------|---------------|---------|
| `pipeline/config.py` | All configuration constants + env loading | 150 |
| `pipeline/logger.py` | Structured logging setup | 100 |
| `pipeline/errors.py` | Custom exception hierarchy | 80 |
| `pipeline/utils.py` | TempDir, disk check, shared utilities | 100 |

### Pipeline Steps
| Module | Responsibility | Max LOC |
|--------|---------------|---------|
| `pipeline/audio.py` | Extract audio from video (ffmpeg) | 150 |
| `pipeline/transcribe.py` | Whisper STT + model caching | 150 |
| `pipeline/translate.py` | Translation public API (delegates to providers) | 200 |
| `pipeline/subtitle.py` | SRT file generation | 100 |
| `pipeline/burn.py` | Burn subtitles/overlays into video | 200 |
| `pipeline/preflight.py` | GPU, ffmpeg, API key startup checks | 300 |

### Translation Providers
| Module | Responsibility | Max LOC |
|--------|---------------|---------|
| `pipeline/providers/base.py` | TranslationProvider ABC + key rotation | 150 |
| `pipeline/providers/grok.py` | Grok HTTP API implementation | 150 |
| `pipeline/providers/gemini.py` | Gemini genai client implementation | 150 |
| `pipeline/providers/vertex.py` | Vertex AI implementation | 150 |
| `pipeline/providers/factory.py` | Provider selection + instantiation | 80 |

### Dubbing Pipeline
| Module | Responsibility | Max LOC |
|--------|---------------|---------|
| `pipeline/dub/tts.py` | edge-tts generation + speed adjustment | 200 |
| `pipeline/dub/mixer.py` | Audio mixing, BGM, fade, limiter | 200 |
| `pipeline/dub/separator.py` | Demucs source separation | 150 |

### OCR Pipeline
| Module | Responsibility | Max LOC |
|--------|---------------|---------|
| `pipeline/ocr/detector.py` | EasyOCR detection + frame extraction | 250 |
| `pipeline/ocr/grouper.py` | Text grouping + persistence tracking | 250 |
| `pipeline/ocr/translator.py` | OCR text batch translation | 100 |
| `pipeline/ocr/filter.py` | Noise filtering, region filtering | 150 |
| `pipeline/ocr_render.py` | Pillow overlay rendering | 400 |

### Web Layer
| Module | Responsibility | Max LOC |
|--------|---------------|---------|
| `web/app.py` | FastAPI app factory + middleware | 80 |
| `web/models.py` | Pydantic request/response models | 100 |
| `web/routes.py` | API endpoint handlers | 200 |
| `web/worker.py` | Job queue + background worker thread | 200 |
| `web/pipeline_runner.py` | Shared pipeline execution logic | 300 |

---

## Dependency Rules (Import Graph)

```
Layer 0 (Infrastructure):
  pipeline/config.py
  pipeline/logger.py
  pipeline/errors.py
  pipeline/utils.py

Layer 1 (Pipeline Steps):
  pipeline/audio.py          → Layer 0
  pipeline/transcribe.py     → Layer 0
  pipeline/translate.py      → Layer 0, pipeline/providers/*
  pipeline/subtitle.py       → Layer 0
  pipeline/burn.py           → Layer 0
  pipeline/dub/*             → Layer 0
  pipeline/ocr/*             → Layer 0, pipeline/translate.py
  pipeline/ocr_render.py     → Layer 0
  pipeline/preflight.py      → Layer 0

Layer 2 (Shared Runner):
  web/pipeline_runner.py     → Layer 0, Layer 1

Layer 3 (Entry Points):
  web/routes.py              → Layer 2, web/models.py, web/worker.py
  web/app.py                 → web/routes.py
  web_app.py                 → web/app.py
  video_translator.py        → Layer 2

FORBIDDEN:
  Layer 1 → Layer 2 or Layer 3
  Layer 0 → anything above Layer 0
  web/* → video_translator.py
  pipeline/* → web/*
```
