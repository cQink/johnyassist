"""«в четверг про врача» → дата, текст, время, повтор, предупреждение.

Разбор произнесённой фразы напоминания. Чистые функции: на входе строка и
сегодняшняя дата, на выходе разложенное событие. Часы внутри не читаются
никогда — иначе тест «а что будет 29 февраля» пришлось бы ждать четыре года.

СВОЙ ПАРСЕР, А НЕ МОДЕЛЬ, И ПОЧЕМУ ИМЕННО ТАК. Календарные фразы человек
строит десятком способов, и все они помещаются в таблицу: «завтра», «через
три дня», «в четверг», «пятнадцатого сентября», «каждый год». Модель здесь
проигрывает по всем статьям сразу — полсекунды задержки, нужна сеть, стоит
денег и, главное, ошибается уверенно: перепутанная неделя выглядит как
обычный ответ, и человек узнает об этом, не придя к врачу. Парсер, который
чего-то не понял, честно молчит и отдаёт ход модели (см. parse_with_fallback).

Числительные приходят сюда уже цифрами: router._normalize переводит «пять» в
«5» до всякого разбора. Порядковые («пятнадцатого») он не трогает — их
таблица здесь, и она нужна: дату голосом называют порядковым числом почти
всегда.
"""

import datetime as dt
import json
import logging
import re
from dataclasses import dataclass

from .events import MONTHLY, ONCE, WEEKLY, YEARLY

logger = logging.getLogger(__name__)

_WEEKDAYS = {
    "понедельник": 0, "вторник": 1, "среда": 2, "среду": 2, "четверг": 3,
    "пятница": 4, "пятницу": 4, "суббота": 5, "субботу": 5,
    "воскресенье": 6, "воскресение": 6,
}

_MONTHS = {
    "января": 1, "январе": 1, "январь": 1, "февраля": 2, "феврале": 2, "февраль": 2,
    "марта": 3, "марте": 3, "март": 3, "апреля": 4, "апреле": 4, "апрель": 4,
    "мая": 5, "мае": 5, "май": 5, "июня": 6, "июне": 6, "июнь": 6,
    "июля": 7, "июле": 7, "июль": 7, "августа": 8, "августе": 8, "август": 8,
    "сентября": 9, "сентябре": 9, "сентябрь": 9, "октября": 10, "октябре": 10, "октябрь": 10,
    "ноября": 11, "ноябре": 11, "ноябрь": 11, "декабря": 12, "декабре": 12, "декабрь": 12,
}

# Порядковые числительные 1–31 в родительном падеже — «пятнадцатого сентября».
_ORDINAL_UNITS = {
    "первого": 1, "второго": 2, "третьего": 3, "четвертого": 4, "пятого": 5,
    "шестого": 6, "седьмого": 7, "восьмого": 8, "девятого": 9, "десятого": 10,
    "одиннадцатого": 11, "двенадцатого": 12, "тринадцатого": 13,
    "четырнадцатого": 14, "пятнадцатого": 15, "шестнадцатого": 16,
    "семнадцатого": 17, "восемнадцатого": 18, "девятнадцатого": 19,
    "двадцатого": 20, "тридцатого": 30,
}
_ORDINAL_TENS = {"двадцать": 20, "тридцать": 30}

# Слова, с которых начинается сам текст напоминания и которые в него не входят:
# «напомни в четверг ПРО врача» — врач остаётся, «про» уходит.
_TEXT_LEADERS = ("про", "о", "об", "обо", "что", "чтобы", "мне", "надо", "нужно", ":", ",", "-", "—")


@dataclass(frozen=True)
class Reminder:
    date: dt.date
    text: str
    time: str = ""
    repeat: str = ONCE
    warn: int = 0


def parse(phrase: str, today: dt.date) -> Reminder | None:
    """Разобрать фразу напоминания. None — не нашёл в ней даты.

    Именно None, а не «поставлю на сегодня»: напоминание не в тот день хуже,
    чем отказ. На отказ человек переспросит, подмену он заметит поздно.
    """
    text = _normalize(phrase)
    if not text:
        return None
    text, repeat, weekday_of_repeat = _take_repeat(text)
    text, warn = _take_warn(text)
    text, time = _take_time(text)
    text, date = _take_date(text, today, weekday_of_repeat)
    if date is None:
        return None
    text = _clean_text(text)
    if not text:
        # «напомни в четверг» — дата есть, а напоминать не о чем. Заводить
        # событие с пустым текстом бессмысленно: в сводке оно будет пустой
        # строкой, и человек не вспомнит, о чём речь.
        return None
    if repeat == ONCE and date < today:
        return None
    return Reminder(date=date, text=text, time=time, repeat=repeat, warn=warn)


def parse_with_fallback(phrase: str, today: dt.date, ask=None) -> Reminder | None:
    """Сначала свой разбор, потом — модель, если она передана.

    ask — функция «промпт → текст ответа», ровно такая, какую отдаёт
    brain_groq.make_provider. Передаётся снаружи, а не берётся из конфига:
    так этот модуль не знает ни про ключи, ни про провайдеров, и тестируется
    подставной функцией.
    """
    found = parse(phrase, today)
    if found is not None:
        return found
    if ask is None:
        return None
    return _ask_model(phrase, today, ask)


def _normalize(text: str) -> str:
    text = (text or "").lower().replace("ё", "е").strip()
    return re.sub(r"\s+", " ", text)


def _cut(text: str, match: re.Match) -> str:
    """Вырезать распознанный кусок, чтобы он не попал в текст напоминания."""
    return _normalize(text[: match.start()] + " " + text[match.end():])


def _take_repeat(text: str) -> tuple[str, str, int | None]:
    """Повтор и, если он недельный по имени дня, номер этого дня."""
    match = re.search(r"\bкажд(?:ый|ую|ое)\s+(" + "|".join(_WEEKDAYS) + r")\b", text)
    if match:
        return _cut(text, match), WEEKLY, _WEEKDAYS[match.group(1)]
    match = re.search(r"\b(?:кажд(?:ый|ую|ое)\s+неделю|еженедельно)\b", text)
    if match:
        return _cut(text, match), WEEKLY, None
    match = re.search(r"\b(?:кажд(?:ый|ое)\s+месяц|ежемесячно)\b", text)
    if match:
        return _cut(text, match), MONTHLY, None
    match = re.search(r"\b(?:кажд(?:ый|ое)\s+год|ежегодно)\b", text)
    if match:
        return _cut(text, match), YEARLY, None
    return text, ONCE, None


def _take_warn(text: str) -> tuple[str, int]:
    """«предупреди за 3 дня», «за неделю» → сколько дней запаса."""
    match = re.search(r"\b(?:предупреди\s+)?за\s+(\d+)\s+(?:дн\w*|сут\w*)\b", text)
    if match:
        return _cut(text, match), int(match.group(1))
    match = re.search(r"\b(?:предупреди\s+)?за\s+(день|сутки|неделю|месяц)\b", text)
    if match:
        return _cut(text, match), {"день": 1, "сутки": 1, "неделю": 7, "месяц": 30}[match.group(1)]
    return text, 0


def _take_time(text: str) -> tuple[str, str]:
    """«в 14:30», «в 9 утра», «в 8 часов вечера» → «14:30».

    Часть суток обязательна, когда час меньше 12 и написан без двоеточия:
    «в 8 вечера» — это 20:00, а просто «в 8» само по себе двусмысленно.
    Двусмысленное трактуем как названо (8 → 08:00) и не гадаем.
    """
    match = re.search(r"\bв\s+(\d{1,2})[:.](\d{2})\b", text)
    if match:
        hour, minute = int(match.group(1)), int(match.group(2))
        if hour < 24 and minute < 60:
            return _cut(text, match), f"{hour:02d}:{minute:02d}"
        return text, ""
    match = re.search(
        r"\bв\s+(\d{1,2})\s*(?:час\w*)?\s*(утра|дня|вечера|ночи)?\b(?!\s*(?:мин\w*|дн\w*|нед\w*|мес\w*))",
        text,
    )
    if match and (match.group(2) or "час" in match.group(0)):
        hour = int(match.group(1))
        if hour > 24:
            return text, ""
        part = match.group(2)
        if part in ("вечера", "ночи") and hour < 12:
            hour = (hour + 12) % 24
        elif part == "дня" and hour < 12:
            hour += 12
        return _cut(text, match), f"{hour % 24:02d}:00"
    return text, ""


def _take_date(text: str, today: dt.date, weekday_of_repeat: int | None):
    """Найти дату. Порядок проб — от самого однозначного к самому общему."""
    if weekday_of_repeat is not None:
        # «каждый понедельник»: первая дата — ближайший такой день.
        return text, _next_weekday(today, weekday_of_repeat, include_today=True)

    match = re.search(r"\b(сегодня|завтра|послезавтра)\b", text)
    if match:
        shift = {"сегодня": 0, "завтра": 1, "послезавтра": 2}[match.group(1)]
        return _cut(text, match), today + dt.timedelta(days=shift)

    match = re.search(r"\bчерез\s+(\d+)\s+(дн\w*|нед\w*|мес\w*|год\w*|лет)\b", text)
    if match:
        return _cut(text, match), _shift(today, int(match.group(1)), match.group(2))

    match = re.search(r"\bчерез\s+(день|неделю|месяц|год)\b", text)
    if match:
        return _cut(text, match), _shift(today, 1, match.group(1))

    match = re.search(r"\b(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?\b", text)
    if match:
        day, month = int(match.group(1)), int(match.group(2))
        year = _full_year(match.group(3), today)
        found = _make_date(year, month, day, today, explicit_year=bool(match.group(3)))
        if found is not None:
            return _cut(text, match), found

    match = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", text)
    if match:
        try:
            return _cut(text, match), dt.date(*(int(g) for g in match.groups()))
        except ValueError:
            pass

    # «пятнадцатого сентября» и «15 сентября» — один и тот же случай, отличается
    # только написанием числа.
    day_word = r"(\d{1,2}|(?:двадцать\s+|тридцать\s+)?[а-я]+го)"

    # «10 числа» — месяц не назван. Обычно так говорят про ежемесячное («каждый
    # месяц 10 числа заплатить»), но и само по себе это осмысленная дата:
    # ближайшее такое число.
    match = re.search(day_word + r"\s+числа\b", text)
    if match:
        day = _day_number(match.group(1))
        if day is not None:
            found = _day_of_month(today, day)
            if found is not None:
                return _cut(text, match), found

    match = re.search(day_word + r"\s+(" + "|".join(_MONTHS) + r")\b", text)
    if match:
        day = _day_number(match.group(1))
        if day is not None:
            found = _make_date(today.year, _MONTHS[match.group(2)], day, today, explicit_year=False)
            if found is not None:
                return _cut(text, match), found

    match = re.search(
        r"\b(?:в|во)\s+(?:следующ\w+\s+)?(" + "|".join(_WEEKDAYS) + r")\b", text
    )
    if match:
        next_week = "следующ" in match.group(0)
        found = _next_weekday(today, _WEEKDAYS[match.group(1)], include_today=False)
        if next_week and (found - today).days < 7:
            found += dt.timedelta(days=7)
        return _cut(text, match), found

    return text, None


def _day_number(raw: str) -> int | None:
    if raw.isdigit():
        day = int(raw)
        return day if 1 <= day <= 31 else None
    words = raw.split()
    if len(words) == 2 and words[0] in _ORDINAL_TENS and words[1] in _ORDINAL_UNITS:
        day = _ORDINAL_TENS[words[0]] + _ORDINAL_UNITS[words[1]]
        return day if 1 <= day <= 31 else None
    return _ORDINAL_UNITS.get(raw)


def _full_year(raw: str | None, today: dt.date) -> int:
    if not raw:
        return today.year
    year = int(raw)
    return year if year > 100 else 2000 + year


def _make_date(year: int, month: int, day: int, today: dt.date, *, explicit_year: bool):
    """Собрать дату; без явно названного года прошедшую переносим на следующий.

    «Напомни 15 марта» в апреле — это про март следующего года: назначить на
    дату, которая уже прошла, значит не напомнить вовсе.
    """
    try:
        found = dt.date(year, month, day)
    except ValueError:
        return None
    if not explicit_year and found < today:
        try:
            found = dt.date(year + 1, month, day)
        except ValueError:      # 29 февраля в невисокосном году
            return None
    return found


def _day_of_month(today: dt.date, day: int) -> dt.date | None:
    """Ближайшее такое число месяца, начиная с сегодняшнего дня.

    Месяц, в котором такого числа нет, перешагиваем: «31 числа» в феврале не
    существует, и придумывать вместо него 28-е нельзя — см. events._next_monthly,
    там то же правило и та же причина.
    """
    year, month = today.year, today.month
    for _ in range(14):
        try:
            candidate = dt.date(year, month, day)
        except ValueError:
            candidate = None
        if candidate is not None and candidate >= today:
            return candidate
        month += 1
        if month > 12:
            month, year = 1, year + 1
    return None


def _shift(today: dt.date, count: int, unit: str) -> dt.date:
    if unit.startswith("нед"):
        return today + dt.timedelta(weeks=count)
    if unit.startswith("мес"):
        return _add_months(today, count)
    if unit.startswith("год") or unit == "лет":
        return _add_months(today, 12 * count)
    return today + dt.timedelta(days=count)


def _add_months(day: dt.date, months: int) -> dt.date:
    """«через месяц» 31 января — это 28 февраля, а не 3 марта.

    Здесь, в отличие от ежемесячного повтора в events.py, месяц пропустить
    нельзя: человек сказал «через месяц» про конкретное дело, и ответ «такого
    числа в феврале нет» его не устроит. Поэтому упираемся в последний день.
    """
    total = day.month - 1 + months
    year = day.year + total // 12
    month = total % 12 + 1
    last = _days_in_month(year, month)
    return dt.date(year, month, min(day.day, last))


def _days_in_month(year: int, month: int) -> int:
    if month == 12:
        return 31
    return (dt.date(year + month // 12, month % 12 + 1, 1) - dt.timedelta(days=1)).day


def _next_weekday(today: dt.date, weekday: int, *, include_today: bool) -> dt.date:
    """Ближайший такой день недели.

    include_today=False для «в четверг», сказанного в четверг: это следующий
    четверг, через неделю. Человек, которому нужно сегодня, скажет «сегодня»;
    а вот «напомни в четверг», произнесённое в четверг вечером, почти наверняка
    про следующую неделю — и ошибиться в эту сторону безопаснее: напоминание
    придёт, просто позже, а не окажется в прошлом.
    """
    ahead = (weekday - today.weekday()) % 7
    if ahead == 0 and not include_today:
        ahead = 7
    return today + dt.timedelta(days=ahead)


def _clean_text(text: str) -> str:
    words = text.split()
    while words and words[0] in _TEXT_LEADERS:
        words.pop(0)
    while words and words[-1] in (",", "-", "—", ":"):
        words.pop()
    return " ".join(words).strip(" ,:-—")


_MODEL_PROMPT = """Сегодня {today} ({weekday}).
Разбери фразу-напоминание и верни ТОЛЬКО JSON без пояснений:
{{"date": "ГГГГ-ММ-ДД", "text": "о чём напомнить", "time": "ЧЧ:ММ или пусто", \
"repeat": "once|yearly|monthly|weekly", "warn": 0}}
Если даты в фразе нет — верни {{"date": ""}}.
Фраза: {phrase}"""

_WEEKDAY_NAMES = (
    "понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"
)


def _ask_model(phrase: str, today: dt.date, ask) -> Reminder | None:
    """Запасной путь: спросить модель. Ответ проверяем так же строго, как свой.

    Модели тут не доверяем ни в чём, кроме самой даты: текст напоминания она
    может пересказать своими словами, но дата обязана быть разбираемой ISO-
    строкой и не в прошлом. Всё остальное — повтор, время, запас — приводим к
    известным значениям, иначе в календарь уедет мусор, который потом молча
    не сработает.
    """
    prompt = _MODEL_PROMPT.format(
        today=today.isoformat(), weekday=_WEEKDAY_NAMES[today.weekday()], phrase=phrase
    )
    try:
        raw = ask(prompt)
    except Exception as exc:                     # провайдер уже глушит своё, но чужой код бывает разный
        logger.warning("Модель не разобрала дату: %s", exc)
        return None
    data = _extract_json(raw or "")
    if not data:
        return None
    date = _parse_iso(str(data.get("date") or ""))
    if date is None:
        return None
    text = _clean_text(_normalize(str(data.get("text") or "")))
    if not text:
        return None
    repeat = str(data.get("repeat") or ONCE).lower()
    if repeat not in (ONCE, YEARLY, MONTHLY, WEEKLY):
        repeat = ONCE
    if repeat == ONCE and date < today:
        return None
    time = str(data.get("time") or "").strip()
    try:
        time = dt.time.fromisoformat(time).strftime("%H:%M") if time else ""
    except ValueError:
        time = ""
    try:
        warn = max(0, int(data.get("warn") or 0))
    except (TypeError, ValueError):
        warn = 0
    logger.info("Дату напоминания разобрала модель: %s → %s", phrase, date)
    return Reminder(date=date, text=text, time=time, repeat=repeat, warn=warn)


def _parse_iso(raw: str) -> dt.date | None:
    try:
        return dt.date.fromisoformat(raw.strip())
    except ValueError:
        return None


def _extract_json(raw: str) -> dict | None:
    """JSON из ответа модели, даже если она обернула его в текст или ```-блок."""
    match = re.search(r"\{.*\}", raw, re.S)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None
