import json
import os
import re
import time

from google import genai


class KeyRotator:
    """Round-robin API key rotator with automatic switch on rate limit."""

    def __init__(self, keys: list[str]):
        if not keys:
            raise ValueError("No API keys provided. Set GEMINI_API_KEYS in .env")
        self.keys = keys
        self.index = 0
        self.clients = {}  # lazy init per key

    @property
    def current_key(self) -> str:
        return self.keys[self.index]

    def get_client(self) -> genai.Client:
        key = self.current_key
        if key not in self.clients:
            self.clients[key] = genai.Client(api_key=key)
        return self.clients[key]

    def rotate(self):
        """Switch to next key. Returns True if we haven't looped back to start."""
        prev = self.index
        self.index = (self.index + 1) % len(self.keys)
        key_num = self.index + 1
        total = len(self.keys)
        print(f"  Switching to API key {key_num}/{total}")
        return self.index != prev  # True = still have keys to try

    def rotate_all_exhausted(self, start_index: int) -> bool:
        """Check if we've tried all keys (full loop)."""
        return self.index == start_index


def _load_keys() -> list[str]:
    """Load API keys from env. Supports both GEMINI_API_KEYS (comma-sep) and legacy GEMINI_API_KEY."""
    keys_str = os.getenv("GEMINI_API_KEYS", "")
    if keys_str:
        keys = [k.strip() for k in keys_str.split(",") if k.strip()]
        if keys:
            return keys

    # Fallback to single key
    single = os.getenv("GEMINI_API_KEY", "").strip()
    if single:
        return [single]

    raise ValueError("No API keys set. Add GEMINI_API_KEYS=key1,key2,... to .env")


def _build_prompt(texts: list[str], source_lang: str, target_lang: str, context_before: list[str] = None, context_after: list[str] = None) -> str:
    """Build translation prompt for Gemini."""
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
    Translate segments using Gemini API with automatic key rotation.

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
        rotator = KeyRotator([api_key])
    else:
        rotator = KeyRotator(_load_keys())

    print(f"  Loaded {len(rotator.keys)} API key(s)")
    model = "gemini-2.0-flash"

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

        print(f"  Translating batch {batch_idx + 1}/{total_batches} ({len(batch_texts)} segments)...")

        # Retry with key rotation + backoff
        translations = None
        max_total_attempts = len(rotator.keys) * 3  # 3 attempts per key
        start_key_index = rotator.index

        for attempt in range(max_total_attempts):
            try:
                client = rotator.get_client()
                response = client.models.generate_content(
                    model=model,
                    contents=prompt,
                )
                translations = _parse_json_array(response.text)

                if translations and len(translations) == len(batch_texts):
                    break

                print(f"  Translation count mismatch (got {len(translations) if translations else 0}, expected {len(batch_texts)}), retrying...")
                translations = None

            except Exception as e:
                error_str = str(e)
                is_rate_limit = "429" in error_str or "RESOURCE_EXHAUSTED" in error_str

                if is_rate_limit:
                    # Try next key immediately
                    rotator.rotate()
                    if rotator.rotate_all_exhausted(start_key_index) and attempt > 0:
                        # All keys exhausted in this round, wait before retrying
                        wait_time = 5
                        print(f"  All keys rate-limited. Waiting {wait_time}s...")
                        time.sleep(wait_time)
                        start_key_index = rotator.index  # reset loop detection
                else:
                    wait_time = 2 ** min(attempt + 1, 4)
                    print(f"  API error: {e}. Retrying in {wait_time}s...")
                    time.sleep(wait_time)

        if translations is None:
            raise RuntimeError(f"Failed to translate batch {batch_idx + 1} after {max_total_attempts} attempts across {len(rotator.keys)} keys")

        for i, translated_text in enumerate(translations):
            translated_segments[start + i]["translated_text"] = translated_text

    print(f"  Translated {len(segments)} segments")

    # Save cache
    if cache_path:
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(translated_segments, f, ensure_ascii=False, indent=2)
        print(f"  Cached translation: {cache_path}")

    return translated_segments
