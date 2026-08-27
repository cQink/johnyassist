"""Школьный календарь: что считается уроком, как узнаётся отмена, что в сводку.

Фид синтетический, но построен по живой выгрузке SchoolSoft от 26.08.2026 —
включая её неряшливости: «Prov Prov kap1» с задвоенным словом и служебную
пометку «visas inte på provschema» прямо внутри названия задания.
"""
import datetime as dt
import urllib.error

import pytest

from johnny import school
from johnny.school import (
    Changes,
    classify,
    clean_task,
    describe_lessons,
    diff,
    fetch,
    lessons_on,
    menu_on,
    parse_feed,
    short_subject,
    snapshot,
    tasks_between,
)

СРЕДА = dt.date(2026, 8, 26)


def урок(uid, час, минута, название, до_часа=None):
    конец = f"DTEND;TZID=Europe/Berlin:20260826T{(до_часа or час + 1):02d}2000\n" if True else ""
    return (
        f"BEGIN:VEVENT\nUID:{uid}\n"
        f"DTSTART;TZID=Europe/Berlin:20260826T{час:02d}{минута:02d}00\n{конец}"
        f"SUMMARY:{название}\nDESCRIPTION:Учитель 7 КОД\nEND:VEVENT\n"
    )


ФИД = (
    "BEGIN:VCALENDAR\n"
    + урок("lesson-1-2-w35", 10, 0, "Lektion Kemi nivå 1", 11)
    + урок("lesson-2-2-w35", 12, 15, "Lektion Engelska nivå 2", 13)
    + "BEGIN:VEVENT\nUID:lunchmenu-35-2-2\nDTSTART;VALUE=DATE:20260826\n"
      "SUMMARY:Matsedel\nDESCRIPTION:Huvudrätt\\nChili con carne med ris\nEND:VEVENT\n"
    + "BEGIN:VEVENT\nUID:lunchmenu-35-2-3\nDTSTART;VALUE=DATE:20260826\n"
      "SUMMARY:Matsedel\nDESCRIPTION:Dagens gröna\\nChili sin carne med ris\nEND:VEVENT\n"
    + "BEGIN:VEVENT\nUID:ps_assignment-1420-2026-09-03T10:10\n"
      "DTSTART;TZID=Europe/Berlin:20260903T101000\nSUMMARY:Prov Prov kap1\n"
      "DESCRIPTION:Prov kap1 kemi1\nEND:VEVENT\n"
    + "BEGIN:VEVENT\nUID:ps_planning-257\nDTSTART;VALUE=DATE:20260813\n"
      "SUMMARY:Planering Fysik 1\nDESCRIPTION:-\nEND:VEVENT\n"
    + "END:VCALENDAR\n"
)


# -- что чем является --

@pytest.mark.parametrize("uid,тип", [
    ("lesson-63447-2-w35", "lesson"),
    ("lunchmenu-35-2-2", "menu"),
    ("ps_assignment-1420-2026-09-03T10:10", "task"),
    ("ps_planning-257", "planning"),
    ("что-то-новое-42", "other"),
])
def test_тип_события_берётся_из_uid(uid, тип):
    # По тексту заголовка так нельзя: «Prov» встречается и как тип, и внутри
    # названия, а у домашки тип вообще склеен со служебной пометкой.
    assert classify(uid) == тип


# -- названия --

@pytest.mark.parametrize("полное,короткое", [
    ("Lektion Kemi nivå 1", "Kemi"),
    ("Lektion Matematik fortsättning nivå 1c", "Matematik fortsättning"),
    ("Lektion Svenska som andra språk nivå 2", "Svenska som andra språk"),
    ("Lektion Artificiell Intelligens Nivå 1", "Artificiell Intelligens"),
    ("Lektion Mentorstid", "Mentorstid"),
])
def test_название_предмета_укорачивается(полное, короткое):
    assert short_subject(полное) == короткое


@pytest.mark.parametrize("вырожденное", ["Lektion ", " nivå 1"])
def test_укорачивание_не_съедает_название_целиком(вырожденное):
    """Правила срезают служебные части — но не всё до пустоты.

    Такие заголовки в фиде школы не встречаются, и в том и дело: правило,
    оставившее пустую строку, испортило бы сводку молча. Пусть уж лучше
    останется как было — длинное имя читается, пустое нет.
    """
    assert short_subject(вырожденное) == вырожденное.strip()


@pytest.mark.parametrize("грязное,чистое", [
    ("Prov Prov kap1", "Prov kap1"),
    ("Läxa visas inte på provschema Kapitel 1 Instuderingsfrågor 1",
     "Läxa Kapitel 1 Instuderingsfrågor 1"),
    ("Seminarium Kapitel 1 Diskutera 1", "Seminarium Kapitel 1 Diskutera 1"),
])
def test_заголовок_задания_чистится(грязное, чистое):
    assert clean_task(грязное) == чистое


# -- разбор фида --

def test_фид_разбирается_по_типам():
    события = parse_feed(ФИД)
    assert [e.kind for e in события].count("lesson") == 2
    assert [e.kind for e in события].count("menu") == 2
    assert [e.kind for e in события].count("task") == 1


def test_уроки_дня_по_порядку():
    уроки = lessons_on(parse_feed(ФИД), СРЕДА)
    assert [(u.time(), u.title) for u in уроки] == [("10:00", "Kemi"), ("12:15", "Engelska")]


def test_чужой_день_пуст():
    assert lessons_on(parse_feed(ФИД), СРЕДА + dt.timedelta(days=1)) == []


def test_строка_уроков_с_концом_дня():
    уроки = lessons_on(parse_feed(ФИД), СРЕДА)
    assert describe_lessons(уроки) == "10:00 Kemi, 12:15 Engelska (до 13:20)"


def test_нет_уроков_нет_строки():
    assert describe_lessons([]) == ""


def test_из_двух_блюд_берём_основное():
    # Huvudrätt, а не Dagens gröna: сводку читают за две секунды, а не выбирают.
    assert menu_on(parse_feed(ФИД), СРЕДА) == "Chili con carne med ris"


def test_меню_на_день_без_меню():
    assert menu_on(parse_feed(ФИД), dt.date(2026, 12, 25)) == ""


# -- задания --

def test_задание_попадает_в_окно():
    задания = tasks_between(parse_feed(ФИД), СРЕДА, СРЕДА + dt.timedelta(days=30))
    assert [(t.date(), t.title) for t in задания] == [(dt.date(2026, 9, 3), "Prov kap1")]


def test_задание_за_окном_не_показывается():
    assert tasks_between(parse_feed(ФИД), СРЕДА, СРЕДА + dt.timedelta(days=3)) == []


def test_одно_задание_названо_один_раз_ближайшей_датой():
    """SchoolSoft вешает задание на каждое занятие курса — в фиде оно не одно.

    В сводке оно должно быть один раз и ближайшей датой: именно она и есть
    срок, о котором стоит помнить.
    """
    фид = (
        "BEGIN:VEVENT\nUID:ps_assignment-1407-2026-09-10T12:10\n"
        "DTSTART;TZID=Europe/Berlin:20260910T121000\nSUMMARY:Läxa Kapitel 1\nEND:VEVENT\n"
        "BEGIN:VEVENT\nUID:ps_assignment-1407-2026-09-04T10:30\n"
        "DTSTART;TZID=Europe/Berlin:20260904T103000\nSUMMARY:Läxa Kapitel 1\nEND:VEVENT\n"
    )
    задания = tasks_between(parse_feed(фид), СРЕДА, СРЕДА + dt.timedelta(days=30))
    assert len(задания) == 1 and задания[0].date() == dt.date(2026, 9, 4)


# -- снимок --

def уроки_снимка(слепок: dict) -> set:
    """Слепок без служебного ключа с границами окна."""
    return {k for k in слепок if k != school.WINDOW}


def test_в_снимок_идут_только_уроки():
    assert уроки_снимка(snapshot(parse_feed(ФИД), СРЕДА)) == {"lesson-1-2-w35", "lesson-2-2-w35"}


def test_снимок_помнит_своё_окно():
    # Без этого сравнение объявит вчерашние уроки отменёнными: окно скользит.
    слепок = snapshot(parse_feed(ФИД), СРЕДА, days=21)
    assert слепок[school.WINDOW] == {"from": "2026-08-26", "to": "2026-09-16"}


def test_снимок_ограничен_окном():
    # Иначе разница между выгрузками включала бы весь учебный год и стала бы
    # нечитаемой, а перенос плана на май — не новость для утренней сводки.
    слепок = snapshot(parse_feed(ФИД), СРЕДА + dt.timedelta(days=60))
    assert уроки_снимка(слепок) == set()


# -- сравнение выгрузок: единственный способ узнать про отмену --

def test_первый_запуск_это_не_отмена_всего():
    """Пустой прошлый слепок значит «сравнить не с чем», а не «всё отменили».

    Иначе в первое же утро человек получил бы сообщение об отмене всех
    уроков на три недели вперёд.
    """
    новый = snapshot(parse_feed(ФИД), СРЕДА)
    assert not diff({}, новый)


def test_пропавший_урок_это_отмена():
    старый = snapshot(parse_feed(ФИД), СРЕДА)
    новый = dict(старый)
    del новый["lesson-1-2-w35"]
    изменения = diff(старый, новый)
    assert изменения.cancelled == [("2026-08-26", "Kemi")]
    assert "отменили Kemi" in изменения.lines()[0]


def test_сдвинутое_время_это_перенос():
    старый = snapshot(parse_feed(ФИД), СРЕДА)
    новый = dict(старый)
    новый["lesson-1-2-w35"] = dict(старый["lesson-1-2-w35"], when="2026-08-26T14:00:00+02:00")
    изменения = diff(старый, новый)
    assert изменения.moved and "перенесли на 14:00" in изменения.lines()[0]


def test_новый_урок_это_добавление():
    старый = snapshot(parse_feed(ФИД), СРЕДА)
    новый = dict(старый)
    новый["lesson-9"] = {"when": "2026-08-26T08:00:00+02:00", "title": "Fysik", "date": "2026-08-26"}
    изменения = diff(старый, новый)
    assert изменения.added and "добавили Fysik в 08:00" in изменения.lines()[0]


def test_ничего_не_изменилось():
    слепок = snapshot(parse_feed(ФИД), СРЕДА)
    assert not diff(слепок, dict(слепок))


def test_changes_пустой_ложен():
    assert not Changes() and Changes(cancelled=[("2026-08-26", "Kemi")])


# -- сохранение и чтение слепка --

def test_слепок_переживает_запись_и_чтение(tmp_path):
    путь = tmp_path / "снимок.json"
    слепок = snapshot(parse_feed(ФИД), СРЕДА)
    school.save_snapshot(путь, слепок)
    assert school.load_snapshot(путь) == слепок


def test_нет_файла_слепка_это_первый_запуск(tmp_path):
    assert school.load_snapshot(tmp_path / "нет.json") == {}


def test_битый_слепок_не_роняет_сводку(tmp_path):
    # Битый файл значит ровно «сравнить не с чем» — как и первый запуск.
    путь = tmp_path / "снимок.json"
    путь.write_text("{это не json", encoding="utf-8")
    assert school.load_snapshot(путь) == {}


def test_слепок_пишется_упорядоченно(tmp_path):
    # Иначе разница в git была бы перестановкой строк, а не сменой расписания.
    путь = tmp_path / "снимок.json"
    school.save_snapshot(путь, {"б": {"when": "1"}, "а": {"when": "2"}})
    текст = путь.read_text(encoding="utf-8")
    assert текст.index('"а"') < текст.index('"б"')


# -- сеть --

def test_сбой_сети_не_роняет_сводку(monkeypatch):
    # Про врача и про дождь человек должен узнать, даже когда SchoolSoft лежит.
    def падает(request, timeout=None):
        raise urllib.error.URLError("нет сети")

    monkeypatch.setattr(school.urllib.request, "urlopen", падает)
    assert fetch("https://example.invalid/feed") is None


def test_пустая_ссылка_в_сеть_не_ходит(monkeypatch):
    def нельзя(*args, **kwargs):
        raise AssertionError("без ссылки в сеть ходить не за чем")

    monkeypatch.setattr(school.urllib.request, "urlopen", нельзя)
    assert fetch("") is None


# -- переименование предметов настройкой --

def test_предмет_переименовывается_по_короткому_имени():
    # «Svenska som andra språk» человек называет просто «Svenska».
    assert short_subject("Lektion Svenska som andra språk nivå 2",
                         {"Svenska som andra språk": "Svenska"}) == "Svenska"


def test_переименование_работает_и_по_полному_имени():
    # В настройках можно написать то, что видишь в сводке, — или то, что
    # отдаёт SchoolSoft. Гадать, что получилось после правил, не нужно.
    assert short_subject("Lektion Kemi nivå 1", {"Lektion Kemi nivå 1": "Химия"}) == "Химия"


def test_переименование_не_зависит_от_регистра():
    assert short_subject("Lektion Kemi nivå 1", {"kemi": "Химия"}) == "Химия"


def test_чужое_переименование_не_трогает_предмет():
    assert short_subject("Lektion Kemi nivå 1", {"Fysik": "Физика"}) == "Kemi"


def test_переименование_доезжает_до_разбора_фида():
    события = parse_feed(ФИД, {"Kemi": "Химия"})
    assert [e.title for e in lessons_on(события, СРЕДА)] == ["Химия", "Engelska"]


def test_блюдо_в_несколько_строк_схлопывается():
    """Школа пишет часть блюд в две строки — в сводке это разорвало бы строку.

    Живой пример из фида: «Pastasallad vegetarisk, serveras med Smakis» и
    «vid Odenkampen i Vinterviken» — одно блюдо, два переноса.
    """
    фид = (
        "BEGIN:VEVENT\nUID:lunchmenu-35-2-2\nDTSTART;VALUE=DATE:20260826\n"
        "SUMMARY:Matsedel\n"
        + r"DESCRIPTION:Huvudrätt\nPastasallad vegetarisk\, serveras med Smakis\nvid Odenkampen"
        + "\nEND:VEVENT\n"
    )
    блюдо = menu_on(parse_feed(фид), СРЕДА)
    assert "\n" not in блюдо
    assert блюдо == "Pastasallad vegetarisk, serveras med Smakis vid Odenkampen"


# -- скользящее окно: главный источник ложных отмен --

def test_сдвиг_окна_на_сутки_не_выдумывает_отмен():
    """Вчерашний слепок покрывал 26.08–16.09, сегодняшний 27.08–17.09.

    Уроки 26 августа выпали сзади (просто прошли), уроки 17 сентября вошли
    спереди. Без сравнения по пересечению окон это выглядело бы как три отмены
    и три добавления — и так и выглядело на первом облачном запуске 27.08.2026.
    """
    события = parse_feed(ФИД)
    вчера = snapshot(события, СРЕДА)
    сегодня = snapshot(события, СРЕДА + dt.timedelta(days=1))
    assert not diff(вчера, сегодня)


def test_настоящая_отмена_внутри_пересечения_видна():
    """Сторож против чрезмерного лечения.

    Заглушив ложные отмены, легко заглушить и настоящие — а это ровно то, ради
    чего сравнение и заводилось. Урок 28 августа лежит в обоих окнах: и во
    вчерашнем, и в сегодняшнем. Его пропажа — настоящая отмена.
    """
    фид = (
        "BEGIN:VEVENT\nUID:lesson-77\nDTSTART;TZID=Europe/Berlin:20260828T090000\n"
        "DTEND;TZID=Europe/Berlin:20260828T102000\nSUMMARY:Lektion Teknik nivå 2\nEND:VEVENT\n"
    )
    вчера = snapshot(parse_feed(фид), СРЕДА)
    сегодня = snapshot([], СРЕДА + dt.timedelta(days=1))
    assert diff(вчера, сегодня).cancelled == [("2026-08-28", "Teknik")]


def test_отмена_завтрашнего_урока_видна():
    завтра = СРЕДА + dt.timedelta(days=1)
    фид = (
        "BEGIN:VEVENT\nUID:lesson-9\nDTSTART;TZID=Europe/Berlin:20260827T100000\n"
        "DTEND;TZID=Europe/Berlin:20260827T112000\nSUMMARY:Lektion Fysik nivå 1b\nEND:VEVENT\n"
    )
    было = snapshot(parse_feed(фид), СРЕДА)
    стало = snapshot([], завтра)
    assert diff(было, стало).cancelled == [("2026-08-27", "Fysik")]


def test_совсем_устаревший_слепок_не_объявляет_всё_отменённым():
    события = parse_feed(ФИД)
    древний = snapshot(события, СРЕДА - dt.timedelta(days=300))
    свежий = snapshot(события, СРЕДА)
    assert not diff(древний, свежий)


def test_слепок_без_границ_окна_переживается():
    # Файлы, снятые до появления ключа _window: границы выводятся из самих дат.
    события = parse_feed(ФИД)
    старый = {k: v for k, v in snapshot(события, СРЕДА).items() if k != school.WINDOW}
    новый = snapshot(события, СРЕДА)
    assert not diff(старый, новый)
