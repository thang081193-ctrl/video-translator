#!/usr/bin/env python3
"""Verify all voice IDs in pipeline/languages.py exist in edge-tts catalog.

Run before releasing a new language: catches typos, deprecated voices,
and locale mismatches before runtime failures.

Usage:
    python scripts/verify_edge_tts_voices.py

Exit codes:
    0 = all voices found
    1 = one or more voices missing
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import edge_tts

from pipeline.languages import LANGUAGES, DEFAULT_VOICES


async def main() -> int:
    print("Fetching edge-tts voice catalog...")
    catalog = await edge_tts.list_voices()
    catalog_ids = {v["ShortName"] for v in catalog}
    print(f"Catalog contains {len(catalog_ids)} voices total\n")

    print(f"Verifying {len(DEFAULT_VOICES)} configured voices...")
    missing: list[tuple[str, str]] = []
    found: list[tuple[str, str]] = []

    for spec in LANGUAGES:
        if spec.voice in catalog_ids:
            found.append((spec.code, spec.voice))
        else:
            missing.append((spec.code, spec.voice))

    # Report
    print(f"\n[{len(found)}/{len(DEFAULT_VOICES)}] OK:")
    for code, voice in found:
        print(f"  {code:5s}  {voice}")

    if missing:
        print(f"\n[{len(missing)}] MISSING:")
        for code, voice in missing:
            print(f"  {code:5s}  {voice}")

            # Suggest alternatives from same locale
            locale_prefix = voice.split("-")[0] + "-" + voice.split("-")[1]
            alternatives = sorted(
                v["ShortName"] for v in catalog
                if v["ShortName"].startswith(locale_prefix)
            )
            if alternatives:
                print(f"         Alternatives in {locale_prefix}: {alternatives[:3]}")
            else:
                print(f"         No alternatives found for locale {locale_prefix}")
        return 1

    print(f"\nAll {len(DEFAULT_VOICES)} voices verified successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
