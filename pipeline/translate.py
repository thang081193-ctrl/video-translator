"""Translation pipeline — public API for subtitle translation.

Delegates to provider implementations in pipeline/providers/.
"""

import json
import os
import re
import time

from pipeline.config import cfg
from pipeline.errors import FatalError
from pipeline.logger import get_logger
from pipeline.providers.base import TranslationProvider
from pipeline.providers.factory import load_keys, build_rotator

log = get_logger("Translate")


# ─── Prompt building ─────────────────────────────────────────────────────────

def _build_prompt(texts: list[str], source_lang: str, target_lang: str,
                  context_before: list[str] = None, context_after: list[str] = None) -> str:
    """Build translation prompt."""
    prompt_parts = [
        f"Translate the following subtitle segments from {source_lang} to {target_lang}.",
        "Keep translations natural and concise (suitable for subtitles).",
        "Maintain the same meaning and tone.",
        "Return ONLY a JSON array of translated strings, matching the input order.",
        "Do not include any other text, explanation, or markdown formatting.",
    ]

    if context_before:
        prompt_parts.append(f"\nContext (previous segments, do NOT translate these):")
        for i, t in enumerate(context_before):
            prompt_parts.append(f"  [{i+1}] \"{t}\"")

    prompt_parts.append(f"\nSegments to translate:")
    for i, t in enumerate(texts):
        prompt_parts.append(f"  {i+1}. \"{t}\"")

    if context_after:
        prompt_parts.append(f"\nContext (following segments, do NOT translate these):")
        for i, t in enumerate(context_after):
            prompt_parts.append(f"  [{i+1}] \"{t}\"")

    return "\n".join(prompt_parts)


# ─── Response parsing ────────────────────────────────────────────────────────

def _parse_json_array(text: str) -> list[str] | None:
    """Try to parse a JSON array from LLM response, with fallback regex."""
    text = text.strip()
    # Remove markdown code fences if present
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    text = text.strip()

    try:
        result = json.loads(text)
        if isinstance(result, list):
            return [str(item) for item in result]
    except json.JSONDecodeError:
        pass

    # Fallback: try to find JSON array in the text
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if match:
        try:
            result = json.loads(match.group())
            if isinstance(result, list):
                return [str(item) for item in result]
        except json.JSONDecodeError:
            pass

    return None


# ─── Public API ──────────────────────────────────────────────────────────────

def translate_segments(
    segments: list[dict],
    source_lang: str,
    target_lang: str,
    api_key: str | None = None,
    batch_size: int = 20,
    cache_path: str | None = None,
    use_cache: bool = True,
) -> list[dict]:
    """
    Translate segments using Grok/Gemini/Vertex API with automatic key rotation.

    Each segment dict must have "text" key. Returns a new list with
    "translated_text" added to each segment.
    """
    # Load from cache
    if use_cache and cache_path and os.path.isfile(cache_path):
        log.info(f"Loading cached translation: {cache_path}")
        with open(cache_path, "r", encoding="utf-8") as f:
            return json.load(f)

    # Init key rotator with provider instances
    if api_key:
        # Auto-detect provider from key prefix
        provider = "grok" if api_key.startswith("xai-") else "gemini"
        keys = [{"provider": provider, "key": api_key}]
    else:
        keys = load_keys()

    rotator = build_rotator(keys)

    grok_count = sum(1 for k in rotator.keys if k["provider"] == "grok")
    gemini_count = sum(1 for k in rotator.keys if k["provider"] == "gemini")
    vertex_count = sum(1 for k in rotator.keys if k["provider"] == "vertex")
    log.info(f"Loaded {len(rotator.keys)} API key(s) [Grok={grok_count}, Gemini={gemini_count}, Vertex={vertex_count}]")

    translated_segments = [dict(seg) for seg in segments]
    texts = [seg["text"] for seg in segments]

    # Batch translate
    ctx_window = cfg.translate.context_window
    total_batches = (len(texts) + batch_size - 1) // batch_size
    for batch_idx in range(total_batches):
        start = batch_idx * batch_size
        end = min(start + batch_size, len(texts))
        batch_texts = texts[start:end]

        # Context segments before/after the batch
        context_before = texts[max(0, start - ctx_window):start] if start > 0 else None
        context_after = texts[end:end + ctx_window] if end < len(texts) else None

        prompt = _build_prompt(batch_texts, source_lang, target_lang, context_before, context_after)

        log.info(f"Translating batch {batch_idx + 1}/{total_batches} "
                 f"({len(batch_texts)} segments) via {rotator.current_provider_label}...")

        # Retry with key rotation + backoff
        translations = None
        attempts_per_key = cfg.translate.attempts_single_key if len(rotator.keys) == 1 else cfg.translate.attempts_multi_key
        max_total_attempts = len(rotator.keys) * attempts_per_key
        start_key_index = rotator.index
        last_error: str | None = None

        for attempt in range(max_total_attempts):
            try:
                response_text = rotator.generate(prompt)
                translations = _parse_json_array(response_text)

                if translations and len(translations) == len(batch_texts):
                    break

                mismatch_msg = (
                    f"Translation count mismatch (got {len(translations) if translations else 0}, "
                    f"expected {len(batch_texts)})"
                )
                log.warning(f"{mismatch_msg}, retrying...")
                last_error = mismatch_msg
                translations = None

            except Exception as e:
                error_str = str(e)
                last_error = TranslationProvider.compact_error(error_str)
                is_rate_limit = TranslationProvider.is_rate_limit_error(error_str)

                if is_rate_limit:
                    default_delay = cfg.translate.retry_delay_single if len(rotator.keys) == 1 else cfg.translate.retry_delay_multi
                    retry_after = TranslationProvider.extract_retry_seconds(error_str, default=default_delay)
                    log.warning(f"Rate limit/quota from {rotator.current_provider_label}: {last_error}")
                    if len(rotator.keys) == 1:
                        log.info(f"Single API key detected. Waiting {retry_after}s before retry...")
                        time.sleep(retry_after)
                    else:
                        rotator.rotate()
                        if rotator.rotate_all_exhausted(start_key_index) and attempt > 0:
                            log.warning(f"All keys rate-limited. Waiting {retry_after}s...")
                            time.sleep(retry_after)
                            start_key_index = rotator.index
                else:
                    wait_time = 2 ** min(attempt + 1, 4)
                    log.warning(f"API error ({rotator.current_provider_label}): {e}. Retrying in {wait_time}s...")
                    time.sleep(wait_time)

        if translations is None:
            # Retry loop has already exhausted max_total_attempts. Raising
            # TransientError here would mislead any outer retry decorator
            # into trying again — we just finished trying. FatalError is
            # honest: "we tried, we failed, escalate to user".
            if last_error and TranslationProvider.is_rate_limit_error(last_error):
                raise FatalError(
                    f"API quota/rate limit hit while translating batch {batch_idx + 1}. "
                    f"Attempts: {max_total_attempts} across {len(rotator.keys)} key(s). "
                    f"Add more keys to GROK_API_KEYS / GEMINI_API_KEYS / VERTEX_API_KEYS or wait and retry. "
                    f"Last API error: {last_error}"
                )
            raise FatalError(
                f"Failed to translate batch {batch_idx + 1} after {max_total_attempts} attempts "
                f"across {len(rotator.keys)} key(s). Last error: {last_error or 'unknown'}"
            )

        for i, translated_text in enumerate(translations):
            translated_segments[start + i]["translated_text"] = translated_text

    log.info(f"Translated {len(segments)} segments")

    # Save cache
    if cache_path:
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(translated_segments, f, ensure_ascii=False, indent=2)
        log.info(f"Cached translation: {cache_path}")

    return translated_segments
