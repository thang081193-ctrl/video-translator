import os


def _format_timestamp(seconds: float) -> str:
    """Convert seconds to SRT timestamp format: HH:MM:SS,mmm"""
    total_ms = int(round(seconds * 1000))
    hours = total_ms // 3_600_000
    total_ms %= 3_600_000
    minutes = total_ms // 60_000
    total_ms %= 60_000
    secs = total_ms // 1000
    millis = total_ms % 1000
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _break_lines(text: str, max_chars: int = 42) -> str:
    """Break long text into 2 lines at a word boundary."""
    if len(text) <= max_chars:
        return text

    words = text.split()
    mid = len(text) // 2

    # Find the best split point near the middle
    best_pos = 0
    current_pos = 0
    for word in words:
        current_pos += len(word) + 1  # +1 for space
        if abs(current_pos - mid) < abs(best_pos - mid):
            best_pos = current_pos

    line1 = text[:best_pos].strip()
    line2 = text[best_pos:].strip()
    return f"{line1}\n{line2}"


def generate_srt(segments: list[dict], output_path: str, text_key: str = "translated_text"):
    """
    Generate an SRT subtitle file from segments.

    Args:
        segments: List of dicts with "start", "end", and text_key.
        output_path: Path to write the .srt file.
        text_key: Key to use for subtitle text (default: "translated_text",
                  use "text" for original transcript).
    """
    lines = []
    index = 0
    for seg in segments:
        text = seg.get(text_key, seg.get("text", ""))
        if not text:
            continue

        index += 1
        start_ts = _format_timestamp(seg["start"])
        end_ts = _format_timestamp(seg["end"])
        text = _break_lines(text)

        lines.append(f"{index}")
        lines.append(f"{start_ts} --> {end_ts}")
        lines.append(text)
        lines.append("")  # blank line separator

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"  Generated SRT: {output_path} ({index} entries)")
