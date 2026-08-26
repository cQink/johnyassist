"""Погода фактами. Без ключей, без советов.

Источник — open-meteo.com: бесплатно, без регистрации и без ключа, что важно
именно здесь. Сводку считает облако, и каждый лишний секрет — это ещё одна
строка в GitHub Secrets, которую надо завести и когда-нибудь продлить.

ФАКТАМИ, А НЕ СОВЕТОМ, и это решение человека, а не упущение. Обсуждался
вариант подавать не «+4, осадки 60 %», а «возьми зонт»; человек его отверг.
Поэтому здесь нет ни одного «стоит», «лучше» и «не забудь»: только числа и
название явления.

Разбор ответа отделён от запроса (`summary` не ходит в сеть) — иначе проверить
формулировку «+12…+21, дождь, осадки 60 %» можно было бы только дождавшись
подходящей погоды.
"""

import json
import logging
import urllib.error
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

_URL = "https://api.open-meteo.com/v1/forecast"
_TIMEOUT = 15.0

# Коды явлений WMO — тем набором, что реально отдаёт open-meteo. Соседние коды
# («слабый дождь», «сильный дождь») сведены к одному слову намеренно: сводку
# читают спросонья, и разница между 61 и 63 там не работает.
_CODES = {
    0: "ясно", 1: "почти ясно", 2: "переменная облачность", 3: "пасмурно",
    45: "туман", 48: "изморозь",
    51: "морось", 53: "морось", 55: "морось",
    56: "ледяная морось", 57: "ледяная морось",
    61: "дождь", 63: "дождь", 65: "сильный дождь",
    66: "ледяной дождь", 67: "ледяной дождь",
    71: "снег", 73: "снег", 75: "сильный снег", 77: "снежная крупа",
    80: "ливень", 81: "ливень", 82: "сильный ливень",
    85: "снегопад", 86: "сильный снегопад",
    95: "гроза", 96: "гроза с градом", 99: "гроза с градом",
}

# Что считается погодой, о которой стоит написать, даже когда в календаре
# пусто. Пороги — не истина, а отправная точка: их правит человек в settings.
# Смысл в том, чтобы «+18, переменная облачность» не будило телефон.
_NOTABLE_CODES = frozenset(_CODES) - {0, 1, 2, 3}
DEFAULT_RAIN_CHANCE = 50
DEFAULT_COLD = -5
DEFAULT_HOT = 27


def fetch(latitude: float, longitude: float, timezone: str, *, days: int = 2, timeout: float = _TIMEOUT) -> dict | None:
    """Сырой прогноз с open-meteo. None — не достучались (это не повод падать).

    Часовой пояс уходит в запрос, а не пересчитывается на месте: «сегодня» у
    прогноза должно совпадать со «сегодня» у человека, а сводку считает машина
    в UTC. Без этого параметра в 01:00 по Стокгольму приехал бы вчерашний день.
    """
    query = urllib.parse.urlencode({
        "latitude": latitude,
        "longitude": longitude,
        "daily": "weather_code,temperature_2m_min,temperature_2m_max,precipitation_probability_max",
        "timezone": timezone,
        "forecast_days": max(1, days),
    })
    try:
        with urllib.request.urlopen(f"{_URL}?{query}", timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        # Молча пропустить погоду — правильнее, чем не отправить сводку: про
        # врача человек узнать должен даже когда метеосервис лежит.
        logger.warning("Погода не пришла: %s", exc)
        return None


def _day(raw: dict | None, index: int) -> dict | None:
    daily = (raw or {}).get("daily") or {}
    try:
        return {
            "code": daily["weather_code"][index],
            "low": daily["temperature_2m_min"][index],
            "high": daily["temperature_2m_max"][index],
            "rain": (daily.get("precipitation_probability_max") or [None] * (index + 1))[index],
        }
    except (KeyError, IndexError, TypeError):
        return None


def summary(raw: dict | None, index: int = 0) -> str:
    """Строка погоды на день index (0 — сегодня). Пустая — сказать нечего.

    Знак у плюсовой температуры ставим явно: «+2 - +7» читается как диапазон,
    а «2 - 7» на телефоне легко принять за что угодно. Разделитель — дефис по
    просьбе владельца (26.08.2026): многоточие на его телефоне читалось хуже.
    """
    day = _day(raw, index)
    if day is None:
        return ""
    части = [f"{_temp(day['low'])} - {_temp(day['high'])}"]
    явление = _CODES.get(day["code"])
    if явление:
        части.append(явление)
    if day["rain"] is not None and day["rain"] >= 20:
        части.append(f"осадки {int(day['rain'])}%")
    return ", ".join(части)


def _temp(value) -> str:
    градусы = int(round(float(value)))
    return f"+{градусы}" if градусы > 0 else str(градусы)


def is_notable(
    raw: dict | None,
    index: int = 0,
    *,
    rain_chance: int = DEFAULT_RAIN_CHANCE,
    cold: float = DEFAULT_COLD,
    hot: float = DEFAULT_HOT,
) -> bool:
    """Стоит ли будить телефон погодой, когда в календаре пусто.

    Ответ «нет» на обычную погоду — это и есть правило «молчать, когда сказать
    нечего». Уведомление, приходящее каждый день без повода, через неделю
    перестают открывать, и тогда пропустят то самое, ради чего всё делалось.
    """
    day = _day(raw, index)
    if day is None:
        return False
    if day["code"] in _NOTABLE_CODES:
        return True
    if day["rain"] is not None and day["rain"] >= rain_chance:
        return True
    return float(day["low"]) <= cold or float(day["high"]) >= hot
