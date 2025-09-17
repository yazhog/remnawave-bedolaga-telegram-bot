import asyncio
import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, Optional

from app.config import settings

logger = logging.getLogger(__name__)

_localization_cache: Dict[str, Dict[str, Any]] = {}
_cached_rules: Dict[str, str] = {}
_SETTINGS_PLACEHOLDER_PATTERN = re.compile(r"\{\{([A-Z0-9_]+)\}\}")


def _resolve_localization_directories() -> list[Path]:
    directories: list[Path] = []

    configured_dir = Path(settings.LOCALIZATION_DIR)
    if not configured_dir.is_absolute():
        configured_dir = Path(__file__).resolve().parents[2] / configured_dir

    try:
        configured_dir.mkdir(parents=True, exist_ok=True)
    except Exception as exc:  # pragma: no cover - best effort only
        logger.debug("Failed to ensure localization directory %s exists: %s", configured_dir, exc)

    directories.append(configured_dir)

    default_dir = Path(__file__).resolve().parents[2] / "locales"
    if default_dir not in directories:
        directories.append(default_dir)

    return directories


def _load_localization_file(language: str) -> Optional[Dict[str, Any]]:
    language = (language or "").strip().lower()

    for directory in _resolve_localization_directories():
        file_path = directory / f"{language}.json"
        if not file_path.exists():
            continue

        try:
            with file_path.open("r", encoding="utf-8") as file:
                data = json.load(file)
                logger.debug("Loaded localization file: %s", file_path)
                return data
        except Exception as exc:
            logger.error("Failed to load localization file %s: %s", file_path, exc)

    return None


def _build_settings_context() -> Dict[str, str]:
    context: Dict[str, str] = {}

    for period in (14, 30, 60, 90, 180, 360):
        attr = f"PRICE_{period}_DAYS"
        if hasattr(settings, attr):
            price_kopeks = getattr(settings, attr)
            context[attr] = settings.format_price(price_kopeks)

    traffic_keys = [5, 10, 25, 50, 100, 250, 500, 1000]
    for traffic in traffic_keys:
        attr = f"PRICE_TRAFFIC_{traffic}GB"
        if hasattr(settings, attr):
            price_kopeks = getattr(settings, attr)
            context[attr] = settings.format_price(price_kopeks)

    if hasattr(settings, "PRICE_TRAFFIC_UNLIMITED"):
        context["PRICE_TRAFFIC_UNLIMITED"] = settings.format_price(settings.PRICE_TRAFFIC_UNLIMITED)

    context["SUPPORT_USERNAME"] = settings.SUPPORT_USERNAME

    return context


def _apply_settings_placeholders(data: Dict[str, Any]) -> Dict[str, Any]:
    context = _build_settings_context()

    def _replace_value(value: Any) -> Any:
        if isinstance(value, str):
            return _SETTINGS_PLACEHOLDER_PATTERN.sub(
                lambda match: context.get(match.group(1), match.group(0)),
                value,
            )
        if isinstance(value, dict):
            return {key: _replace_value(inner) for key, inner in value.items()}
        if isinstance(value, list):
            return [_replace_value(item) for item in value]
        return value

    return {key: _replace_value(val) for key, val in data.items()}


def _load_localization(language: str) -> Dict[str, Any]:
    normalized_language = (language or settings.DEFAULT_LANGUAGE or "ru").lower()

    if normalized_language in _localization_cache:
        return _localization_cache[normalized_language]

    data = _load_localization_file(normalized_language)

    if data is None and normalized_language != (settings.DEFAULT_LANGUAGE or "ru").lower():
        fallback_language = (settings.DEFAULT_LANGUAGE or "ru").lower()
        fallback_data = _load_localization(fallback_language)
        _localization_cache[normalized_language] = fallback_data
        return fallback_data

    if data is None:
        logger.warning("Localization for '%s' not found. Using empty fallback.", normalized_language)
        data = {}

    processed_data = _apply_settings_placeholders(data)
    _localization_cache[normalized_language] = processed_data
    return processed_data


class Texts:
    def __init__(self, language: str = "ru"):
        self.language = (language or settings.DEFAULT_LANGUAGE or "ru").lower()
        self._data = _load_localization(self.language)
        self._fallback_data = _load_localization((settings.DEFAULT_LANGUAGE or "ru").lower())

    def __getattr__(self, item: str) -> Any:
        if item in {"language", "_data", "_fallback_data"}:
            return super().__getattribute__(item)

        if item in self._data:
            return self._data[item]

        if item in self._fallback_data:
            return self._fallback_data[item]

        raise AttributeError(f"Text '{item}' not found for language '{self.language}'")

    def get(self, key: str, default: Any = None) -> Any:
        if key in self._data:
            return self._data[key]
        if key in self._fallback_data:
            return self._fallback_data[key]
        return default

    @property
    def RULES_TEXT(self) -> str:
        normalized_language = self.language

        if normalized_language in _cached_rules:
            return _cached_rules[normalized_language]

        if "RULES_TEXT" in self._data:
            return self._data["RULES_TEXT"]

        return self._fallback_data.get("RULES_TEXT", "")

    @staticmethod
    def format_price(kopeks: int) -> str:
        return settings.format_price(kopeks)

    @staticmethod
    def format_traffic(gb: float) -> str:
        if gb == 0:
            return "∞ (безлимит)"
        if gb >= 1024:
            return f"{gb / 1024:.1f} ТБ"
        return f"{gb:.0f} ГБ"


def get_texts(language: str = "ru") -> Texts:
    available_languages = {lang.lower() for lang in settings.get_available_languages()}
    normalized_language = (language or settings.DEFAULT_LANGUAGE or "ru").lower()

    if normalized_language not in available_languages:
        normalized_language = (settings.DEFAULT_LANGUAGE or "ru").lower()

    return Texts(normalized_language)


def _get_default_rules(language: str = "ru") -> str:
    data = _load_localization(language)
    if "RULES_TEXT" in data:
        return data["RULES_TEXT"]

    fallback_language = (settings.DEFAULT_LANGUAGE or "ru").lower()
    fallback_data = _load_localization(fallback_language)
    return fallback_data.get("RULES_TEXT", "")


async def get_rules_from_db(language: str = "ru") -> str:
    normalized_language = (language or settings.DEFAULT_LANGUAGE or "ru").lower()

    try:
        from app.database.database import get_db
        from app.database.crud.rules import get_current_rules_content

        async for db in get_db():
            rules = await get_current_rules_content(db, normalized_language)
            if rules:
                _cached_rules[normalized_language] = rules
                return rules
            break

    except Exception as exc:  # pragma: no cover - database errors should not break the bot
        logger.error("Failed to fetch rules from database: %s", exc)

    default_rules = _get_default_rules(normalized_language)
    _cached_rules[normalized_language] = default_rules
    return default_rules


def get_rules_sync(language: str = "ru") -> str:
    normalized_language = (language or settings.DEFAULT_LANGUAGE or "ru").lower()

    try:
        if normalized_language in _cached_rules:
            return _cached_rules[normalized_language]

        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            rules = loop.run_until_complete(get_rules_from_db(normalized_language))
            return rules
        finally:
            loop.close()

    except Exception as exc:  # pragma: no cover - graceful fallback
        logger.error("Failed to fetch rules synchronously: %s", exc)
        return _get_default_rules(normalized_language)


async def refresh_rules_cache(language: str = "ru"):
    normalized_language = (language or settings.DEFAULT_LANGUAGE or "ru").lower()
    try:
        if normalized_language in _cached_rules:
            del _cached_rules[normalized_language]

        await get_rules_from_db(normalized_language)
        logger.info("Rules cache refreshed for language '%s'", normalized_language)
    except Exception as exc:  # pragma: no cover
        logger.error("Failed to refresh rules cache: %s", exc)


def clear_rules_cache():
    _cached_rules.clear()
    logger.info("Rules cache cleared")


def clear_localization_cache(language: Optional[str] = None):
    if language is None:
        _localization_cache.clear()
        logger.info("Localization cache cleared")
    else:
        normalized_language = (language or settings.DEFAULT_LANGUAGE or "ru").lower()
        if normalized_language in _localization_cache:
            del _localization_cache[normalized_language]
            logger.info("Localization cache cleared for language '%s'", normalized_language)


def refresh_localization_cache(language: Optional[str] = None):
    clear_localization_cache(language)
