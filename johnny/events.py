"""Календарь: что показать на такую-то дату.

Отвечает ровно на один вопрос и не знает больше ничего — ни про телеграм, ни
про погоду, ни про то, утро сейчас или вечер. На входе дата и уже прочитанные
события, на выходе список того, что про этот день стоит сказать.

Почему это отдельный файл с чистыми функциями: даты — самая ошибкоопасная
часть всей затеи (високосный год, месяц без тридцать первого числа, «за три
дня» через границу года), и одновременно самая удобная для тестов. Ни сети,
ни чтения файлов внутри логики нет: `occurrences_on` можно позвать с любой
датой и проверить ответ, не трогая ни диск, ни часы.

Формат файла — config/calendar.yaml, список записей:

    - date: 2026-09-15      # ISO, обязательно
      text: день рождения у мамы
      repeat: yearly        # once (по умолчанию) | yearly | monthly | weekly
      warn: 3               # предупреждать за N дней (по умолчанию 0 — в день)
      time: "14:30"         # необязательно, попадёт в текст сводки

Список, а не словарь с ключами-датами, по двум причинам: на одну дату бывает
несколько событий, и голосовая команда дописывает запись в конец файла
текстом, не переписывая его целиком (иначе yaml.dump стёр бы все комментарии —
ровно этим болеет panel.py).
"""

import datetime as dt
import logging
from dataclasses import dataclass
from pathlib import Path

import yaml

from .git_tools import plural

logger = logging.getLogger(__name__)

# Файл по умолчанию. Один на весь проект: его читают и голосовая команда, и
# сводка. Рядом с остальными настройками, а не в корне, — правится он так же
# руками, как apps.yaml.
DEFAULT_PATH = Path(__file__).resolve().parent.parent / "config" / "calendar.yaml"

_HEADER = """\
# Календарь Джони: дни рождения, врачи, дедлайны.
#
# Правится тремя способами и все три равноправны: руками здесь, голосом
# («Джони, напомни в четверг про врача»), с телефона через сайт GitHub.
# Поэтому файл — обычный текст, а не база: правки видно в git, и если Джони
# запишет чушь, её видно и можно откатить.
#
#   - date: 2026-09-15    # ISO, обязательно. У повторов — первая дата.
#     text: врач          # что сказать. Обязательно.
#     repeat: yearly      # once (по умолчанию) | yearly | monthly | weekly
#     warn: 3             # напоминать за N дней (обратным отсчётом)
#     time: "14:30"       # необязательно
#
# Событие без warn видно только в свой день. Разовое прошедшее не показывается
# больше никогда, но и не удаляется само — историю чистит человек.
"""

ONCE = "once"
YEARLY = "yearly"
MONTHLY = "monthly"
WEEKLY = "weekly"
REPEATS = (ONCE, YEARLY, MONTHLY, WEEKLY)

# Потолок предупреждения. Больше года бессмысленно, а у повторов лишнее
# предупреждение хуже, чем кажется: warn: 10 у еженедельного события
# означало бы строку в сводке КАЖДЫЙ день, и через неделю сводку перестанут
# открывать — то есть пропустят как раз то, ради чего всё делалось.
_WARN_LIMIT = {ONCE: 365, YEARLY: 364, MONTHLY: 27, WEEKLY: 6}


@dataclass(frozen=True)
class Event:
    date: dt.date
    text: str
    repeat: str = ONCE
    warn: int = 0
    time: str = ""


@dataclass(frozen=True)
class Occurrence:
    """Событие, попавшее в сводку за конкретный день."""

    event: Event
    date: dt.date
    days_left: int      # 0 — сегодня, 1 — завтра, …


def parse_events(raw) -> list[Event]:
    """Список записей из YAML → список Event. Кривые записи пропускаем.

    Именно пропускаем, а не падаем: файл правится руками и с телефона через
    сайт GitHub, и одна опечатка в дате не должна отменить утреннюю сводку
    целиком — про врача человек узнать должен. Каждая пропущенная запись
    уходит в журнал, чтобы молчание было объяснимым.
    """
    if raw is None:
        return []
    if not isinstance(raw, list):
        logger.warning("Календарь: ожидался список событий, пришло %s", type(raw).__name__)
        return []
    events: list[Event] = []
    for index, item in enumerate(raw, 1):
        event = _parse_one(item, index)
        if event is not None:
            events.append(event)
    return events


def _parse_one(item, index: int) -> Event | None:
    if not isinstance(item, dict):
        logger.warning("Календарь: запись %d — не набор полей, пропускаю", index)
        return None
    text = str(item.get("text") or "").strip()
    if not text:
        logger.warning("Календарь: запись %d без текста, пропускаю", index)
        return None
    date = _parse_date(item.get("date"))
    if date is None:
        logger.warning("Календарь: запись %d (%s) — непонятная дата %r", index, text, item.get("date"))
        return None
    repeat = str(item.get("repeat") or ONCE).strip().lower()
    if repeat not in REPEATS:
        logger.warning(
            "Календарь: запись %d (%s) — неизвестный повтор %r, считаю разовым", index, text, repeat
        )
        repeat = ONCE
    warn = _parse_warn(item.get("warn"), repeat, text, index)
    time = _parse_time(item.get("time"), text, index)
    return Event(date=date, text=text, repeat=repeat, warn=warn, time=time)


def _parse_date(value) -> dt.date | None:
    """YAML сам разбирает `2026-09-15` в date, но человек мог написать строкой.

    datetime тоже приходит: если в YAML написано `2026-09-15 14:30`. Берём от
    него дату — время у события живёт в отдельном поле, и молча ронять запись
    из-за лишних цифр было бы обидно.
    """
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    if isinstance(value, str):
        try:
            return dt.date.fromisoformat(value.strip())
        except ValueError:
            return None
    return None


def _parse_warn(value, repeat: str, text: str, index: int) -> int:
    if value is None or value == "":
        return 0
    try:
        warn = int(value)
    except (TypeError, ValueError):
        logger.warning("Календарь: запись %d (%s) — непонятное warn %r, считаю нулём", index, text, value)
        return 0
    if warn < 0:
        return 0
    limit = _WARN_LIMIT[repeat]
    if warn > limit:
        logger.warning(
            "Календарь: запись %d (%s) — warn %d больше периода повтора, урезаю до %d",
            index, text, warn, limit,
        )
        return limit
    return warn


def _parse_time(value, text: str, index: int) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, dt.time):
        return value.strftime("%H:%M")
    raw = str(value).strip()
    try:
        return dt.time.fromisoformat(raw).strftime("%H:%M")
    except ValueError:
        logger.warning("Календарь: запись %d (%s) — непонятное время %r, пропускаю поле", index, text, raw)
        return ""


def load_events(path=None) -> list[Event]:
    """Прочитать calendar.yaml. Нет файла или он пуст — пустой список.

    Отсутствие файла — не ошибка: до первой команды «напомни» его и не будет,
    а сводка в этот день просто промолчит.

    Путь по умолчанию берётся ВНУТРИ, а не значением аргумента: значения
    аргументов вычисляются один раз при импорте, и тогда подменить DEFAULT_PATH
    (в тестах, в облачном запуске) стало бы невозможно — функция продолжала бы
    читать файл, запомненный при загрузке модуля.
    """
    path = Path(path or DEFAULT_PATH)
    if not path.exists():
        return []
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        # Файл правится и руками, и с телефона: сломанный отступ не должен
        # ронять ни Джони, ни облачный запуск.
        logger.warning("Календарь %s не разобрался: %s", path, exc)
        return []
    return parse_events(raw)


def append_event(event: Event, path=None) -> None:
    """Дописать событие в конец файла, не трогая всё остальное.

    ДОПИСЫВАЕМ ТЕКСТОМ, а не читаем-меняем-сохраняем через yaml.dump, и это
    главное решение в этой функции. Дамп собрал бы файл заново из разобранных
    данных и стёр бы из него все комментарии — и шапку с описанием формата, и
    пометки, которые человек оставил себе сам. Ровно этим болеет panel.py с
    settings.yaml, и повторять эту ошибку в файле, который человек правит
    руками и с телефона, нельзя.

    Побочная выгода: дозапись не может испортить уже записанное. Даже если
    новая запись получится кривой, старые события останутся читаемыми — а
    перезапись целиком в худшем случае теряет весь календарь.
    """
    path = Path(path or DEFAULT_PATH)      # см. load_events: не в значении аргумента
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {"date": event.date.isoformat(), "text": event.text}
    if event.time:
        record["time"] = event.time
    if event.repeat != ONCE:
        record["repeat"] = event.repeat
    if event.warn:
        record["warn"] = event.warn
    # Сериализуем ОДНУ запись как список из одного элемента: сам yaml
    # позаботится о кавычках и экранировании в надиктованном тексте, а формат
    # блока совпадёт с остальным файлом.
    block = yaml.safe_dump([record], allow_unicode=True, sort_keys=False, width=1000)
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    if not existing.strip():
        existing = _HEADER
    if not existing.endswith("\n"):
        existing += "\n"
    path.write_text(existing + block, encoding="utf-8")


def next_occurrence(event: Event, on_or_after: dt.date) -> dt.date | None:
    """Ближайшее наступление события в этот день или позже. None — не наступит.

    Разовое событие в прошлом не наступит уже никогда — отсюда None. Удалять
    такие записи из файла Джони не будет: история правок в git дешевле, чем
    риск стереть то, что человек хотел сохранить.
    """
    if event.repeat == ONCE:
        return event.date if event.date >= on_or_after else None
    if event.repeat == WEEKLY:
        return _next_weekly(event.date, on_or_after)
    if event.repeat == MONTHLY:
        return _next_monthly(event.date, on_or_after)
    return _next_yearly(event.date, on_or_after)


def _next_weekly(start: dt.date, on_or_after: dt.date) -> dt.date:
    """Тот же день недели. До первой даты события повторов ещё не было."""
    if start >= on_or_after:
        return start
    ahead = (start.weekday() - on_or_after.weekday()) % 7
    return on_or_after + dt.timedelta(days=ahead)


def _next_monthly(start: dt.date, on_or_after: dt.date) -> dt.date | None:
    """То же число каждого месяца.

    Месяц, в котором такого числа нет (31-е в феврале), ПРОПУСКАЕТСЯ, а не
    съезжает на последний день. Причина: «плачу за квартиру 31-го» и «плачу
    28 февраля» — разные обещания, и придумывать за человека второе, когда он
    сказал первое, хуже, чем промолчать один месяц. Для платежей, которые
    нельзя пропускать, есть 28-е число, оно есть в любом месяце.
    """
    if start >= on_or_after:
        return start
    year, month = on_or_after.year, on_or_after.month
    for _ in range(14):     # год с запасом: подряд без 31-го идут максимум два месяца
        try:
            candidate = dt.date(year, month, start.day)
        except ValueError:
            candidate = None
        if candidate is not None and candidate >= on_or_after:
            return candidate
        month += 1
        if month > 12:
            month, year = 1, year + 1
    return None


def _next_yearly(start: dt.date, on_or_after: dt.date) -> dt.date | None:
    """То же число и месяц каждый год.

    29 февраля в невисокосный год отмечаем 28-го, а не пропускаем. Здесь
    решение обратное месячному повтору, и не по недосмотру: годовой повтор —
    это почти всегда день рождения, а поздравить на день раньше несравнимо
    лучше, чем не поздравить вовсе. У платежа 31-го такого «почти всегда»
    нет, поэтому там пропуск.
    """
    year = on_or_after.year
    for _ in range(5):
        candidate = _same_day_in_year(start, year)
        if candidate >= on_or_after:
            return candidate
        year += 1
    return None


def _same_day_in_year(start: dt.date, year: int) -> dt.date:
    try:
        return dt.date(year, start.month, start.day)
    except ValueError:
        # Сюда попадает только 29 февраля: остальные месяцы одинаковы во всех годах.
        return dt.date(year, 2, 28)


def occurrences_on(events: list[Event], day: dt.date) -> list[Occurrence]:
    """Что показать в сводке за `day`: сегодняшнее плюс то, о чём пора предупредить.

    Предупреждение работает окном, а не одним днём: warn: 3 даст строку и за
    три дня, и за два, и за день, и в сам день. Это обратный отсчёт, а не
    единственный окрик — единственный легко проспать (телефон в беззвучном,
    сводка не открыта), и тогда предупреждение не сработало вовсе.

    Порядок: сначала то, что ближе, внутри дня — по времени, потом по тексту.
    Сортировка нужна не для красоты: сводку читают спросонья за две секунды.
    """
    found: list[Occurrence] = []
    for event in events:
        when = next_occurrence(event, day)
        if when is None:
            continue
        days_left = (when - day).days
        if days_left > event.warn:
            continue
        found.append(Occurrence(event=event, date=when, days_left=days_left))
    return sorted(found, key=lambda occ: (occ.days_left, occ.event.time or "99:99", occ.event.text))


# Месяцы и дни недели для ЧТЕНИЯ вслух. В dates.py есть похожая таблица, но она
# для разбора и устроена наоборот — слово в число, с падежными вариантами. Одну
# на обе задачи не сделать, а связывать модули ради двенадцати слов значило бы
# завести цикл импортов (dates.py уже читает отсюда названия повторов).
_MONTHS_SPOKEN = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)
_WEEKDAYS_SPOKEN = (
    "в понедельник", "во вторник", "в среду", "в четверг",
    "в пятницу", "в субботу", "в воскресенье",
)


# «каждый понедельник», но «каждую среду» и «каждое воскресенье»: род у дней
# недели разный, и одного шаблона на все семь не бывает.
_EVERY_WEEKDAY = (
    "каждый понедельник", "каждый вторник", "каждую среду", "каждый четверг",
    "каждую пятницу", "каждую субботу", "каждое воскресенье",
)


def day_month(day: dt.date) -> str:
    """«15 сентября» — дата без всякого «завтра».

    Для повторов human_date не годится: «каждый год, завтра» не значит ничего,
    а «каждый год 15 сентября» значит ровно то, что записано.
    """
    return f"{day.day} {_MONTHS_SPOKEN[day.month - 1]}"


def every_weekday(day: dt.date) -> str:
    """«каждый понедельник» для еженедельного повтора, заведённого в этот день."""
    return _EVERY_WEEKDAY[day.weekday()]


def human_date(day: dt.date, today: dt.date) -> str:
    """Дата так, как её произносит человек: «в четверг, 27 августа».

    Нужна, чтобы Джони проговаривал распознанную дату вслух при записи
    напоминания. Это единственная защита от ошибки разбора: «напомни в
    четверг» могло стать чем угодно, и услышать «записал на четверг, 27
    августа» — единственный момент, когда человек ещё может поправить.
    """
    days = (day - today).days
    if days == 0:
        return "сегодня"
    if days == 1:
        return "завтра"
    if days == 2:
        return "послезавтра"
    число = f"{day.day} {_MONTHS_SPOKEN[day.month - 1]}"
    if day.year != today.year:
        число += f" {day.year} года"
    if 0 < days < 7:
        return f"{_WEEKDAYS_SPOKEN[day.weekday()]}, {число}"
    return число


def describe(occurrence: Occurrence) -> str:
    """Строка события для сводки: «14:30 — запись к врачу (через 2 дня)»."""
    parts = []
    if occurrence.event.time:
        parts.append(f"{occurrence.event.time} — ")
    parts.append(occurrence.event.text)
    if occurrence.days_left:
        parts.append(f" ({_in_days(occurrence.days_left)})")
    return "".join(parts)


def _in_days(days: int) -> str:
    if days == 1:
        return "завтра"
    if days == 2:
        return "послезавтра"
    return f"через {days} {plural(days, 'день', 'дня', 'дней')}"
