# Phase Workflow — Mandatory Process for Every Feature

> This skill defines the **mandatory workflow** that must be followed for every feature, refactor, or bug fix.
> Goal: prevent drift, hallucination, and tech debt accumulation.

---

## Why This Skill Exists

Phase 3 P3.1 (Config + Logger migration) **missed `video_translator.py:158`** where a `_DRAWTEXT_UNSAFE` set was hardcoded — even though P3.1 explicitly aimed to migrate ALL hardcoded values. The CLI vs Web entry points have separate code paths and easy to miss in grep.

Result: silent CLI/Web drift only caught later by external review.

This skill enforces gates that would have caught it.

---

## Pre-Implementation (READ FIRST, in order)

Before writing ANY code for a new feature/phase:

1. **`docs/execution/phase-status.md`** — current state, which phase, what was last done
2. **Latest report** in `docs/execution/reports/` — what decisions were locked in previous phase
3. **`.claude/skills/dev-standards.md`** — 3 pillars (Product / Engineering / Review)
4. **`.claude/skills/architecture-rules.md`** — module map + dependency graph + 7 rules
5. **`.claude/skills/phase-workflow.md`** (this skill) — process gates

If any of these files don't exist or are out of date, **STOP** and update them before coding.

---

## The 5 Mandatory Gates

### Gate 1: Drift Check Gate
> **Run BEFORE editing any hardcoded value.**

When you're about to change a hardcoded value (constant, magic number, string, list), you MUST first grep the entire repo for that exact value AND its semantic siblings:

```bash
# Find ALL occurrences of the literal value
grep -rn "<value_or_pattern>" --include="*.py" .

# Find semantic siblings (e.g. if changing "fr", also grep "es", "de" — same set)
grep -rn "fr\|es\|de\|it" --include="*.py" .
```

**Decision tree:**
- Found in **1 file** → safe to edit in place
- Found in **2+ files but they share an import** → already centralized, edit the source
- Found in **2+ files independently** → **MUST consolidate** to a registry/config module BEFORE editing
- Found in **CLI entry point AND Web entry point** independently → **MUST update both atomically** in same commit + extract to shared module if not already

**Example violation (P3.1 P3 miss):**
```python
# pipeline/config.py
drawtext_unsafe_langs = ("vi", "fr", "es", ...)

# video_translator.py  ← MISSED in P3.1 grep
_DRAWTEXT_UNSAFE = {"vi", "fr", "es", ...}  # local copy, drifted
```
The grep `_DRAWTEXT_UNSAFE` would have caught this. **Run grep before declaring migration done.**

---

### Gate 2: Single Source of Truth Gate
> **Run BEFORE creating any dict/list/set of metadata.**

Before adding a new dict, list, or set that holds metadata (mapping codes→values, names, settings):

```bash
# Search for existing registries with similar shape
grep -rn "DEFAULT_\|_MAP\|REGISTRY\|VOICES\|LANGUAGES\|SUPPORTED_" --include="*.py" .
```

**Decision tree:**
- No similar registry exists → create the new one in a dedicated module (not embedded in a consumer)
- Similar registry exists → **MUST extend the existing registry**, never create a parallel one
- Multiple similar registries exist → **STOP**, consolidate them first as a separate refactor phase

**Example violation (Phase 3 → Phase 4 issue):**
```python
# web/routes.py
languages = [{"code": "vi", "name": "Tiếng Việt"}, ...]  # 15 entries

# pipeline/dub/tts.py
DEFAULT_VOICES = {"vi": "vi-VN-...", ...}  # 15 entries — MUST match languages

# pipeline/config.py
drawtext_unsafe_langs = ("vi", ...)  # subset of language codes
```
3 files × 15 entries × 0 enforcement = guaranteed drift. **Solution: create `pipeline/languages.py` registry, derive everything from it.**

---

### Gate 3: Module Granularity Gate
> **Run AFTER writing code, BEFORE committing.**

Check size and responsibility of every file you touched:

| Metric | Limit | Action if exceeded |
|---|---|---|
| File LOC | < 400 | Find natural split point, extract to package |
| Function LOC | < 50 | Extract helper functions |
| Function arguments | < 7 | Use a dataclass for params |
| Distinct responsibilities per file | < 5 | Split into focused modules |
| Cyclomatic complexity per function | < 10 | Refactor with early returns or polymorphism |

**Why**: Large files cause hallucination. When Claude (or a human) sees `ocr.py 819 LOC`, the model can't hold the whole context — it makes up function signatures or duplicates logic. Small focused modules = reliable edits.

---

### Gate 4: CLI/Web Parity Gate
> **Run as part of Senior Review 1 for every phase touching pipeline logic.**

For every change touching pipeline behavior, verify both entry points behave identically:

```bash
# Both must import from the same source module
grep -n "from pipeline" video_translator.py
grep -n "from pipeline" web/pipeline_runner.py

# Neither should have local copies of constants/sets/dicts that exist in pipeline/
grep -n "^[A-Z_]* = {" video_translator.py
grep -n "^[A-Z_]* = {" web/routes.py
grep -n "^[A-Z_]* = {" web/pipeline_runner.py
```

**Rule**: If a constant or behavior decision exists in BOTH CLI and Web independently, it's a drift waiting to happen. Extract to `pipeline/` shared module.

---

### Gate 5: Phase Completion Gate
> **Run BEFORE moving to next phase.**

After implementing a phase, before declaring done:

1. **Run regression suite**:
   ```bash
   pytest tests/ -v
   # All tests must pass
   ```

2. **Run drift checks** (Gate 1 retroactively):
   ```bash
   # For each hardcoded value you migrated, grep one more time
   grep -rn "<old_value>" --include="*.py" .
   # Expected: 0 matches outside the new registry/config
   ```

3. **Manual smoke test**:
   - CLI: at least 1 command from regression baseline
   - Web: at least 1 upload→process→download cycle

4. **Write phase report** using `docs/execution/phase-report-template.md`:
   - All sections filled
   - Evidence attached (grep output, test output, screenshots)
   - Files Changed table complete
   - Sign-off section: PASS / FAIL / CONDITIONAL PASS

5. **Update `docs/execution/phase-status.md`**:
   - Status column: `In Progress` → `Done` or `Done w/ Exception`
   - QA Gate column: `PASS` / `FAIL`
   - End date filled

6. **Commit** with conventional prefix:
   - `feat:` new feature
   - `refactor:` no behavior change
   - `fix:` bug fix
   - `test:` test only
   - `docs:` docs only
   - `chore:` build/deps/config

7. **Only THEN** start the next phase.

---

## Phase Template (Copy For Every New Phase)

```markdown
### P{X}.{Y} — {Phase Name}
> One-line goal

**Scope:**
1. ...
2. ...

**Files to create:**
- `path/to/new_file.py`

**Files to modify:**
- `path/to/existing.py` — what change

**Files to remove:**
- `path/to/dead.py` (if any)

**Regression:**
- CLI: `python video_translator.py test.mp4 -t vi` → expected output
- CLI: `python video_translator.py test.mp4 -t vi --dub --bgm music.mp3` → expected
- Web: Upload → process → download → expected files
- Cache: re-run → cache hit, faster

**QA 3-Step:**
1. **Senior Review 1 (Architecture)**:
   - [ ] Module isolation
   - [ ] No CLI/Web drift (Gate 4)
   - [ ] No hardcoded values (Gate 1)
   - [ ] No duplicate metadata (Gate 2)
   - [ ] File LOC < 400 (Gate 3)
2. **QA Functional**:
   - [ ] Happy path
   - [ ] Empty state
   - [ ] Error state
   - [ ] Edge cases
   - [ ] Cross-mode (CLI = Web output)
3. **Senior Review 2 (Sign-off)**:
   - [ ] All acceptance criteria met
   - [ ] No regression
   - [ ] Phase report written
   - [ ] phase-status.md updated
   - [ ] Commit message follows convention
```

---

## Anti-Patterns (Things That Caused P3.1 Miss)

### ❌ "I'll grep just the obvious places"
```bash
# WRONG: only grepping pipeline/
grep -rn "drawtext" pipeline/

# RIGHT: grep the whole repo
grep -rn "drawtext\|DRAWTEXT" --include="*.py" .
```

### ❌ "CLI is a thin entry point, doesn't need migration"
P3.1's mistake. CLI entry point also has business logic if you don't extract it. **Always check entry points last** in any migration.

### ❌ "Tests pass, so I'm done"
60 tests passed in P3.6. None of them caught the CLI drift. **Tests are necessary but not sufficient.** Drift checks (Gate 1) are required.

### ❌ "Same dict in 2 files is fine, easier to read"
False economy. Drift is guaranteed within 2 commits. **Extract to registry first**, then read as needed.

### ❌ "I'll skip the phase report, it's just docs"
Phase reports are the QA evidence. Without them, you can't prove the gates were run. **Always write the report**, even short.

---

## Function Modularization Rules (Anti-Hallucination)

When functions get long, models start hallucinating. Apply these rules to any function > 30 LOC:

1. **Extract pure helpers**: Math/parsing/formatting → standalone functions
2. **Extract I/O boundaries**: File read/write, network calls → separate functions
3. **Extract validation**: Input checks → guard clause functions that raise typed errors
4. **Use early returns**: Reduce nesting
5. **Use dataclasses for params**: When > 5 args, group into a dataclass

**Example before/after:**
```python
# BAD: 80 LOC, 12 args, hard to test, easy to hallucinate
def build_dubbed_audio(segments, lang, output_path, output_dir, voice, mode, bgm, vol, orig, dur, batch, fade):
    # ... 80 lines of mixed concerns ...

# GOOD: 20 LOC orchestrator + 5 focused helpers
def build_dubbed_audio(params: DubParams) -> str:
    tts_files = generate_tts_segments(params.segments, params.voice)
    adjusted = speed_adjust_all(tts_files, params.timings)
    silent_track = concat_with_silence(adjusted, params.duration)
    mixed = mix_with_bgm(silent_track, params.bgm, params.mode)
    return apply_limiter(mixed, params.output_path)
```

Each helper has 1 responsibility, 1-3 args, < 30 LOC, easy to mock and test.

---

## Skill Inheritance

This skill applies to:
- Every new phase in `docs/execution/phase-status.md`
- Every PR/commit touching pipeline logic
- Every refactor task
- Every bug fix (Gate 1 catches "fix in 1 place, miss the other")

Skip only for:
- Pure documentation changes
- Test-only additions (still run Gate 5: tests must pass)
- Single-line typo fixes
