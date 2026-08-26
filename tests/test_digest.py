"""Сводка: что попадает в текст, когда она молчит и как переживает летнее время.

Часы сюда передаются аргументами, а не читаются: проверить зимний запуск
иначе можно было бы только зимой, а именно там и живёт ошибка, из-за которой
сводка приезжает на час не вовремя.
"""
import datetime as dt
from zoneinfo import ZoneInfo

import pytest

import digest
from johnny.events import Event, occurrences_on

СТОКГОЛЬМ = ZoneInfo("Europe/Stockholm")
ДЕНЬ = dt.date(2026, 8, 24)      # понедельник


def события(*тексты, day=ДЕНЬ, **поля):
    записи = [Event(date=day, text=текст, **поля) for текст in тексты]
    return occurrences_on(записи, day)


# -- текст --

def test_утренняя_называет_сегодняшнюю_дату():
    текст = digest.build("morning", [], "", ДЕНЬ, notable=True)
    assert текст.splitlines()[0] == "Доброе утро. Сегодня понедельник, 24 августа."


def test_вечерняя_говорит_про_завтра():
    текст = digest.build("evening", [], "", ДЕНЬ, notable=True)
    assert текст.splitlines()[0] == "Завтра, понедельник, 24 августа."


def test_погода_и_события_вместе():
    текст = digest.build("morning", события("врач"), "+12…+21, дождь", ДЕНЬ, notable=True)
    assert текст.splitlines() == [
        "Доброе утро. Сегодня понедельник, 24 августа.",
        "Погода: +12…+21, дождь",
        "— врач",
    ]


def test_несколько_событий_каждое_своей_строкой():
    текст = digest.build("morning", события("врач", "зал"), "", ДЕНЬ, notable=True)
    assert текст.count("\n— ") == 2


def test_предупреждение_видно_в_сводке():
    записи = [Event(date=ДЕНЬ + dt.timedelta(days=3), text="сдать отчет", warn=3)]
    текст = digest.build("morning", occurrences_on(записи, ДЕНЬ), "", ДЕНЬ, notable=True)
    assert "— сдать отчет (через 3 дня)" in текст


# -- молчание --

def test_нечего_сказать_значит_молчим():
    # Главное правило: сообщение без повода каждый день перестают открывать.
    assert digest.build("morning", [], "+18, ясно", ДЕНЬ, notable=False) == ""


def test_событие_есть_значит_пишем_даже_при_скучной_погоде():
    assert digest.build("morning", события("врач"), "+18, ясно", ДЕНЬ, notable=False) != ""


def test_погода_стоящая_упоминания_сама_повод_написать():
    assert digest.build("morning", [], "+12…+21, дождь", ДЕНЬ, notable=True) != ""


# -- когда запускаться --

@pytest.mark.parametrize("время,пора", [
    ("07:30", True),      # ровно вовремя
    ("07:25", True),      # часы машины чуть спешат
    ("07:45", True),      # штатное опоздание cron у GitHub Actions
    ("08:20", True),      # опоздание в пределах окна
    ("08:40", False),     # это уже следующий cron-запуск, не наш
    ("06:30", False),
])
def test_окно_запуска(время, пора):
    час, минута = (int(x) for x in время.split(":"))
    now = dt.datetime(2026, 8, 24, час, минута, tzinfo=СТОКГОЛЬМ)
    assert digest.should_run(now, "07:30") is пора


def test_летом_и_зимой_срабатывает_ровно_один_из_двух_запусков():
    """Два cron в UTC против перехода на летнее время.

    В .github/workflows/digest.yml утренняя сводка стоит на 05:30 и 06:30 UTC.
    Летом Стокгольм это UTC+2, зимой UTC+1 — и в каждый из сезонов ровно один
    из двух запусков попадает в окно, а второй отсеивается. Если этот тест
    когда-нибудь покраснеет, значит сводка приходит на час не вовремя.
    """
    опции = {"morning": "07:30", "evening": "21:00"}
    for месяц, ожидаемые in ((7, ["morning", None]), (1, [None, "morning"])):
        сработали = []
        for utc_час, utc_минута in ((5, 30), (6, 30)):
            utc = dt.datetime(2026, месяц, 15, utc_час, utc_минута, tzinfo=dt.timezone.utc)
            сработали.append(digest.choose_kind(utc.astimezone(СТОКГОЛЬМ), опции))
        assert сработали == ожидаемые, f"месяц {месяц}"


def test_вечерний_запуск_узнаётся_как_вечерний():
    now = dt.datetime(2026, 8, 24, 21, 5, tzinfo=СТОКГОЛЬМ)
    assert digest.choose_kind(now, {"morning": "07:30", "evening": "21:00"}) == "evening"


def test_среди_дня_не_запускаемся():
    now = dt.datetime(2026, 8, 24, 15, 0, tzinfo=СТОКГОЛЬМ)
    assert digest.choose_kind(now, {"morning": "07:30", "evening": "21:00"}) is None


def test_кривое_время_в_настройках_не_отменяет_сводку():
    # Лучше лишняя сводка, чем молчание из-за опечатки в settings.yaml.
    assert digest.should_run(dt.datetime(2026, 8, 24, 3, 0), "семь тридцать") is True


def test_неизвестный_часовой_пояс_это_none_а_не_время_машины():
    """Раньше здесь был запасной путь «посчитаю по машине», и он был ловушкой.

    Машина в облаке живёт по UTC — на два часа мимо Стокгольма. should_run не
    попал бы в окно ни разу, сводка не приходила бы никогда, и молчание было
    бы неотличимо от «сегодня нечего сказать».
    """
    assert digest.local_now("Луна/Море_Спокойствия") is None
    assert digest.local_now("Europe/Stockholm") is not None


# -- настройки и секреты --

class Конфиг:
    def __init__(self, digest_block=None, secrets=None):
        self.settings = type("S", (), {"digest": digest_block or {}})()
        self.secrets = secrets or {}


def test_значения_по_умолчанию_есть_и_без_блока_в_настройках():
    # Облачный запуск не должен зависеть от того, дописал ли кто-то блок digest.
    опции = digest.настройки(Конфиг())
    assert опции["timezone"] == "Europe/Stockholm" and опции["morning"] == "07:30"


def test_настройки_перекрывают_умолчания():
    опции = digest.настройки(Конфиг({"morning": "06:00", "latitude": 1.5}))
    assert (опции["morning"], опции["latitude"], опции["evening"]) == ("06:00", 1.5, "21:00")


def test_пустое_значение_в_настройках_не_затирает_умолчание():
    assert digest.настройки(Конфиг({"timezone": ""}))["timezone"] == "Europe/Stockholm"


def test_секреты_из_файла(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    конфиг = Конфиг(secrets={"telegram_bot_token": "т", "telegram_chat_id": "42"})
    assert digest.секреты(конфиг) == ("т", "42")


def test_переменные_окружения_главнее_файла(monkeypatch):
    # В GitHub Actions secrets.yaml нет и быть не должно — он в .gitignore.
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "из_облака")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "99")
    конфиг = Конфиг(secrets={"telegram_bot_token": "из_файла", "telegram_chat_id": "42"})
    assert digest.секреты(конфиг) == ("из_облака", "99")


def test_запуск_по_расписанию_без_часового_пояса_падает_а_не_молчит(monkeypatch):
    """Красный запуск в GitHub видно. Молчащую сводку не видно никак.

    Без зоны решать «утро сейчас или вечер» не по чему, а угадать значит слать
    не вовремя. Поэтому cron-запуск обязан завершиться ошибкой.
    """
    monkeypatch.setattr(digest, "local_now", lambda tz: None)
    assert digest.main([]) == 1


def test_явно_названная_сводка_считается_даже_без_пояса(monkeypatch):
    # Руками или кнопкой Run workflow: решать «утро или вечер» не нужно —
    # уже сказано, и падать не из-за чего.
    monkeypatch.setattr(digest, "local_now", lambda tz: None)
    monkeypatch.setattr(digest.weather, "fetch", lambda *a, **kw: None)
    monkeypatch.setattr(digest.events, "load_events", lambda *a, **kw: [])
    assert digest.main(["morning", "--dry-run"]) == 0


# -- школьная часть --

def школа(**поля):
    return digest.Школа(**поля)


def test_уроки_отдельной_строкой():
    текст = digest.build("morning", [], "", ДЕНЬ, notable=False,
                         школа=школа(lessons="10:00 Kemi, 12:15 Engelska (до 13:20)"))
    assert "Уроки: 10:00 Kemi, 12:15 Engelska (до 13:20)" in текст


def test_обед_отдельной_строкой():
    текст = digest.build("morning", [], "", ДЕНЬ, notable=False,
                         школа=школа(menu="Chili con carne med ris"))
    assert "Обед: Chili con carne med ris" in текст


def test_изменения_стоят_сразу_за_уроками():
    """Это единственная строка, ради которой сводку стоит открыть немедленно.

    В общем списке дел, между днём рождения и контрольной, отмена урока
    потерялась бы — а именно ради неё всё и затевалось.
    """
    текст = digest.build("morning", события("врач"), "+18, ясно", ДЕНЬ, notable=True,
                         школа=школа(lessons="10:00 Kemi", menu="Пюре",
                                     changes=["отменили Kemi (26.08)"]))
    строки = текст.splitlines()
    assert строки[2].startswith("Уроки:")
    assert строки[3] == "! Расписание: отменили Kemi (26.08)"
    assert строки[4].startswith("Обед:")


def test_контрольные_идут_общим_списком_с_делами():
    текст = digest.build("morning", события("врач"), "", ДЕНЬ, notable=False,
                         школа=школа(tasks=["10:10 Prov kap1 (через 8 дней)"]))
    assert текст.splitlines()[-2:] == ["— врач", "— 10:10 Prov kap1 (через 8 дней)"]


def test_одних_уроков_достаточно_чтобы_написать():
    # Утренняя сводка в учебный день осмысленна и без событий и без дождя.
    assert digest.build("morning", [], "+18, ясно", ДЕНЬ, notable=False,
                        школа=школа(lessons="10:00 Kemi")) != ""


def test_выходной_без_уроков_и_без_дел_молчит():
    assert digest.build("morning", [], "+18, ясно", ДЕНЬ, notable=False, школа=школа()) == ""


def test_без_школьного_блока_сводка_прежняя():
    # Обратная совместимость: школы может не быть вовсе (нет ссылки, лето).
    assert digest.build("morning", события("врач"), "", ДЕНЬ, notable=False) == (
        "Доброе утро. Сегодня понедельник, 24 августа.\n— врач"
    )


# -- сбор расписания --

class КонфигСоСсылкой:
    secrets = {"schoolsoft_ical_url": "https://example.invalid/feed"}
    settings = type("S", (), {"digest": {}})()


def test_без_ссылки_в_сеть_не_ходим(monkeypatch):
    def нельзя(*args, **kwargs):
        raise AssertionError("без ссылки на фид ходить некуда")

    monkeypatch.setattr(digest.school, "fetch", нельзя)
    monkeypatch.delenv("SCHOOLSOFT_ICAL_URL", raising=False)
    результат = digest.собрать_расписание(Конфиг(), digest._DEFAULTS, ДЕНЬ, ДЕНЬ, False)
    assert not результат


def test_школу_можно_выключить_настройкой(monkeypatch):
    def нельзя(*args, **kwargs):
        raise AssertionError("school: false значит не ходить вовсе")

    monkeypatch.setattr(digest.school, "fetch", нельзя)
    опции = dict(digest._DEFAULTS, school=False)
    assert not digest.собрать_расписание(КонфигСоСсылкой(), опции, ДЕНЬ, ДЕНЬ, False)


def test_недоступный_фид_не_роняет_сводку(monkeypatch):
    monkeypatch.setattr(digest.school, "fetch", lambda url, **kw: None)
    assert not digest.собрать_расписание(КонфигСоСсылкой(), digest._DEFAULTS, ДЕНЬ, ДЕНЬ, False)


ФИД_ДЛЯ_СВОДКИ = (
    "BEGIN:VEVENT\nUID:lesson-1-2-w35\n"
    "DTSTART;TZID=Europe/Berlin:20260824T100000\n"
    "DTEND;TZID=Europe/Berlin:20260824T112000\n"
    "SUMMARY:Lektion Kemi nivå 1\nEND:VEVENT\n"
)


def test_просмотр_не_съедает_отмену(monkeypatch, tmp_path):
    """--dry-run НЕ сохраняет слепок, и это важнее, чем кажется.

    Иначе человек посмотрел бы, что получится, слепок обновился бы, и
    следующий — настоящий — запуск сравнил бы уже с новым: отмена урока
    исчезла бы, ни разу никому не показавшись.
    """
    путь = tmp_path / "снимок.json"
    monkeypatch.setattr(digest, "SNAPSHOT", путь)
    monkeypatch.setattr(digest.school, "fetch", lambda url, **kw: ФИД_ДЛЯ_СВОДКИ)
    digest.собрать_расписание(КонфигСоСсылкой(), digest._DEFAULTS, ДЕНЬ, ДЕНЬ, True)
    assert not путь.exists()


def test_настоящий_запуск_слепок_сохраняет(monkeypatch, tmp_path):
    путь = tmp_path / "снимок.json"
    monkeypatch.setattr(digest, "SNAPSHOT", путь)
    monkeypatch.setattr(digest.school, "fetch", lambda url, **kw: ФИД_ДЛЯ_СВОДКИ)
    результат = digest.собрать_расписание(КонфигСоСсылкой(), digest._DEFAULTS, ДЕНЬ, ДЕНЬ, False)
    assert результат.lessons.startswith("10:00 Kemi")
    assert "lesson-1-2-w35" in digest.school.load_snapshot(путь)


def test_пропавший_урок_доезжает_до_сводки(monkeypatch, tmp_path):
    путь = tmp_path / "снимок.json"
    monkeypatch.setattr(digest, "SNAPSHOT", путь)
    monkeypatch.setattr(digest.school, "fetch", lambda url, **kw: ФИД_ДЛЯ_СВОДКИ)
    digest.собрать_расписание(КонфигСоСсылкой(), digest._DEFAULTS, ДЕНЬ, ДЕНЬ, False)
    # Вторая выгрузка без этого урока — то есть его отменили.
    monkeypatch.setattr(digest.school, "fetch", lambda url, **kw: "BEGIN:VCALENDAR\nEND:VCALENDAR\n")
    результат = digest.собрать_расписание(КонфигСоСсылкой(), digest._DEFAULTS, ДЕНЬ, ДЕНЬ, False)
    assert результат.changes == ["отменили Kemi (24.08)"]


def test_окружение_главнее_файла_и_для_школы(monkeypatch):
    monkeypatch.setenv("SCHOOLSOFT_ICAL_URL", "https://из-облака/feed")
    assert digest.адрес_школы(КонфигСоСсылкой()) == "https://из-облака/feed"
