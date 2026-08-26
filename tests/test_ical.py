"""Разбор iCalendar. Файл здесь всегда синтетический — настоящий фид школы
живой, меняется каждую неделю и тянет за собой сеть.

Образцы построены по тому, что реально отдаёт SchoolSoft (проверено на живой
выгрузке 26.08.2026): TZID=Europe/Berlin у уроков, VALUE=DATE у меню, перенос
длинных строк, экранированные переводы строк в описании.
"""
import datetime as dt

import pytest

from johnny.ical import VEvent, parse, parse_datetime, split_property, unescape, unfold

ФИД = """BEGIN:VCALENDAR
VERSION:2.0
X-WR-TIMEZONE:Europe/Stockholm
BEGIN:VTIMEZONE
TZID:Europe/Berlin
END:VTIMEZONE
BEGIN:VEVENT
UID:lesson-63447-2-w35
DTSTART;TZID=Europe/Berlin:20260826T100000
DTEND;TZID=Europe/Berlin:20260826T112000
SUMMARY:Lektion Kemi nivå 1
DESCRIPTION:David Mehdipoor 7 KEMI1000XTE25
END:VEVENT
BEGIN:VEVENT
UID:lunchmenu-35-2-2
DTSTART;VALUE=DATE:20260826
SUMMARY:Matsedel
DESCRIPTION:Huvudrätt\\nChili con carne med ris
END:VEVENT
END:VCALENDAR
"""


# -- склейка перенесённых строк --

def test_перенесённая_строка_склеивается():
    # По RFC длинное свойство режется, продолжение помечается пробелом.
    строки = unfold("DESCRIPTION:начало\n  продолжение\nUID:1")
    assert строки == ["DESCRIPTION:начало продолжение", "UID:1"]


def test_склейка_по_табу_тоже():
    assert unfold("A:раз\n\tдва")[0] == "A:раздва"


def test_маркер_склейки_удаляется_вместе_с_переводом_строки():
    """Пробел-маркер — часть разметки, а не текста, и по RFC 5545 удаляется.

    Отсюда и два пробела в тесте выше: первый склеивает, второй остаётся
    словом. Если бы маркер сохранялся, в описаниях заданий появлялись бы
    лишние пробелы ровно каждые 75 символов.
    """
    assert unfold("A:раз\n два")[0] == "A:раздва"
    assert unfold("A:раз\n  два")[0] == "A:раз два"


def test_crlf_не_мешает():
    assert unfold("A:1\r\nB:2\r\n") == ["A:1", "B:2", ""]


# -- экранирование --

@pytest.mark.parametrize("вход,выход", [
    (r"Huvudrätt\nChili", "Huvudrätt\nChili"),
    (r"раз\, два", "раз, два"),
    (r"раз\; два", "раз; два"),
    (r"путь\\каталог", "путь\\каталог"),
    ("без экранирования", "без экранирования"),
])
def test_экранирование_разворачивается(вход, выход):
    assert unescape(вход) == выход


def test_экранированный_слэш_не_становится_переводом_строки():
    # «\\n» — это слэш и буква n, а не перевод строки. Порядок разбора важен:
    # развернув сначала слэш, получили бы \n и съели букву.
    assert unescape(r"C:\\new") == "C:\\new"


# -- свойства --

def test_параметры_свойства_разбираются():
    имя, параметры, значение = split_property("DTSTART;TZID=Europe/Berlin:20260826T100000")
    assert (имя, параметры, значение) == ("DTSTART", {"TZID": "Europe/Berlin"}, "20260826T100000")


def test_двоеточия_внутри_значения_не_ломают_разбор():
    # В описании задания легко встречается ссылка — двоеточий там сколько угодно.
    имя, _, значение = split_property("DESCRIPTION:смотри http://sms.schoolsoft.se/a:b")
    assert (имя, значение) == ("DESCRIPTION", "смотри http://sms.schoolsoft.se/a:b")


def test_кавычки_вокруг_параметра_снимаются():
    _, параметры, _ = split_property('DTSTART;TZID="Europe/Berlin":20260826T100000')
    assert параметры["TZID"] == "Europe/Berlin"


def test_строка_без_двоеточия_это_не_свойство():
    assert split_property("МУСОР") is None


# -- даты --

def test_дата_без_времени():
    значение = parse_datetime("20260826", {"VALUE": "DATE"})
    assert значение == dt.date(2026, 8, 26)
    assert not isinstance(значение, dt.datetime)


def test_восемь_цифр_без_параметра_тоже_дата():
    assert parse_datetime("20260826", {}) == dt.date(2026, 8, 26)


def test_время_с_часовым_поясом():
    значение = parse_datetime("20260826T100000", {"TZID": "Europe/Berlin"})
    assert значение.hour == 10 and значение.tzinfo is not None
    assert значение.utcoffset() == dt.timedelta(hours=2)      # лето


def test_время_в_utc_по_суффиксу_z():
    значение = parse_datetime("20260826T080000Z", {})
    assert значение.utcoffset() == dt.timedelta(0)


def test_неизвестный_пояс_не_теряет_урок():
    # Стенные часы важнее пояса: человеку нужно «в 10:00», а не смещение.
    значение = parse_datetime("20260826T100000", {"TZID": "Луна/Море"})
    assert значение.hour == 10 and значение.tzinfo is None


@pytest.mark.parametrize("мусор", ["", "завтра", "2026-08-26", "20260832T100000", "20260826T990000"])
def test_непонятная_дата_это_none(мусор):
    assert parse_datetime(мусор, {}) is None


# -- события целиком --

def test_из_фида_достаются_только_события():
    события = parse(ФИД)
    assert [e.uid for e in события] == ["lesson-63447-2-w35", "lunchmenu-35-2-2"]


def test_поля_урока():
    урок = parse(ФИД)[0]
    assert урок.summary == "Lektion Kemi nivå 1"
    assert урок.description == "David Mehdipoor 7 KEMI1000XTE25"
    assert урок.start.hour == 10 and урок.end.hour == 11
    assert урок.all_day is False
    assert урок.date() == dt.date(2026, 8, 26)


def test_событие_на_весь_день():
    меню = parse(ФИД)[1]
    assert меню.all_day is True
    assert меню.date() == dt.date(2026, 8, 26)
    assert меню.description == "Huvudrätt\nChili con carne med ris"


def test_событие_без_uid_пропускается():
    # По UID события сравниваются между выгрузками; безымянное бесполезно.
    фид = "BEGIN:VEVENT\nSUMMARY:безымянное\nEND:VEVENT\n"
    assert parse(фид) == []


def test_вложенный_компонент_не_подмешивает_свои_поля():
    # У VALARM свои SUMMARY и DESCRIPTION — они не должны стать полями события.
    фид = (
        "BEGIN:VEVENT\nUID:1\nSUMMARY:урок\n"
        "BEGIN:VALARM\nSUMMARY:напоминание\nTRIGGER:-PT15M\nEND:VALARM\n"
        "END:VEVENT\n"
    )
    (событие,) = parse(фид)
    assert событие.summary == "урок"


def test_пустой_файл():
    assert parse("") == [] and parse("BEGIN:VCALENDAR\nEND:VCALENDAR\n") == []


def test_событие_без_дат_не_роняет_разбор():
    (событие,) = parse("BEGIN:VEVENT\nUID:1\nSUMMARY:что-то\nEND:VEVENT\n")
    assert событие.start is None and событие.date() is None


def test_vevent_сравнимы_между_собой():
    # raw исключён из сравнения: одинаковые по смыслу события из двух выгрузок
    # не должны различаться из-за DTSTAMP.
    a = VEvent(uid="1", summary="урок", raw={"DTSTAMP": "1"})
    b = VEvent(uid="1", summary="урок", raw={"DTSTAMP": "2"})
    assert a == b
