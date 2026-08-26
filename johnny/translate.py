"""Перевод коротких строк моделью. Пока только для меню школьной столовой.

Отдельный маленький модуль, а не вызов brain_groq, по одной причине: этот код
работает в облаке, где стоят только pyyaml и tzdata. brain_groq ходит через
http_client, то есть через requests, которого там нет и ставить его ради двух
строк в сутки незачем. Здесь тот же запрос на голом urllib — как в notify.py
и weather.py, и по той же причине.

ПЕРЕВОДИТСЯ ТОЛЬКО МЕНЮ. Названия предметов остаются шведскими — это решение
владельца: именно они стоят у него в расписании и на двери кабинета, и перевод
заставлял бы держать в голове два имени вместо одного. Переименовать отдельный
предмет можно настройкой subject_names, это другое дело и другой механизм.

Ответу модели здесь не доверяют: слишком длинный, многострочный или пустой
перевод отбрасывается, и в сводку идёт шведский оригинал. Цена ошибки мала
(это обед), но «Chili con carne» не должно превращаться в абзац рассуждений.
"""

import json
import logging
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

_URL = "https://api.groq.com/openai/v1/chat/completions"
_TIMEOUT = 12.0

# Groq стоит за Cloudflare, который отдаёт 403 любому клиенту без браузерного
# User-Agent. Заголовок ОБЯЗАТЕЛЕН — в http_client.py об этом оставлена записка
# («на этом уже потеряли час»), и первый же запуск отсюда наступил на те же
# грабли: перевод молча откатывался к шведскому оригиналу.
#
# Строка продублирована, а не взята из http_client, намеренно: тот тянет за
# собой requests, которого в облаке нет и ставить его ради двух строк в сутки
# незачем. Если UA когда-нибудь придётся менять — менять в обоих местах.
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0 Safari/537.36"
)

_PROMPT = (
    "Переведи название блюда со шведского на русский. "
    "Ответь ТОЛЬКО переводом, одной строкой, без пояснений и без кавычек. "
    "Названия, которые в русском звучат как есть (чили кон карне, паста), "
    "оставь узнаваемыми.\n\nБлюдо: {dish}"
)


def make_asker(api_key: str, model: str = "openai/gpt-oss-120b", timeout: float = _TIMEOUT):
    """Функция «промпт → ответ» или None, если ключа нет.

    Такая же по форме, как у brain_groq.make_provider, — чтобы вызывающий код
    не знал, кто именно отвечает, и подменялся в тестах обычной лямбдой.
    """
    if not api_key:
        return None

    def ask(prompt: str) -> str:
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            # Перевод названия блюда — не место для фантазии.
            "temperature": 0.1,
            # ПОТОЛОК ЩЕДРЫЙ НАМЕРЕННО, и это не расточительность. gpt-oss —
            # модель с рассуждением, и рассуждение тратит тот же лимит, что и
            # ответ. С max_tokens: 60 замер 26.08.2026 дал finish_reason=length
            # и ПУСТОЙ content: все 60 токенов ушли в размышление, до ответа не
            # дошло. Маленький потолок здесь даёт не короткий ответ, а никакого.
            "max_tokens": 300,
            # Рассуждать над названием блюда незачем: с low тот же ответ
            # укладывается в 49 токенов вместо 93.
            "reasoning_effort": "low",
        }
        request = urllib.request.Request(
            _URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
                "User-Agent": _USER_AGENT,
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
        return body["choices"][0]["message"]["content"] or ""

    return ask


def dish_to_russian(dish: str, ask=None) -> str:
    """Название блюда по-русски. Не смог — возвращает шведский оригинал.

    Оригинал вместо ошибки: непереведённое меню человек прочтёт, отсутствующее
    меню — нет, а падать из-за обеда сводка тем более не должна.
    """
    dish = (dish or "").strip()
    if not dish or ask is None:
        return dish
    try:
        ответ = ask(_PROMPT.format(dish=dish))
    except (urllib.error.URLError, OSError, ValueError, KeyError, TypeError) as exc:
        logger.warning("Меню не перевелось (%s) — оставляю по-шведски", exc)
        return dish
    перевод = _clean(ответ)
    if not _sane(перевод, dish):
        logger.warning("Перевод меню отброшен как неправдоподобный: %r", ответ)
        return dish
    return перевод


def _clean(ответ: str) -> str:
    """Первая непустая строка без кавычек: модель любит добавлять пояснение."""
    for строка in (ответ or "").splitlines():
        строка = строка.strip().strip('"«»').strip()
        if строка:
            return строка
    return ""


def _sane(перевод: str, оригинал: str) -> bool:
    """Похоже ли это на перевод названия блюда, а не на рассуждение о нём.

    Порог щедрый — кириллица длиннее латиницы, а «Chicken nuggets med sötsur
    sås & ris» по-русски заметно длиннее. Но втрое длиннее оригинала название
    блюда не бывает: это уже пересказ.
    """
    if not перевод:
        return False
    return len(перевод) <= max(60, len(оригинал) * 3)
