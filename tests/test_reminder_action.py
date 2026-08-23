"""Голосовая команда календаря: что попадает в файл и что Джони говорит вслух.

Календарь во всех тестах — временный файл: настоящий config/calendar.yaml
принадлежит человеку, и тест, дописывающий в него «врача», был бы уже не
тестом, а порчей данных.
"""
import datetime as dt

import pytest

from johnny import events
from johnny.actions import execute
from johnny.actions.registry import registry
from johnny.router import RoutedAction


@pytest.fixture(autouse=True)
def календарь(tmp_path, monkeypatch):
    path = tmp_path / "calendar.yaml"
    monkeypatch.setattr(events, "DEFAULT_PATH", path)
    return path


def выполнить(action, argument, config=None):
    return execute(RoutedAction(action=action, argument=argument), apps={}, config=config)


def test_действия_зарегистрированы():
    assert "remind" in registry and "calendar" in registry


def test_напоминание_попадает_в_файл(календарь):
    результат = выполнить("remind", "завтра про врача")
    assert результат.ok
    (записано,) = events.load_events(календарь)
    assert записано.text == "врача"
    assert записано.date == dt.date.today() + dt.timedelta(days=1)


def test_джони_проговаривает_дату_вслух():
    # Единственный момент, когда человек может заметить ошибку разбора.
    assert выполнить("remind", "завтра про врача").message == "Записал на завтра: врача"


def test_повтор_и_запас_проговариваются():
    ответ = выполнить("remind", "каждый год 15 сентября день рождения у мамы за 3 дня").message
    assert ответ == "Записал: каждый год 15 сентября — день рождения у мамы. Предупрежу за 3 дня"


@pytest.mark.parametrize("фраза,ответ", [
    # У дней недели разный род: «каждый понедельник», но «каждую среду».
    ("каждый понедельник вынести мусор", "Записал: каждый понедельник — вынести мусор"),
    ("каждую среду про тренировку", "Записал: каждую среду — тренировку"),
    ("каждое воскресенье позвонить бабушке", "Записал: каждое воскресенье — позвонить бабушке"),
    ("каждый месяц 10 числа заплатить", "Записал: каждое 10 число — заплатить"),
])
def test_повторы_называются_по_русски(фраза, ответ):
    assert выполнить("remind", фраза).message == ответ


def test_предлог_не_удваивается_в_подтверждении():
    # «Записал на в пятницу» — так было до правки; human_date отдаёт дату с
    # предлогом, потому что её же читают в обзоре недели.
    import datetime as dt_

    from johnny.actions.reminder_action import _подтверждение

    пятница = dt_.date(2026, 8, 28)
    сообщение = _подтверждение(events.Event(пятница, "посылку"), dt_.date(2026, 8, 23))
    assert сообщение == "Записал на пятницу, 28 августа: посылку"


def test_время_проговаривается():
    assert ", в 14:30" in выполнить("remind", "завтра в 14:30 про врача").message


def test_непонятная_дата_это_отказ_а_не_запись_на_сегодня(календарь):
    результат = выполнить("remind", "про врача")
    assert not результат.ok
    assert not календарь.exists()


def test_запись_дописывается_а_не_переписывает_файл(календарь):
    календарь.write_text(
        "# мой комментарий\n- date: 2030-01-01\n  text: старое дело\n", encoding="utf-8"
    )
    выполнить("remind", "завтра про врача")
    текст = календарь.read_text(encoding="utf-8")
    # Ровно то, чем болеет panel.py: yaml.dump стёр бы и комментарий, и всё,
    # что человек написал в файле руками.
    assert "# мой комментарий" in текст
    assert [e.text for e in events.load_events(календарь)] == ["старое дело", "врача"]


def test_первая_запись_создаёт_файл_с_шапкой(календарь):
    выполнить("remind", "завтра про врача")
    assert календарь.read_text(encoding="utf-8").startswith("# Календарь Джони")


def test_кавычки_и_двоеточия_в_надиктованном_тексте_не_ломают_файл(календарь):
    выполнить("remind", 'завтра про "встречу": в офисе')
    assert [e.text for e in events.load_events(календарь)] == ['"встречу": в офисе']


def test_сегодня_на_пустом_календаре():
    assert выполнить("calendar", "today").message == "На сегодня ничего не записано"


def test_завтра_на_пустом_календаре():
    assert выполнить("calendar", "tomorrow").message == "На завтра ничего не записано"


def test_неделя_на_пустом_календаре():
    assert выполнить("calendar", "week").message == "На неделю ничего не записано"


def test_записанное_сегодня_зачитывается_сегодня():
    выполнить("remind", "сегодня про врача")
    assert выполнить("calendar", "today").message == "Сегодня: врача"


def test_записанное_на_завтра_не_попадает_в_сегодня():
    выполнить("remind", "завтра про врача")
    assert выполнить("calendar", "today").message == "На сегодня ничего не записано"
    assert выполнить("calendar", "tomorrow").message == "Завтра: врача"


def test_неделя_называет_событие_один_раз(календарь):
    # С warn: 3 событие попадает в сводку четыре дня подряд — в обзоре недели
    # это означало бы одну и ту же строку четырежды.
    выполнить("remind", "через 3 дня про врача предупреди за 3 дня")
    assert выполнить("calendar", "week").message.count("врача") == 1


class СКлючом:
    """Конфиг, в котором ключ Groq есть — значит запасной путь доступен."""

    secrets = {"groq_api_key": "ключ"}

    class settings:
        groq_model = "модель"


def test_модель_не_дёргается_когда_свой_разбор_справился(monkeypatch):
    # Сеть на каждое «напомни завтра» — это полсекунды задержки и деньги
    # за то, что таблица разбирает мгновенно и бесплатно.
    def провайдер(key, model):
        def спросить(prompt):
            raise AssertionError("Groq не должен вызываться на понятной фразе")

        return спросить

    monkeypatch.setattr("johnny.brain_groq.make_provider", провайдер)
    assert выполнить("remind", "завтра про врача", config=СКлючом()).ok


def test_модель_подхватывает_фразу_которую_парсер_не_понял(monkeypatch, календарь):
    завтра = (dt.date.today() + dt.timedelta(days=1)).isoformat()
    monkeypatch.setattr(
        "johnny.brain_groq.make_provider",
        lambda key, model: lambda prompt: '{"date": "%s", "text": "к стоматологу"}' % завтра,
    )
    результат = выполнить("remind", "в первый рабочий день после выходных к стоматологу", config=СКлючом())
    assert результат.ok
    assert [e.text for e in events.load_events(календарь)] == ["к стоматологу"]


def test_без_ключа_groq_запасного_пути_просто_нет():
    # Конфиг без секретов — обычное дело; команда обязана работать и так.
    class Пустой:
        secrets = {}

    assert not выполнить("remind", "когда-нибудь потом про врача", config=Пустой()).ok


def test_сбой_записи_не_роняет_джони(monkeypatch):
    def падает(*args, **kwargs):
        raise OSError("диск только для чтения")

    monkeypatch.setattr(events, "append_event", падает)
    результат = выполнить("remind", "завтра про врача")
    assert (результат.ok, результат.message) == (False, "Не смог записать в календарь")
