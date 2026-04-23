"""Internationalisation (i18n) support.

Loads locale JSON files from the ``locales/`` directory at the project root.
Provides a ``t(key, lang)`` function for use in templates and application code.

Behaviour:
- Keys are looked up in the requested locale first.
- If the key is missing, the English locale is tried as a fallback.
- If the key is missing in English too, the key string itself is returned
  and a WARNING is logged so missing translations are discoverable.
- Locale files are cached in memory after first load. Call
  ``reload_locales()`` to clear the cache (useful in tests).
"""

import json
import logging
from pathlib import Path
from typing import Dict

logger = logging.getLogger("softarr.i18n")

# Resolve relative to this file: src/softarr/core/i18n.py -> project root
_LOCALES_DIR = Path(__file__).resolve().parents[3] / "locales"

# Supported language codes
SUPPORTED_LANGUAGES: frozenset = frozenset(
    {"en", "de", "es", "fr", "it", "ja", "ko", "pt", "zh", "ar"}
)

_cache: Dict[str, Dict[str, str]] = {}


def _load(lang: str) -> Dict[str, str]:
    """Load and cache a locale file. Returns an empty dict on failure."""
    if lang in _cache:
        return _cache[lang]

    path = _LOCALES_DIR / f"{lang}.json"
    if not path.exists():
        logger.warning("Locale file not found: %s", path)
        _cache[lang] = {}
        return {}

    try:
        with path.open(encoding="utf-8") as fh:
            data = json.load(fh)
        _cache[lang] = data
        return data
    except Exception as exc:
        logger.error("Failed to load locale %s: %s", lang, exc)
        _cache[lang] = {}
        return {}


def t(key: str, lang: str = "en") -> str:
    """Translate ``key`` into ``lang``.

    Falls back to English if the key is absent in the requested locale.
    Returns the key itself if even the English locale is missing the key,
    and logs a warning so the gap can be found and addressed.
    """
    locale = _load(lang)
    if key in locale:
        return locale[key]

    if lang != "en":
        en_locale = _load("en")
        if key in en_locale:
            return en_locale[key]
        logger.warning("Missing i18n key: %r (lang=%s, no English fallback)", key, lang)
    else:
        logger.warning("Missing i18n key: %r", key)

    return key


def reload_locales() -> None:
    """Clear the locale cache.

    Intended for use in tests and hot-reload scenarios. The next call to
    ``t()`` will re-read locale files from disk.
    """
    _cache.clear()
