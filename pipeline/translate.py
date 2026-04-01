import json
import os
import re
import time
import urllib.request
import urllib.error

from google import genai


# ─── Grok (xAI) API caller ────────────────────────────────────────────────────

GROK_ENDPOINT = "https://api.x.ai/v1/chat/completions"
GROK_MODEL = "grok-3-mini-fast"


def _call_grok(api_key: str, prompt: str) -> str:
    """Call xAI Grok API (OpenAI-compatible) and return the text response."""
    payload = json.dumps({
        "model": GROK_MODEL,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a professional subtitle translator. "
                    "Always respond with ONLY a JSON array of translated strings. "
                    "No explanation, no markdown formatting."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.3,
    }).encode("utf-8")

    req = urllib.request.Request(
        GROK_ENDPOINT,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"]
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            pass
        raise RuntimeError(f"Grok API {e.code}: {body}") from e


# ─── Key rotator ──────────────────────────────────────────────────────────────

class KeyRotator:
    """Round-robin API key rotator with automatic switch on rate limit."""

    def __init__(self, keys: list[dict[str, str]]):
        if not keys:
            raise ValueError(
                "No API keys provided. Set GROK_API_KEYS, GEMINI_API_KEYS, "
                "and/or VERTEX_API_KEYS in .env"
            )
        self.keys = keys
        self.index = 0
        self.clients = {}  # lazy init per key (Gemini/Vertex only)

    @property
    def current_key(self) -> dict[str, str]:
        return self.keys[self.index]

    def get_client(self) -> genai.Client | None:
        """Get a genai Client for Gemini/Vertex keys. Returns None for Grok."""
        key_info = self.current_key
        provider = key_info["provider"]
        if provider == "grok":
            return None  # Grok uses direct HTTP calls
        key = key_info["key"]
        client_cache_key = f"{provider}:{key}"
        if client_cache_key not in self.clients:
            if provider == "vertex":
                self.clients[client_cache_key] = genai.Client(vertexai=True, api_key=key)
            else:
                self.clients[client_cache_key] = genai.Client(api_key=key)
        return self.clients[client_cache_key]

    @property
    def current_provider_label(self) -> str:
        provider = self.current_key["provider"]
        labels = {"grok": "Grok", "vertex": "Vertex AI", "gemini": "Gemini"}
        return labels.get(provider, provider)

    def rotate(self):
        """Switch to next key. Returns True if we haven't looped back to start."""
        prev = self.index
        self.index = (self.index + 1) % len(self.keys)
        key_num = self.index + 1
        total = len(self.keys)
        print(f"  Switching to API key {key_num}/{total} ({self.current_provider_label})")
        return self.index != prev

    def rotate_all_exhausted(self, start_index: int) -> bool:
        """Check if we've tried all keys (full loop)."""
        return self.index == start_index


def _split_keys(value: str | None) -> list[str]:
    if not value:
        return []
    return [k.strip() for k in value.split(",") if k.strip()]


def _load_keys() -> list[dict[str, str]]:
    """
    Load translation API keys from env.

    Supported env vars (checked in priority order):
    - GROK_API_KEYS / GROK_API_KEY  (default, recommended)
    - GEMINI_API_KEYS / GEMINI_API_KEY
    - VERTEX_API_KEYS / VERTEX_API_KEY
    """
    loaded: list[dict[str, str]] = []

    # Grok keys (highest priority — loaded first)
    grok_keys = _split_keys(os.getenv("GROK_API_KEYS", ""))
    if not grok_keys:
        grok_single = os.getenv("GROK_API_KEY", "").strip()
        if grok_single:
            grok_keys = [grok_single]
    loaded.extend({"provider": "grok", "key": key} for key in grok_keys)

    # Gemini keys
    gemini_keys = _split_keys(os.getenv("GEMINI_API_KEYS", ""))
    if not gemini_keys:
        gemini_single = os.getenv("GEMINI_API_KEY", "").strip()
        if gemini_single:
            gemini_keys = [gemini_single]
    loaded.extend({"provider": "gemini", "key": key} for key in gemini_keys)

    # Vertex keys
    vertex_keys = _split_keys(os.getenv("VERTEX_API_KEYS", ""))
    if not vertex_keys:
        vertex_single = os.getenv("VERTEX_API_KEY", "").strip()
        if vertex_single:
            vertex_keys = [vertex_single]
    loaded.extend({"provider": "vertex", "key": key} for key in vertex_keys)

    if not loaded:
        raise ValueError(
            "No API keys set. Add GROK_API_KEYS, GEMINI_API_KEYS, "
            "and/or VERTEX_API_KEYS to .env"
        )

    return loaded


def _build_prompt(texts: list[str], source_lang: str, target_lang: str, context_before: list[str] = None, context_after: list[str] = None) -> str:
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


def _compact_error_message(error: Exception | str, max_len: int = 280) -> str:
    """Compact error text for UI/log display."""
    text = str(error)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def _extract_retry_seconds(error_text: str, default: int = 5) -> int:
    """Best-effort parse retry delay from API error text."""
    m = re.search(r"retryDelay\\?\"?\s*[:=]\s*\\?\"?(\d+)s", error_text)
    if m:
        return max(1, int(m.group(1)))
    m = re.search(r"retry(?:ing)?\s+in\s+(\d+)s", error_text, flags=re.IGNORECASE)
    if m:
        return max(1, int(m.group(1)))
    return default


def _is_rate_limit_error(error_str: str) -> bool:
    """Check if error is a rate limit / quota error across all providers."""
    return any(kw in error_str for kw in (
        "429", "RESOURCE_EXHAUSTED", "rate_limit", "too many requests",
        "quota", "rate limit",
    ))


def _generate_content(rotator: KeyRotator, prompt: str, gemini_model: str) -> str:
    """Call the current provider's API and return the raw text response."""
    key_info = rotator.current_key
    provider = key_info["provider"]

    if provider == "grok":
        return _call_grok(key_info["key"], prompt)
    else:
        # Gemini / Vertex
        client = rotator.get_client()
        response = client.models.generate_content(
            model=gemini_model,
            contents=prompt,
        )
        return response.text


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
    Translate segments using Grok/Gemini API with automatic key rotation.

    Each segment dict must have "text" key. Returns a new list with
    "translated_text" added to each segment.
    """
    # Load from cache
    if use_cache and cache_path and os.path.isfile(cache_path):
        print(f"  Loading cached translation: {cache_path}")
        with open(cache_path, "r", encoding="utf-8") as f:
            return json.load(f)

    # Init key rotator
    if api_key:
        # Auto-detect provider from key prefix
        if api_key.startswith("xai-"):
            provider = "grok"
        else:
            provider = "gemini"
        rotator = KeyRotator([{"provider": provider, "key": api_key}])
    else:
        rotator = KeyRotator(_load_keys())

    grok_count = sum(1 for k in rotator.keys if k["provider"] == "grok")
    gemini_count = sum(1 for k in rotator.keys if k["provider"] == "gemini")
    vertex_count = sum(1 for k in rotator.keys if k["provider"] == "vertex")
    print(f"  Loaded {len(rotator.keys)} API key(s) [Grok={grok_count}, Gemini={gemini_count}, Vertex={vertex_count}]")
    gemini_model = "gemini-2.0-flash"

    translated_segments = [dict(seg) for seg in segments]
    texts = [seg["text"] for seg in segments]

    # Batch translate
    total_batches = (len(texts) + batch_size - 1) // batch_size
    for batch_idx in range(total_batches):
        start = batch_idx * batch_size
        end = min(start + batch_size, len(texts))
        batch_texts = texts[start:end]

        # Context: 2 segments before/after the batch
        context_before = texts[max(0, start - 2):start] if start > 0 else None
        context_after = texts[end:end + 2] if end < len(texts) else None

        prompt = _build_prompt(batch_texts, source_lang, target_lang, context_before, context_after)

        print(f"  Translating batch {batch_idx + 1}/{total_batches} "
              f"({len(batch_texts)} segments) via {rotator.current_provider_label}...")

        # Retry with key rotation + backoff
        translations = None
        attempts_per_key = 6 if len(rotator.keys) == 1 else 3
        max_total_attempts = len(rotator.keys) * attempts_per_key
        start_key_index = rotator.index
        last_error: str | None = None

        for attempt in range(max_total_attempts):
            try:
                response_text = _generate_content(rotator, prompt, gemini_model)
                translations = _parse_json_array(response_text)

                if translations and len(translations) == len(batch_texts):
                    break

                mismatch_msg = (
                    f"Translation count mismatch (got {len(translations) if translations else 0}, "
                    f"expected {len(batch_texts)})"
                )
                print(f"  {mismatch_msg}, retrying...")
                last_error = mismatch_msg
                translations = None

            except Exception as e:
                error_str = str(e)
                last_error = _compact_error_message(error_str)
                is_rate_limit = _is_rate_limit_error(error_str)

                if is_rate_limit:
                    retry_after = _extract_retry_seconds(error_str, default=8 if len(rotator.keys) == 1 else 5)
                    print(f"  Rate limit/quota from {rotator.current_provider_label}: {last_error}")
                    if len(rotator.keys) == 1:
                        print(f"  Single API key detected. Waiting {retry_after}s before retry...")
                        time.sleep(retry_after)
                    else:
                        rotator.rotate()
                        if rotator.rotate_all_exhausted(start_key_index) and attempt > 0:
                            print(f"  All keys rate-limited. Waiting {retry_after}s...")
                            time.sleep(retry_after)
                            start_key_index = rotator.index
                else:
                    wait_time = 2 ** min(attempt + 1, 4)
                    print(f"  API error ({rotator.current_provider_label}): {e}. Retrying in {wait_time}s...")
                    time.sleep(wait_time)

        if translations is None:
            if last_error and _is_rate_limit_error(last_error):
                raise RuntimeError(
                    f"API quota/rate limit hit while translating batch {batch_idx + 1}. "
                    f"Attempts: {max_total_attempts} across {len(rotator.keys)} key(s). "
                    f"Add more keys to GROK_API_KEYS / GEMINI_API_KEYS / VERTEX_API_KEYS or wait and retry. "
                    f"Last API error: {last_error}"
                )
            raise RuntimeError(
                f"Failed to translate batch {batch_idx + 1} after {max_total_attempts} attempts "
                f"across {len(rotator.keys)} key(s). Last error: {last_error or 'unknown'}"
            )

        for i, translated_text in enumerate(translations):
            translated_segments[start + i]["translated_text"] = translated_text

    print(f"  Translated {len(segments)} segments")

    # Save cache
    if cache_path:
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(translated_segments, f, ensure_ascii=False, indent=2)
        print(f"  Cached translation: {cache_path}")

    return translated_segments
