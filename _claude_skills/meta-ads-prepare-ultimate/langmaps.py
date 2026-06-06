"""Shared language maps for the meta-ads-prepare-ultimate pipeline.

Single source of truth so scan / organize / dub all agree on:
  - ISO 639-1 code  -> native-script folder name   (ISO_TO_FOLDER)
  - native folder    -> 2/3-letter uppercase code   (FOLDER_TO_CODE)
  - native folder    -> ISO code                     (FOLDER_TO_ISO)

Consolidated from the standalone classify / rename-* skills.
"""
from __future__ import annotations

# ISO 639-1 -> native-script folder label (what humans expect to see).
ISO_TO_FOLDER = {
    "en": "English", "vi": "Tiếng Việt", "pt": "Português", "es": "Español",
    "fr": "Français", "de": "Deutsch", "it": "Italiano", "ru": "Русский",
    "ja": "日本語", "ko": "한국어", "zh": "中文", "th": "ไทย",
    "id": "Indonesia", "ar": "العربية", "tr": "Türkçe", "hi": "हिन्दी",
    "nl": "Nederlands", "pl": "Polski", "km": "ខ្មែរ", "ml": "മലയാളം",
    "my": "မြန်မာ", "ne": "नेपाली", "sw": "Kiswahili", "ta": "தமிழ்",
    "te": "తెలుగు", "ur": "اردو", "yo": "Yorùbá", "bn": "বাংলা",
    "fa": "فارسی", "uk": "Українська", "ms": "Melayu", "fil": "Filipino",
    "tl": "Tagalog", "sv": "Svenska", "no": "Norsk", "da": "Dansk",
    "fi": "Suomi", "el": "Ελληνικά", "he": "עברית", "cs": "Čeština",
    "ro": "Română", "hu": "Magyar", "bg": "Български", "sr": "Српски",
    "hr": "Hrvatski", "sk": "Slovenčina", "sl": "Slovenščina", "lt": "Lietuvių",
    "lv": "Latviešu", "et": "Eesti",
}

# native folder label -> short uppercase code used in filenames.
FOLDER_TO_CODE = {
    "English": "EN", "Tiếng Việt": "VI", "Português": "PT", "Español": "ES",
    "Français": "FR", "Deutsch": "DE", "Italiano": "IT", "Русский": "RU",
    "日本語": "JA", "한국어": "KO", "中文": "ZH", "ไทย": "TH",
    "Indonesia": "ID", "العربية": "AR", "Türkçe": "TR", "हिन्दी": "HI",
    "Nederlands": "NL", "Polski": "PL", "ខ្មែរ": "KM", "മലയാളം": "ML",
    "မြန်မာ": "MY", "नेपाली": "NE", "Kiswahili": "SW", "தமிழ்": "TA",
    "తెలుగు": "TE", "اردو": "UR", "Yorùbá": "YO", "বাংলা": "BN",
    "فارسی": "FA", "Українська": "UK", "Melayu": "MS", "Filipino": "FIL",
    "Tagalog": "TL", "Svenska": "SV", "Norsk": "NO", "Dansk": "DA",
    "Suomi": "FI", "Ελληνικά": "EL", "עברית": "HE", "Čeština": "CS",
    "Română": "RO", "Magyar": "HU", "Български": "BG", "Српски": "SR",
    "Hrvatski": "HR", "Slovenčina": "SK", "Slovenščina": "SL", "Lietuvių": "LT",
    "Latviešu": "LV", "Eesti": "ET",
}

FOLDER_TO_ISO = {folder: iso for iso, folder in ISO_TO_FOLDER.items()}


def iso_to_folder(code: str | None) -> str:
    if not code:
        return "Unknown"
    return ISO_TO_FOLDER.get(code, f"Other_{code}")


# ISO 639-1 -> recognizable ENGLISH language name. Used for CAMPAIGN folder labels
# (VOICED_<English>) — the native-script autonyms above are hard to read in a
# deliverable tree (हिन्दी / العربية / বাংলা). Source working folders keep autonyms.
ISO_TO_ENGLISH = {
    "en": "English", "vi": "Vietnamese", "pt": "Portuguese", "es": "Spanish",
    "fr": "French", "de": "German", "it": "Italian", "ru": "Russian",
    "ja": "Japanese", "ko": "Korean", "zh": "Chinese", "th": "Thai",
    "id": "Indonesian", "ar": "Arabic", "tr": "Turkish", "hi": "Hindi",
    "nl": "Dutch", "pl": "Polish", "km": "Khmer", "ml": "Malayalam",
    "my": "Burmese", "ne": "Nepali", "sw": "Swahili", "ta": "Tamil",
    "te": "Telugu", "ur": "Urdu", "yo": "Yoruba", "bn": "Bengali",
    "fa": "Persian", "uk": "Ukrainian", "ms": "Malay", "fil": "Filipino",
    "tl": "Tagalog", "sv": "Swedish", "no": "Norwegian", "da": "Danish",
    "fi": "Finnish", "el": "Greek", "he": "Hebrew", "cs": "Czech",
    "ro": "Romanian", "hu": "Hungarian", "bg": "Bulgarian", "sr": "Serbian",
    "hr": "Croatian", "sk": "Slovak", "sl": "Slovenian", "lt": "Lithuanian",
    "lv": "Latvian", "et": "Estonian",
}


def iso_to_english(code: str | None) -> str:
    """Campaign-folder label = recognizable English name (falls back to the autonym
    folder, then Other_xx)."""
    if not code:
        return "Unknown"
    return ISO_TO_ENGLISH.get(code) or ISO_TO_FOLDER.get(code, f"Other_{code}")


def folder_to_code(folder: str) -> str | None:
    """Folder -> uppercase code. Falls back to Other_xx -> XX."""
    if folder in FOLDER_TO_CODE:
        return FOLDER_TO_CODE[folder]
    if folder.startswith("Other_"):
        return folder.split("_", 1)[1].upper()
    return None
