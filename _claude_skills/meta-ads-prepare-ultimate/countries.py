"""Meta ads target-country tiers (T1 + T2) for IAA monetization.

Used by the `package` step to emit a country targeting list per run, and by Opus
in the creative step to localize headlines / primary texts and pick BGM mood.

TW (Taiwan) and SG (Singapore) are intentionally EXCLUDED from the default core
because they require business address verification for ad accounts. Add them
back explicitly with --extra-countries if the account is verified.
"""
from __future__ import annotations

# 28-country T1+T2 core (validated 2026-05-28, used for Chatify + DecoAI).
T1_T2_CORE = [
    "Australia", "Austria", "Belgium", "Brazil", "Canada", "Denmark",
    "Finland", "France", "Germany", "Hong Kong", "Iceland", "Ireland",
    "Italy", "Japan", "Mexico", "Netherlands", "New Zealand", "Norway",
    "Portugal", "Qatar", "Saudi Arabia", "South Korea", "Spain", "Sweden",
    "Switzerland", "United Arab Emirates", "United Kingdom", "United States",
]

# Optional East-Europe expansion layer.
EAST_EU = [
    "Poland", "Czechia", "Hungary", "Romania", "Greece", "Croatia",
    "Slovakia", "Slovenia", "Lithuania", "Latvia", "Estonia", "Bulgaria",
]


def build_country_list(include_east_eu: bool = False,
                       extra: list[str] | None = None) -> list[str]:
    out = list(T1_T2_CORE)
    if include_east_eu:
        out += EAST_EU
    if extra:
        for c in extra:
            c = c.strip()
            if c and c not in out:
                out.append(c)
    return out
