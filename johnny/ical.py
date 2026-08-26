"""Чтение формата iCalendar (RFC 5545) — ровно столько, сколько нужно.

Не универсальная библиотека и не пытается ею быть. Здесь разбираются события
из фида, который отдаёт школа: заголовки, даты со временем и без, экранированный
текст. Ни RRULE, ни VALARM, ни VTODO, ни часовых поясов, объявленных внутри
самого файла, — если что-то из этого понадобится, лучше взять icalendar с pypi,
чем дописывать сюда.

ПОЧЕМУ СВОЙ КОД, А НЕ ПАКЕТ. Этот разбор работает и в облаке, где каждая
зависимость — лишний шаг сборки на каждый запуск сводки. По той же причине
notify.py и weather.py ходят в сеть через urllib. Нужного здесь — сотня строк
и ноль сложных случаев; пакет icalendar тянет за собой python-dateutil и умеет
в двадцать раз больше, чем спрашивают.

Календарь возвращается СПИСКОМ СОБЫТИЙ, а не деревом компонентов: то, что
внутри VCALENDAR лежит ещё и VTIMEZONE, вызывающего не касается.
"""

import datetime as dt
import logging
import re
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class VEvent:
    """Одно событие. start/end — date у события «на весь день», иначе datetime."""

    uid: str
    summary: str
    description: str = ""
    start: dt.date | dt.datetime | None = None
    end: dt.date | dt.datetime | None = None
    raw: dict = field(default_factory=dict, repr=False, compare=False)

    @property
    def all_day(self) -> bool:
        return isinstance(self.start, dt.date) and not isinstance(self.start, dt.datetime)

    def date(self) -> dt.date | None:
        """День, к которому относится событие."""
        if self.start is None:
            return None
        return self.start.date() if isinstance(self.start, dt.datetime) else self.start


def unfold(text: str) -> list[str]:
    """Склеить перенесённые строки: продолжение начинается с пробела или таба.

    По RFC строка длиннее 75 октетов разрезается, и продолжение помечается
    одним ведущим пробелом. Без склейки длинное описание домашнего задания
    развалилось бы на несколько «свойств», ни одно из которых не разбирается.
    """
    return re.sub(r"\r?\n[ \t]", "", text.replace("\r\n", "\n")).split("\n")


def unescape(value: str) -> str:
    r"""Развернуть экранирование текстовых полей: \n \, \; \\ .

    Порядок важен: обратный слэш разворачивается последним, иначе «\\n»
    (экранированный слэш перед буквой n) превратится в перевод строки.
    """
    out = []
    i = 0
    while i < len(value):
        if value[i] == "\\" and i + 1 < len(value):
            nxt = value[i + 1]
            out.append({"n": "\n", "N": "\n", ",": ",", ";": ";", "\\": "\\"}.get(nxt, nxt))
            i += 2
        else:
            out.append(value[i])
            i += 1
    return "".join(out)


def split_property(line: str) -> tuple[str, dict, str] | None:
    """«DTSTART;TZID=Europe/Berlin:20260826T100000» → ('DTSTART', {...}, '2026…').

    Двоеточие ищем ПЕРВОЕ, а не последнее: в значении их сколько угодно
    (URL внутри DESCRIPTION — обычное дело), а в имени свойства — ни одного.
    """
    if ":" not in line:
        return None
    head, value = line.split(":", 1)
    parts = head.split(";")
    name = parts[0].strip().upper()
    params = {}
    for part in parts[1:]:
        if "=" in part:
            key, val = part.split("=", 1)
            params[key.strip().upper()] = val.strip().strip('"')
    return name, params, value


def parse_datetime(value: str, params: dict) -> dt.date | dt.datetime | None:
    """Значение DTSTART/DTEND в date или datetime с часовым поясом.

    Три формы, и все три встречаются в школьном фиде:
      VALUE=DATE          20260826            — событие на весь день (меню, планы)
      TZID=Europe/Berlin  20260826T100000     — местное время названной зоны
      (без параметров)    20260826T080000Z    — UTC, суффикс Z

    Неизвестная зона не повод потерять урок: возвращаем время без пояса и
    пишем в журнал. Расписание при этом остаётся верным по стенным часам —
    именно они человеку и нужны, — а сравнение с «сейчас» просто становится
    наивным.
    """
    value = value.strip()
    if params.get("VALUE") == "DATE" or (len(value) == 8 and "T" not in value):
        try:
            return dt.date(int(value[:4]), int(value[4:6]), int(value[6:8]))
        except ValueError:
            return None
    match = re.match(r"^(\d{8})T(\d{6})(Z?)$", value)
    if not match:
        return None
    day, time, zulu = match.groups()
    try:
        naive = dt.datetime(
            int(day[:4]), int(day[4:6]), int(day[6:8]),
            int(time[:2]), int(time[2:4]), int(time[4:6]),
        )
    except ValueError:
        return None
    if zulu:
        return naive.replace(tzinfo=dt.timezone.utc)
    tzid = params.get("TZID")
    if not tzid:
        return naive
    try:
        return naive.replace(tzinfo=ZoneInfo(tzid))
    except (ZoneInfoNotFoundError, ValueError):
        logger.warning("Календарь: неизвестный часовой пояс %r, читаю время как есть", tzid)
        return naive


def parse(text: str) -> list[VEvent]:
    """Все VEVENT из файла. Всё остальное (VTIMEZONE, VTODO) пропускается.

    События без UID пропускаем молча — по UID они потом сравниваются между
    выгрузками, и безымянное событие в такой паре бесполезно.
    """
    events: list[VEvent] = []
    current: dict | None = None
    depth_other = 0
    for line in unfold(text):
        line = line.strip()
        if not line:
            continue
        if line.upper() == "BEGIN:VEVENT":
            current = {}
            continue
        if line.upper() == "END:VEVENT":
            if current is not None:
                event = _build(current)
                if event is not None:
                    events.append(event)
            current = None
            continue
        if current is None:
            continue
        if line.upper().startswith("BEGIN:"):
            # Вложенный компонент внутри события (VALARM) — пропускаем целиком,
            # иначе его свойства перемешаются со свойствами самого события.
            depth_other += 1
            continue
        if line.upper().startswith("END:"):
            depth_other = max(0, depth_other - 1)
            continue
        if depth_other:
            continue
        parsed = split_property(line)
        if parsed is None:
            continue
        name, params, value = parsed
        current[name] = (params, value)
    return events


def _build(props: dict) -> VEvent | None:
    uid = props.get("UID", ({}, ""))[1].strip()
    if not uid:
        return None
    start = end = None
    if "DTSTART" in props:
        start = parse_datetime(props["DTSTART"][1], props["DTSTART"][0])
    if "DTEND" in props:
        end = parse_datetime(props["DTEND"][1], props["DTEND"][0])
    return VEvent(
        uid=uid,
        summary=unescape(props.get("SUMMARY", ({}, ""))[1]).strip(),
        description=unescape(props.get("DESCRIPTION", ({}, ""))[1]).strip(),
        start=start,
        end=end,
        raw={name: value for name, (_, value) in props.items()},
    )
