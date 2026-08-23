"""Календарь: повторы, предупреждения и стойкость к кривому файлу.

Даты проверяются на фиксированных числах, а не на «сегодня»: тест, зависящий
от дня запуска, однажды покраснеет в високосный год и никто не поймёт почему.
"""
import datetime as dt

import pytest

from johnny.events import (
    Event,
    describe,
    load_events,
    next_occurrence,
    occurrences_on,
    parse_events,
)

ПН = dt.date(2026, 8, 24)      # понедельник


def событие(**kwargs) -> Event:
    поля = dict(date=dt.date(2026, 8, 24), text="дело")
    поля.update(kwargs)
    return Event(**поля)


# -- разбор файла --

def test_обычная_запись_разбирается_целиком():
    события = parse_events([
        {"date": "2026-09-15", "text": "день рождения у мамы", "repeat": "yearly",
         "warn": 3, "time": "14:30"}
    ])
    assert события == [Event(dt.date(2026, 9, 15), "день рождения у мамы", "yearly", 3, "14:30")]


def test_дата_из_yaml_приходит_объектом_date_и_это_тоже_работает():
    # yaml.safe_load сам разбирает 2026-09-15 в date — строкой она сюда не дойдёт.
    assert parse_events([{"date": dt.date(2026, 9, 15), "text": "врач"}])[0].date == dt.date(2026, 9, 15)


def test_дата_со_временем_в_одном_поле_не_роняет_запись():
    события = parse_events([{"date": dt.datetime(2026, 9, 15, 14, 30), "text": "врач"}])
    assert события[0].date == dt.date(2026, 9, 15)


def test_кривая_запись_пропускается_а_соседние_остаются():
    # Главное свойство: одна опечатка не отменяет всю утреннюю сводку.
    события = parse_events([
        {"date": "не дата", "text": "мусор"},
        {"text": "без даты"},
        {"date": "2026-09-15"},
        "вообще не запись",
        {"date": "2026-09-16", "text": "врач"},
    ])
    assert [e.text for e in события] == ["врач"]


def test_неизвестный_повтор_считается_разовым():
    assert parse_events([{"date": "2026-09-15", "text": "врач", "repeat": "иногда"}])[0].repeat == "once"


def test_предупреждение_длиннее_периода_урезается():
    # warn: 10 у еженедельного означало бы строку в сводке каждый день.
    событие_ = parse_events([{"date": "2026-09-15", "text": "х", "repeat": "weekly", "warn": 10}])[0]
    assert событие_.warn == 6


def test_нет_файла_это_пустой_календарь_а_не_ошибка(tmp_path):
    assert load_events(tmp_path / "нет.yaml") == []


def test_сломанный_yaml_не_роняет_сводку(tmp_path):
    path = tmp_path / "calendar.yaml"
    path.write_text("- date: 2026-09-15\n   text: криво\n  плохо:\n", encoding="utf-8")
    assert load_events(path) == []


def test_файл_читается_целиком(tmp_path):
    path = tmp_path / "calendar.yaml"
    path.write_text("- date: 2026-09-15\n  text: врач\n", encoding="utf-8")
    assert load_events(path) == [Event(dt.date(2026, 9, 15), "врач")]


# -- повторы --

def test_разовое_событие_в_прошлом_больше_не_наступит():
    assert next_occurrence(событие(date=dt.date(2026, 1, 1)), ПН) is None


def test_ежегодное_переходит_на_следующий_год():
    др = событие(date=dt.date(1990, 9, 15), repeat="yearly")
    assert next_occurrence(др, dt.date(2026, 9, 16)) == dt.date(2027, 9, 15)


def test_ежегодное_в_свой_день_это_сегодня():
    др = событие(date=dt.date(1990, 9, 15), repeat="yearly")
    assert next_occurrence(др, dt.date(2026, 9, 15)) == dt.date(2026, 9, 15)


def test_двадцать_девятое_февраля_в_невисокосный_год_отмечаем_двадцать_восьмого():
    # Поздравить на день раньше несравнимо лучше, чем не поздравить вовсе.
    др = событие(date=dt.date(2024, 2, 29), repeat="yearly")
    assert next_occurrence(др, dt.date(2026, 1, 1)) == dt.date(2026, 2, 28)
    assert next_occurrence(др, dt.date(2028, 1, 1)) == dt.date(2028, 2, 29)


def test_ежемесячное_пропускает_месяц_без_такого_числа():
    # Обратное решение, чем у 29 февраля, и это осознанно: «плачу 31-го» и
    # «плачу 28 февраля» — разные обещания.
    платёж = событие(date=dt.date(2026, 1, 31), repeat="monthly")
    assert next_occurrence(платёж, dt.date(2026, 2, 1)) == dt.date(2026, 3, 31)


def test_ежемесячное_обычное_число():
    платёж = событие(date=dt.date(2026, 1, 10), repeat="monthly")
    assert next_occurrence(платёж, dt.date(2026, 2, 11)) == dt.date(2026, 3, 10)


def test_еженедельное_держится_своего_дня_недели():
    тренировка = событие(date=dt.date(2026, 8, 24), repeat="weekly")   # понедельник
    assert next_occurrence(тренировка, dt.date(2026, 8, 25)).weekday() == 0
    assert next_occurrence(тренировка, dt.date(2026, 8, 25)) == dt.date(2026, 8, 31)


def test_повтор_до_своей_первой_даты_ещё_не_начался():
    тренировка = событие(date=dt.date(2026, 9, 7), repeat="weekly")
    assert next_occurrence(тренировка, dt.date(2026, 8, 24)) == dt.date(2026, 9, 7)


# -- что попадает в сводку --

def test_без_предупреждения_событие_видно_только_в_свой_день():
    события = [событие(date=dt.date(2026, 8, 26), text="врач")]
    assert occurrences_on(события, dt.date(2026, 8, 25)) == []
    assert [o.event.text for o in occurrences_on(события, dt.date(2026, 8, 26))] == ["врач"]


def test_предупреждение_это_обратный_отсчёт_а_не_один_окрик():
    события = [событие(date=dt.date(2026, 8, 27), text="врач", warn=3)]
    дни = [
        len(occurrences_on(события, dt.date(2026, 8, д)))
        for д in (23, 24, 25, 26, 27, 28)
    ]
    assert дни == [0, 1, 1, 1, 1, 0]


def test_у_попавшего_в_сводку_известно_сколько_дней_осталось():
    события = [событие(date=dt.date(2026, 8, 27), text="врач", warn=3)]
    (найдено,) = occurrences_on(события, dt.date(2026, 8, 25))
    assert (найдено.days_left, найдено.date) == (2, dt.date(2026, 8, 27))


def test_предупреждение_работает_через_границу_года():
    новый_год = событие(date=dt.date(2027, 1, 1), text="новый год", repeat="yearly", warn=2)
    (найдено,) = occurrences_on([новый_год], dt.date(2026, 12, 30))
    assert найдено.days_left == 2


def test_прошедшее_разовое_в_сводку_не_лезет():
    assert occurrences_on([событие(date=dt.date(2020, 1, 1), warn=365)], ПН) == []


def test_сначала_ближайшее_потом_по_времени():
    события = [
        событие(date=ПН, text="вечером", time="19:00"),
        событие(date=ПН + dt.timedelta(days=1), text="завтра", warn=1),
        событие(date=ПН, text="утром", time="08:00"),
        событие(date=ПН, text="без времени"),
    ]
    assert [o.event.text for o in occurrences_on(события, ПН)] == [
        "утром", "вечером", "без времени", "завтра"
    ]


def test_пустой_календарь_даёт_пустую_сводку():
    assert occurrences_on([], ПН) == []


# -- как это читается --

@pytest.mark.parametrize("осталось,хвост", [
    (0, ""), (1, " (завтра)"), (2, " (послезавтра)"),
    (3, " (через 3 дня)"), (5, " (через 5 дней)"), (21, " (через 21 день)"),
])
def test_строка_события_склоняется_по_русски(осталось, хвост):
    события = [событие(date=ПН + dt.timedelta(days=осталось), text="врач", warn=365)]
    (найдено,) = occurrences_on(события, ПН)
    assert describe(найдено) == "врач" + хвост


def test_время_попадает_в_строку():
    события = [событие(date=ПН, text="врач", time="14:30")]
    assert describe(occurrences_on(события, ПН)[0]) == "14:30 — врач"
