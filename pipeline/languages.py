"""Language registry — single source of truth for all language metadata.

This module is the ONLY place where language codes, native names, default TTS
voices, OCR drawtext safety, and script families are defined. All consumers
(web/routes.py, pipeline/dub/tts.py, pipeline/config.py, video_translator.py,
web/pipeline_runner.py) MUST import from here, never duplicate the data.

Adding a new language:
1. Add a `LanguageSpec` entry to `LANGUAGES` below
2. Verify edge-tts voice ID exists: `python scripts/verify_edge_tts_voices.py`
3. If new script family, add font path to pipeline/ocr_render.py FONT_MAP
4. Update tests/test_languages_registry.py count assertion
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LanguageSpec:
    """Metadata for a single supported language."""
    code: str             # ISO 639-1 (e.g. "vi", "en", "zh")
    native_name: str      # Display name in native script
    voice: str            # Default edge-tts voice ID (xx-XX-NameNeural)
    drawtext_safe: bool   # True if ffmpeg drawtext can render correctly
    speaker_rank: int     # 1 = most spoken (English); used for UI ordering
    script_family: str    # latin / cjk / arabic / devanagari / cyrillic / bengali / telugu / tamil / greek /
                          # thai / hebrew / gujarati / kannada / malayalam / georgian / khmer / burmese /
                          # sinhala / lao / ethiopic


# ─── Master registry: top 30 world languages by total speakers ───────────────
# Order here doesn't affect UI (use list_for_ui()). Speaker rank ascending = most spoken first.
# All voice IDs verified by scripts/verify_edge_tts_voices.py.
LANGUAGES: list[LanguageSpec] = [
    LanguageSpec("en", "English",          "en-US-JennyNeural",        True,  1,  "latin"),
    LanguageSpec("zh", "中文",              "zh-CN-XiaoxiaoNeural",     True,  2,  "cjk"),
    LanguageSpec("hi", "हिन्दी",             "hi-IN-SwaraNeural",        False, 3,  "devanagari"),
    LanguageSpec("es", "Español",          "es-ES-ElviraNeural",       False, 4,  "latin"),
    LanguageSpec("ar", "العربية",           "ar-SA-ZariyahNeural",      False, 5,  "arabic"),
    LanguageSpec("fr", "Français",         "fr-FR-DeniseNeural",       False, 6,  "latin"),
    LanguageSpec("bn", "বাংলা",             "bn-IN-TanishaaNeural",     False, 7,  "bengali"),
    LanguageSpec("pt", "Português",        "pt-BR-FranciscaNeural",    False, 8,  "latin"),
    LanguageSpec("ru", "Русский",          "ru-RU-SvetlanaNeural",     False, 9,  "cyrillic"),
    LanguageSpec("ur", "اردو",              "ur-PK-UzmaNeural",         False, 10, "arabic"),
    LanguageSpec("id", "Bahasa Indonesia", "id-ID-GadisNeural",        True,  11, "latin"),
    LanguageSpec("de", "Deutsch",          "de-DE-KatjaNeural",        False, 12, "latin"),
    LanguageSpec("ja", "日本語",            "ja-JP-NanamiNeural",       True,  13, "cjk"),
    LanguageSpec("fa", "فارسی",             "fa-IR-DilaraNeural",       False, 14, "arabic"),
    LanguageSpec("mr", "मराठी",             "mr-IN-AarohiNeural",       False, 15, "devanagari"),
    LanguageSpec("te", "తెలుగు",             "te-IN-ShrutiNeural",       False, 16, "telugu"),
    LanguageSpec("tr", "Türkçe",           "tr-TR-EmelNeural",         False, 17, "latin"),
    LanguageSpec("ta", "தமிழ்",              "ta-IN-PallaviNeural",      False, 18, "tamil"),
    LanguageSpec("vi", "Tiếng Việt",       "vi-VN-HoaiMyNeural",       False, 19, "latin"),
    LanguageSpec("ko", "한국어",            "ko-KR-SunHiNeural",        True,  20, "cjk"),
    LanguageSpec("it", "Italiano",         "it-IT-ElsaNeural",         False, 21, "latin"),
    LanguageSpec("th", "ไทย",              "th-TH-PremwadeeNeural",    False, 22, "thai"),
    LanguageSpec("pl", "Polski",           "pl-PL-ZofiaNeural",        False, 23, "latin"),
    LanguageSpec("uk", "Українська",       "uk-UA-PolinaNeural",       False, 24, "cyrillic"),
    LanguageSpec("nl", "Nederlands",       "nl-NL-FennaNeural",        False, 25, "latin"),
    LanguageSpec("ro", "Română",           "ro-RO-AlinaNeural",        False, 26, "latin"),
    LanguageSpec("el", "Ελληνικά",         "el-GR-AthinaNeural",       False, 27, "greek"),
    LanguageSpec("cs", "Čeština",          "cs-CZ-VlastaNeural",       False, 28, "latin"),
    LanguageSpec("hu", "Magyar",           "hu-HU-NoemiNeural",        False, 29, "latin"),
    LanguageSpec("sv", "Svenska",          "sv-SE-SofieNeural",        False, 30, "latin"),
    # ─── Phase 7: top 30 → 73 (edge-tts coverage expansion) ────────────────
    # Ported from local feature branch. Voice IDs produced by a live
    # `edge_tts.list_voices()` snapshot; all match the xx-XX-NameNeural
    # pattern. OCR drawtext is False for every new entry (conservative);
    # real-script font coverage for new scripts is a future OCR task.
    LanguageSpec("af", "Afrikaans",        "af-ZA-AdriNeural",         False, 31, "latin"),
    LanguageSpec("am", "አማርኛ",             "am-ET-MekdesNeural",       False, 32, "ethiopic"),
    LanguageSpec("az", "Azərbaycan",       "az-AZ-BanuNeural",         False, 33, "latin"),
    LanguageSpec("bg", "Български",        "bg-BG-KalinaNeural",       False, 34, "cyrillic"),
    LanguageSpec("bs", "Bosanski",         "bs-BA-VesnaNeural",        False, 35, "latin"),
    LanguageSpec("ca", "Català",           "ca-ES-JoanaNeural",        False, 36, "latin"),
    LanguageSpec("cy", "Cymraeg",          "cy-GB-NiaNeural",          False, 37, "latin"),
    LanguageSpec("da", "Dansk",            "da-DK-ChristelNeural",     False, 38, "latin"),
    LanguageSpec("et", "Eesti",            "et-EE-AnuNeural",          False, 39, "latin"),
    LanguageSpec("fi", "Suomi",            "fi-FI-NooraNeural",        False, 40, "latin"),
    LanguageSpec("ga", "Gaeilge",          "ga-IE-OrlaNeural",         False, 41, "latin"),
    LanguageSpec("gl", "Galego",           "gl-ES-SabelaNeural",       False, 42, "latin"),
    LanguageSpec("gu", "ગુજરાતી",           "gu-IN-DhwaniNeural",       False, 43, "gujarati"),
    LanguageSpec("he", "עברית",             "he-IL-HilaNeural",         False, 44, "hebrew"),
    LanguageSpec("hr", "Hrvatski",         "hr-HR-GabrijelaNeural",    False, 45, "latin"),
    LanguageSpec("is", "Íslenska",         "is-IS-GudrunNeural",       False, 46, "latin"),
    LanguageSpec("jv", "Basa Jawa",        "jv-ID-SitiNeural",         False, 47, "latin"),
    LanguageSpec("ka", "ქართული",           "ka-GE-EkaNeural",          False, 48, "georgian"),
    LanguageSpec("kk", "Қазақша",           "kk-KZ-AigulNeural",        False, 49, "cyrillic"),
    LanguageSpec("km", "ខ្មែរ",              "km-KH-SreymomNeural",      False, 50, "khmer"),
    LanguageSpec("kn", "ಕನ್ನಡ",              "kn-IN-SapnaNeural",        False, 51, "kannada"),
    LanguageSpec("lo", "ລາວ",               "lo-LA-KeomanyNeural",      False, 52, "lao"),
    LanguageSpec("lt", "Lietuvių",         "lt-LT-OnaNeural",          False, 53, "latin"),
    LanguageSpec("lv", "Latviešu",         "lv-LV-EveritaNeural",      False, 54, "latin"),
    LanguageSpec("mk", "Македонски",       "mk-MK-MarijaNeural",       False, 55, "cyrillic"),
    LanguageSpec("ml", "മലയാളം",           "ml-IN-SobhanaNeural",      False, 56, "malayalam"),
    LanguageSpec("mn", "Монгол",           "mn-MN-YesuiNeural",        False, 57, "cyrillic"),
    LanguageSpec("ms", "Bahasa Melayu",    "ms-MY-YasminNeural",       False, 58, "latin"),
    LanguageSpec("mt", "Malti",            "mt-MT-GraceNeural",        False, 59, "latin"),
    LanguageSpec("my", "မြန်မာ",             "my-MM-NilarNeural",        False, 60, "burmese"),
    LanguageSpec("nb", "Norsk Bokmål",     "nb-NO-PernilleNeural",     False, 61, "latin"),
    LanguageSpec("ne", "नेपाली",             "ne-NP-HemkalaNeural",      False, 62, "devanagari"),
    LanguageSpec("ps", "پښتو",              "ps-AF-LatifaNeural",       False, 63, "arabic"),
    LanguageSpec("si", "සිංහල",              "si-LK-ThiliniNeural",      False, 64, "sinhala"),
    LanguageSpec("sk", "Slovenčina",       "sk-SK-ViktoriaNeural",     False, 65, "latin"),
    LanguageSpec("sl", "Slovenščina",      "sl-SI-PetraNeural",        False, 66, "latin"),
    LanguageSpec("so", "Soomaali",         "so-SO-UbaxNeural",         False, 67, "latin"),
    LanguageSpec("sq", "Shqip",            "sq-AL-AnilaNeural",        False, 68, "latin"),
    LanguageSpec("sr", "Српски",           "sr-RS-SophieNeural",       False, 69, "cyrillic"),
    LanguageSpec("su", "Basa Sunda",       "su-ID-TutiNeural",         False, 70, "latin"),
    LanguageSpec("sw", "Kiswahili",        "sw-KE-ZuriNeural",         False, 71, "latin"),
    LanguageSpec("uz", "Oʻzbek",           "uz-UZ-MadinaNeural",       False, 72, "latin"),
    LanguageSpec("zu", "isiZulu",          "zu-ZA-ThandoNeural",       False, 73, "latin"),
]


# ─── Derived constants — DO NOT define independently ────────────────────────

#: Mapping language code → default edge-tts voice ID. Used by pipeline/dub/tts.py.
DEFAULT_VOICES: dict[str, str] = {spec.code: spec.voice for spec in LANGUAGES}

#: Tuple of language codes where ffmpeg drawtext cannot render correctly.
#: Pipeline auto-upgrades OCR `fast` mode to `quality` for these languages.
DRAWTEXT_UNSAFE_LANGS: tuple[str, ...] = tuple(
    spec.code for spec in LANGUAGES if not spec.drawtext_safe
)

#: Set of all valid language codes. Used for early validation in API/CLI.
ALL_LANGUAGE_CODES: frozenset[str] = frozenset(spec.code for spec in LANGUAGES)

#: Mapping language code → native display name. For frontend dropdowns.
LANGUAGE_NAMES: dict[str, str] = {spec.code: spec.native_name for spec in LANGUAGES}


# ─── Lookup helpers ──────────────────────────────────────────────────────────

def get_language(code: str) -> LanguageSpec | None:
    """Look up a language spec by ISO code. Returns None if not found."""
    for spec in LANGUAGES:
        if spec.code == code:
            return spec
    return None


def is_supported(code: str) -> bool:
    """Check if a language code is in the registry."""
    return code in ALL_LANGUAGE_CODES


def list_for_ui() -> list[dict[str, str]]:
    """Return language list for frontend dropdown.

    Display order: vi (default for VN users) → en → rest by speaker_rank.
    Returns list of {"code": str, "name": str} dicts.
    """
    by_code = {spec.code: spec for spec in LANGUAGES}

    ordered: list[LanguageSpec] = []
    # Pinned languages first (vi for VN users, en universal)
    for pinned_code in ("vi", "en"):
        if pinned_code in by_code:
            ordered.append(by_code[pinned_code])

    # Rest sorted by speaker_rank ascending (most spoken first)
    seen = {spec.code for spec in ordered}
    rest = sorted(
        (spec for spec in LANGUAGES if spec.code not in seen),
        key=lambda s: s.speaker_rank,
    )
    ordered.extend(rest)

    return [{"code": spec.code, "name": spec.native_name} for spec in ordered]
