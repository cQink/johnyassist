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


def test_неизвестный_часовой_пояс_не_роняет_запуск():
    assert digest.local_now("Луна/Море_Спокойствия") is not None


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
