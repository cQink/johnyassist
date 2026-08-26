"""Погода: разбор ответа open-meteo и решение «стоит ли писать об этом».

Сеть не трогаем: проверять формулировку «+12…+21, дождь» настоящим запросом
значило бы ждать подходящей погоды.
"""
import urllib.error

import pytest

from johnny import weather
from johnny.weather import fetch, is_notable, summary


def прогноз(code=3, low=12.0, high=21.0, rain=10):
    return {
        "daily": {
            "weather_code": [code, code],
            "temperature_2m_min": [low, low],
            "temperature_2m_max": [high, high],
            "precipitation_probability_max": [rain, rain],
        }
    }


def test_обычная_строка():
    # Разделитель — дефис по просьбе владельца (26.08.2026): многоточие на
    # его телефоне читалось хуже.
    assert summary(прогноз()) == "+12 - +21, пасмурно"


def test_осадки_попадают_в_строку():
    assert summary(прогноз(code=61, rain=60)) == "+12 - +21, дождь, осадки 60%"


def test_маловероятные_осадки_не_упоминаются():
    # 5% дождя — это не новость, а шум в строке, которую читают спросонья.
    assert "осадки" not in summary(прогноз(rain=5))


def test_минус_пишется_знаком_а_плюс_тоже():
    assert summary(прогноз(low=-7.4, high=-1.2)) == "-7 - -1, пасмурно"
    assert summary(прогноз(low=0.4, high=3.6)).startswith("0 - +4")


def test_неизвестный_код_явления_не_ломает_строку():
    assert summary(прогноз(code=777)) == "+12 - +21"


def test_нет_прогноза_нет_строки():
    assert summary(None) == ""
    assert summary({}) == ""
    assert summary({"daily": {}}) == ""


def test_день_за_пределами_прогноза():
    # Спросили про послезавтра, а прогноз на два дня — молчим, а не падаем.
    assert summary(прогноз(), index=5) == ""


def test_советов_нет():
    # Решение человека: погода подаётся фактами, «возьми зонт» отвергнуто.
    строка = summary(прогноз(code=61, rain=90))
    assert not any(слово in строка for слово in ("зонт", "стоит", "лучше", "не забудь"))


# -- о чём стоит написать --

@pytest.mark.parametrize("описание,данные", [
    ("дождь", прогноз(code=61, rain=80)),
    ("гроза", прогноз(code=95)),
    ("снег", прогноз(code=71)),
    ("мороз", прогноз(low=-12.0, high=-6.0)),
    ("жара", прогноз(low=20.0, high=30.0)),
    ("высокая вероятность осадков", прогноз(code=3, rain=70)),
])
def test_такое_будит_телефон(описание, данные):
    assert is_notable(данные), описание


@pytest.mark.parametrize("данные", [прогноз(), прогноз(code=0), прогноз(code=1, rain=15)])
def test_обычная_погода_молчит(данные):
    assert not is_notable(данные)


def test_пороги_настраиваются():
    # На чужой карте... то есть в другом климате: +27 в Стокгольме событие, в
    # другом месте — вторник.
    assert is_notable(прогноз(low=20.0, high=28.0))
    assert not is_notable(прогноз(low=20.0, high=28.0), hot=35)


def test_нет_прогноза_это_не_повод_писать():
    assert not is_notable(None)


# -- запрос --

def test_сбой_сети_даёт_none_а_не_исключение(monkeypatch):
    # Про врача человек должен узнать даже когда метеосервис лежит.
    def падает(url, timeout=None):
        raise urllib.error.URLError("нет сети")

    monkeypatch.setattr(weather.urllib.request, "urlopen", падает)
    assert fetch(59.3, 18.0, "Europe/Stockholm") is None


def test_часовой_пояс_уходит_в_запрос(monkeypatch):
    # Без него «сегодня» у прогноза разъедется со «сегодня» у человека:
    # сводку считает машина в UTC.
    адреса = []

    class Ответ:
        def read(self):
            return b'{"daily": {}}'

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def подделка(url, timeout=None):
        адреса.append(url)
        return Ответ()

    monkeypatch.setattr(weather.urllib.request, "urlopen", подделка)
    fetch(59.3293, 18.0686, "Europe/Stockholm")
    assert "timezone=Europe%2FStockholm" in адреса[0]
    assert "latitude=59.3293" in адреса[0]
