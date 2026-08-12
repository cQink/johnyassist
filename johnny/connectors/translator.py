"""Перевод текста: сначала локально, в сеть — только если локально нечем.

Пункт плана назывался MoonMonet/Translator, но это Tauri-приложение с треем и
горячей клавишей: вызвать его из голосовой команды нечем — программного API у
него нет. Из требования «локальная обработка предпочтительна» взято главное, а
не имя репозитория: перевод идёт офлайн-движком на этой машине.

Движок — argostranslate (те же модели OpenNMT/CTranslate2, что Marian из плана).
Запасной вариант — LibreTranslate по HTTP, и это не случайный второй сервис:
LibreTranslate собран на argostranslate, поэтому облачный ответ совпадает с
локальным, а не выглядит переводом «другим голосом». Так «optional local install
or cloud endpoint» из .md закрывается одним коннектором вместо двух.

Согласия не требует: свой текст, отправленный своему же движку, персональных
данных третьих лиц не раскрывает. Но при работе через ЧУЖОЙ адрес текст уходит
наружу, поэтому endpoint пустой по умолчанию — в сеть коннектор сам не пойдёт.
"""

from __future__ import annotations

import importlib

from ..http_client import post
from .base import Connector

# Пусто по умолчанию сознательно: пока адрес не вписан, перевод только офлайн.
# Публичный инстанс здесь по умолчанию значил бы, что любой продиктованный
# текст молча уезжает на чужой сервер.
DEFAULT_ENDPOINT = ""
TIMEOUT_SECONDS = 20.0

# Длинный текст голосом не диктуют и вслух не слушают. Ограничение и от
# случайного «переведи» на весь буфер обмена.
MAX_CHARS = 2000


class TranslatorConnector(Connector):
    name = "translator"
    requires_consent = False
    # Перевод одной и той же фразы не меняется — держим долго.
    ttl_seconds = 30 * 86400.0
    daily_quota = 0
    min_interval_seconds = 0.0

    def __init__(self, *, endpoint: str = DEFAULT_ENDPOINT, api_key: str = "", **kwargs):
        super().__init__(**kwargs)
        self._endpoint = str(endpoint or "").strip()
        self._api_key = str(api_key or "")

    # --- готовность ---

    def available(self) -> tuple[bool, str]:
        if _argos() is not None:
            return True, ""
        if self._endpoint:
            return True, ""
        return False, (
            "Для перевода нужен пакет argostranslate: pip install argostranslate, "
            "либо адрес LibreTranslate в settings.yaml"
        )

    def cache_params(self, params: dict) -> dict:
        """Текст и пара языков. Регистр и пробелы не влияют на перевод, поэтому
        в ключ идёт приведённый вид — иначе «Привет» и «привет  » считались бы
        разными запросами."""
        return {
            "text": " ".join(str(params.get("text", "")).split()).lower(),
            "source": params.get("source", "auto"),
            "target": params.get("target", ""),
        }

    # --- вызов ---

    def _run(self, **params) -> dict:
        text = str(params.get("text", "")).strip()
        if not text:
            raise RuntimeError("нет текста для перевода")
        if len(text) > MAX_CHARS:
            raise RuntimeError(f"текст длиннее {MAX_CHARS} символов")

        source = str(params.get("source") or "auto")
        target = str(params.get("target") or "")
        if not target:
            raise RuntimeError("не задан язык перевода")
        if source == "auto":
            source = detect_language(text)
        if source == target:
            # Не тратим ни модель, ни запрос: «переведи» на тот же язык обычно
            # значит, что язык определился неверно, и честнее это сказать.
            raise RuntimeError(f"текст уже на языке {target}")

        local = _translate_locally(text, source, target)
        if local is not None:
            return {"text": local, "source": source, "target": target, "engine": "local"}
        if not self._endpoint:
            # Пакет есть, а нужной языковой пары в нём нет — это отдельный
            # случай, и человеку надо сказать, что именно доустановить.
            raise RuntimeError(f"нет локальной модели {source}->{target}")
        return {
            "text": self._translate_remotely(text, source, target),
            "source": source,
            "target": target,
            "engine": "remote",
        }

    def _translate_remotely(self, text: str, source: str, target: str) -> str:
        payload = {"q": text, "source": source, "target": target, "format": "text"}
        if self._api_key:
            payload["api_key"] = self._api_key
        response = post(self._endpoint, {}, payload, TIMEOUT_SECONDS)
        if response.status_code != 200:
            # Тело не в тексте ошибки: LibreTranslate возвращает в нём исходный
            # запрос, а с ним и api_key, а текст ошибки уходит в лог.
            raise RuntimeError(f"HTTP {response.status_code}")
        translated = (response.json() or {}).get("translatedText")
        if not translated:
            raise RuntimeError("пустой ответ переводчика")
        return str(translated)

    def describe(self, data: dict) -> str:
        """Только сам перевод. Ни «перевожу с русского на английский», ни имени
        движка: человек попросил перевод, а не отчёт о том, как он получен."""
        return data.get("text", "")


# --- Языки -------------------------------------------------------------------

# Как язык называют вслух → код. Ключ — основа слова без окончания: Whisper
# пишет то «на английский», то «на английском», и держать оба варианта каждого
# языка отдельной строкой значит однажды забыть один из них.
LANGUAGE_STEMS = {
    "англ": "en",
    "русск": "ru",
    "испанск": "es",
    "французск": "fr",
    "немецк": "de",
    "итальянск": "it",
    "польск": "pl",
    "украинск": "uk",
    "турецк": "tr",
    "португальск": "pt",
    "китайск": "zh",
    "японск": "ja",
    "корейск": "ko",
    "арабск": "ar",
}

# Названия для голоса: «перевёл на английский» произносимо, «перевёл на en» нет.
LANGUAGE_NAMES = {
    "en": "английский",
    "ru": "русский",
    "es": "испанский",
    "fr": "французский",
    "de": "немецкий",
    "it": "итальянский",
    "pl": "польский",
    "uk": "украинский",
    "tr": "турецкий",
    "pt": "португальский",
    "zh": "китайский",
    "ja": "японский",
    "ko": "корейский",
    "ar": "арабский",
}


def language_code(said: str) -> str:
    """Код языка по сказанному слову или "" — «английский», «англ», «en»."""
    word = said.strip().lower().replace("ё", "е")
    if word in LANGUAGE_NAMES:
        return word
    for stem, code in LANGUAGE_STEMS.items():
        if word.startswith(stem):
            return code
    return ""


def detect_language(text: str) -> str:
    """Кириллица — русский, иначе английский.

    Нарочно грубо: настоящий детектор — ещё один пакет и ещё одна точка отказа,
    а весь смысл здесь в том, чтобы «переведи hello» и «переведи привет» шли в
    разные стороны без указания языка. Ошибётся он только на языках, которые
    голосом всё равно диктуют с явным «на такой-то».
    """
    return "ru" if any("а" <= ch.lower() <= "я" or ch.lower() == "ё" for ch in text) else "en"


def opposite(source: str) -> str:
    """Куда переводить, когда язык не назван: русский↔английский."""
    return "en" if source == "ru" else "ru"


# --- Локальный движок --------------------------------------------------------


def _argos():
    """Модуль перевода argostranslate или None. Импорт каждый раз — пакет могут
    поставить, не перезапуская Джони, и тогда перевод заработает сам."""
    try:
        return importlib.import_module("argostranslate.translate")
    except Exception:
        return None


def _translate_locally(text: str, source: str, target: str) -> str | None:
    """Перевод офлайн-моделью или None, если пакета либо пары языков нет.

    None вместо исключения: отсутствие пары — не ошибка, а повод попробовать
    удалённый адрес, и call() не должен из-за этого отмечать cooldown.
    """
    module = _argos()
    if module is None:
        return None
    try:
        translated = module.translate(text, source, target)
    except Exception:
        return None
    return str(translated) if translated else None
