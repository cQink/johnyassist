"""Школьный календарь SchoolSoft: уроки, контрольные, сдачи, меню столовой.

Над johnny/ical.py, который знает про формат, но ничего не знает про школу.
Здесь наоборот: формат уже разобран, а смысл придаётся здесь.

КАК ОТЛИЧАТЬ ОДНО ОТ ДРУГОГО. В фиде всё лежит вперемешку, и тип события
надёжно виден только по префиксу UID:

    lesson-63447-2-w35                  урок
    lunchmenu-35-2-2                    меню столовой (неделя 35, день 2, блюдо 2)
    ps_assignment-1420-2026-09-03T10:10 контрольная, домашка, семинар
    ps_planning-257                     план курса

По тексту заголовка это делать нельзя: «Prov» встречается и как тип, и внутри
названия («Prov Prov kap1»), а «Läxa visas inte på provschema Kapitel 1» —
это вообще одно поле, в которое SchoolSoft подмешал служебную пометку.

ПРО ОТМЕНЫ, И ЭТО ГЛАВНОЕ ОГРАНИЧЕНИЕ. Отменённый урок SchoolSoft НИКАК не
помечает — он просто пропадает из выгрузки. Проверено на живом фиде: слов
inställd, cancelled, ändrad, vikarie и свойства STATUS в нём нет ни одного.
Значит «первый урок отменили» можно сказать только сравнив сегодняшнюю
выгрузку со вчерашней — отсюда snapshot() и diff() ниже.
"""

import datetime as dt
import json
import logging
import re
import urllib.error
import urllib.request

from . import ical

logger = logging.getLogger(__name__)

LESSON = "lesson"
MENU = "menu"
TASK = "task"
PLANNING = "planning"
OTHER = "other"

_KIND_BY_PREFIX = {
    "lesson": LESSON,
    "lunchmenu": MENU,
    "ps_assignment": TASK,
    "ps_planning": PLANNING,
}

_TIMEOUT = 20.0

# Хвост «nivå 1c» есть почти у каждого предмета и не значит ничего в сводке,
# которую читают спросонья: курс всё равно один. «Lektion» — служебное слово.
_LEVEL_TAIL = re.compile(r"\s+niv[åa]\s+\S+\s*$", re.I)
_LESSON_PREFIX = re.compile(r"^lektion\s+", re.I)

# SchoolSoft подмешивает эту пометку прямо в название задания. Для человека,
# читающего сводку, она шум: он не смотрит provschema, он смотрит телефон.
_NOISE = re.compile(r"\s*visas inte på provschema\s*", re.I)

# Типы заданий, как их называет SchoolSoft. Первое слово заголовка.
_TASK_WORDS = ("prov", "läxa", "laxa", "seminarium", "inlämning", "inlamning", "uppgift")


class SchoolEvent:
    """Событие школьного календаря, уже приведённое к человеческому виду."""

    __slots__ = ("kind", "uid", "title", "detail", "start", "end")

    def __init__(self, kind, uid, title, detail, start, end):
        self.kind, self.uid, self.title = kind, uid, title
        self.detail, self.start, self.end = detail, start, end

    def __repr__(self):
        return f"SchoolEvent({self.kind}, {self.title!r}, {self.start})"

    def __eq__(self, other):
        return isinstance(other, SchoolEvent) and self._key() == other._key()

    def _key(self):
        return (self.kind, self.uid, self.title, self.detail, self.start, self.end)

    def date(self) -> dt.date | None:
        if self.start is None:
            return None
        return self.start.date() if isinstance(self.start, dt.datetime) else self.start

    def time(self) -> str:
        return self.start.strftime("%H:%M") if isinstance(self.start, dt.datetime) else ""

    def end_time(self) -> str:
        return self.end.strftime("%H:%M") if isinstance(self.end, dt.datetime) else ""

    def group(self) -> str:
        """Ключ, по которому одно и то же задание узнаётся в разных выгрузках.

        У задания UID вида «ps_assignment-1407-2026-08-21T10:30»: номер один,
        а хвост меняется от занятия к занятию, и одно и то же задание попадает
        в фид несколько раз. Сравнивать и считать его нужно по номеру.
        """
        parts = self.uid.split("-")
        return "-".join(parts[:2]) if len(parts) > 1 else self.uid


def classify(uid: str) -> str:
    prefix = uid.split("-", 1)[0]
    return _KIND_BY_PREFIX.get(prefix, OTHER)


def short_subject(summary: str, overrides: dict | None = None) -> str:
    """«Lektion Matematik fortsättning nivå 1c» → «Matematik fortsättning».

    Шведские названия оставляем как есть — решение владельца: именно они стоят
    у него в расписании и на двери кабинета, и перевод заставлял бы сверять
    два имени вместо одного.

    overrides — словарь из настроек (digest.subject_names) для случаев, когда
    правило укорачивания даёт формально верное, но неудобное имя: «Svenska som
    andra språk» человек называет просто «Svenska». Сверка идёт и с укороченным
    именем, и с полным, без учёта регистра, — чтобы в настройках можно было
    написать то, что видишь в сводке, а не гадать, что получилось после правил.
    """
    полное = (summary or "").strip()
    text = _LESSON_PREFIX.sub("", полное)
    короткое = _LEVEL_TAIL.sub("", text).strip() or полное
    if overrides:
        по_ключу = {str(k).strip().lower(): str(v) for k, v in overrides.items()}
        for вариант in (короткое, полное, text.strip()):
            замена = по_ключу.get(вариант.lower())
            if замена:
                return замена
    return короткое


def clean_task(summary: str) -> str:
    """Заголовок задания без служебной пометки SchoolSoft и без повторов.

    «Prov Prov kap1» → «Prov kap1»: SchoolSoft ставит тип задания впереди
    названия, а название часто начинается с того же слова.
    """
    text = _NOISE.sub(" ", (summary or "")).strip()
    text = re.sub(r"\s{2,}", " ", text)
    words = text.split()
    if len(words) >= 2 and words[0].lower() == words[1].lower():
        words.pop(0)
    return " ".join(words)


def task_kind(summary: str) -> str:
    """Первое слово заголовка, если это известный тип задания. Иначе пусто."""
    first = (summary or "").split()
    if first and first[0].lower() in _TASK_WORDS:
        return first[0]
    return ""


def parse_feed(text: str, subject_names: dict | None = None) -> list[SchoolEvent]:
    """Разобранный фид → список событий, отсортированный по времени."""
    events = []
    for raw in ical.parse(text):
        kind = classify(raw.uid)
        if kind == LESSON:
            title, detail = short_subject(raw.summary, subject_names), raw.description
        elif kind == MENU:
            title, detail = _menu_title(raw.description), _menu_dish(raw.description)
        else:
            title, detail = clean_task(raw.summary), raw.description
        events.append(SchoolEvent(kind, raw.uid, title, detail, raw.start, raw.end))
    return sorted(events, key=_order)


def _order(event: SchoolEvent):
    day = event.date() or dt.date.max
    return (day, event.time() or "", event.title)


def _menu_title(description: str) -> str:
    """«Huvudrätt\\nChili con carne» → «Huvudrätt» (что за блюдо в меню)."""
    return (description or "").split("\n")[0].strip()


def _menu_dish(description: str) -> str:
    parts = (description or "").split("\n", 1)
    return parts[1].strip() if len(parts) > 1 else ""


def fetch(url: str, timeout: float = _TIMEOUT) -> str | None:
    """Скачать фид. None — не достучались, и это не повод рушить сводку.

    Расписание — не единственное, что в ней есть: про врача и про дождь
    человек должен узнать, даже когда SchoolSoft лежит.
    """
    if not url:
        return None
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "Johnny/1.0"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        logger.warning("Школьный календарь не пришёл: %s", exc)
        return None


def lessons_on(events: list[SchoolEvent], day: dt.date) -> list[SchoolEvent]:
    return [e for e in events if e.kind == LESSON and e.date() == day]


def menu_on(events: list[SchoolEvent], day: dt.date) -> str:
    """Главное блюдо дня. Пусто — меню на этот день не опубликовано.

    Из двух записей (Huvudrätt и Dagens gröna) берём основную: сводка должна
    читаться за две секунды, а не перечислять варианты.
    """
    записи = [e for e in events if e.kind == MENU and e.date() == day and e.detail]
    основное = [e for e in записи if e.title.lower().startswith("huvudr")]
    выбрано = основное or записи
    return выбрано[0].detail if выбрано else ""


def tasks_between(events, first: dt.date, last: dt.date) -> list[SchoolEvent]:
    """Контрольные, домашние и семинары в промежутке дат, по одному на задание.

    Одно и то же задание висит в фиде несколько раз — по разу на каждое
    занятие курса (см. SchoolEvent.group). В сводке оно должно быть один раз,
    ближайшей датой: именно она и есть срок, о котором стоит помнить.
    """
    найдено: dict[str, SchoolEvent] = {}
    for event in events:
        if event.kind != TASK:
            continue
        day = event.date()
        if day is None or not (first <= day <= last):
            continue
        прежнее = найдено.get(event.group())
        if прежнее is None or day < прежнее.date():
            найдено[event.group()] = event
    return sorted(найдено.values(), key=_order)


def describe_lessons(lessons: list[SchoolEvent]) -> str:
    """«10:00 Kemi, 12:15 Engelska, 13:45 Svenska (до 15:05)»."""
    if not lessons:
        return ""
    куски = [f"{e.time()} {e.title}".strip() for e in lessons]
    конец = lessons[-1].end_time()
    строка = ", ".join(куски)
    return f"{строка} (до {конец})" if конец else строка


# ── Снимок и сравнение: единственный способ узнать про отмену ────────────────

def snapshot(events: list[SchoolEvent], since: dt.date, days: int = 21) -> dict:
    """Слепок ближайших уроков: UID → «когда и что».

    Только уроки и только ближайшие недели. Причина не в экономии места, а в
    шуме: план курса на май, сдвинувшийся на день, — это не новость, ради
    которой стоит будить телефон, а весь учебный год в файле сделал бы разницу
    между выгрузками нечитаемой.
    """
    last = since + dt.timedelta(days=days)
    слепок = {}
    for event in events:
        day = event.date()
        if event.kind != LESSON or day is None or not (since <= day <= last):
            continue
        когда = event.start.isoformat() if isinstance(event.start, dt.datetime) else day.isoformat()
        слепок[event.uid] = {"when": когда, "title": event.title, "date": day.isoformat()}
    return слепок


class Changes:
    """Что изменилось между двумя выгрузками расписания."""

    __slots__ = ("cancelled", "added", "moved")

    def __init__(self, cancelled=None, added=None, moved=None):
        self.cancelled = cancelled or []      # [(date, title)]
        self.added = added or []              # [(date, title, time)]
        self.moved = moved or []              # [(date, title, было, стало)]

    def __bool__(self):
        return bool(self.cancelled or self.added or self.moved)

    def lines(self) -> list[str]:
        строки = []
        for day, title in self.cancelled:
            строки.append(f"отменили {title} ({_день(day)})")
        for day, title, время in self.moved:
            строки.append(f"{title} перенесли на {время} ({_день(day)})")
        for day, title, время in self.added:
            строки.append(f"добавили {title} в {время} ({_день(day)})")
        return строки


def _день(iso: str) -> str:
    try:
        day = dt.date.fromisoformat(iso)
    except ValueError:
        return iso
    return f"{day.day}.{day.month:02d}"


def diff(old: dict, new: dict) -> Changes:
    """Сравнить два слепка. Пустой old = сравнивать не с чем, изменений нет.

    Пустой слепок трактуется как «первый запуск», а НЕ как «всё отменили» —
    иначе в первое же утро человек получил бы сообщение об отмене всех уроков
    на три недели вперёд.
    """
    if not old:
        return Changes()
    cancelled, added, moved = [], [], []
    for uid, было in old.items():
        стало = new.get(uid)
        if стало is None:
            cancelled.append((было["date"], было["title"]))
        elif стало["when"] != было["when"]:
            moved.append((стало["date"], стало["title"], _время(стало["when"])))
    for uid, стало in new.items():
        if uid not in old:
            added.append((стало["date"], стало["title"], _время(стало["when"])))
    return Changes(sorted(cancelled), sorted(added), sorted(moved))


def _время(iso: str) -> str:
    try:
        return dt.datetime.fromisoformat(iso).strftime("%H:%M")
    except ValueError:
        return ""


def load_snapshot(path) -> dict:
    """Прочитать прошлый слепок. Нет файла или он битый — пустой словарь.

    Битый файл не должен ломать сводку: он значит лишь «сравнить не с чем»,
    а это ровно то же, что и первый запуск.
    """
    from pathlib import Path

    path = Path(path)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Слепок расписания не прочитался (%s) — считаю первым запуском", exc)
        return {}
    return data if isinstance(data, dict) else {}


def save_snapshot(path, слепок: dict) -> None:
    from pathlib import Path

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # sort_keys — чтобы разница между запусками в git была настоящей разницей
    # расписания, а не перестановкой строк.
    path.write_text(
        json.dumps(слепок, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8"
    )
