"""Решение «какую модель Whisper держать» — без видеокарты и без Steam."""
from johnny.game_watch import decide

БАЗА = dict(normal_model="medium", gaming_model="small", low_mb=2500, high_mb=4500)


def test_игра_запущена_уходим_на_маленькую():
    assert decide("medium", game_running=True, free_mb=6000, **БАЗА) == "small"


def test_игры_нет_и_памяти_вдоволь_возвращаемся():
    assert decide("small", game_running=False, free_mb=6000, **БАЗА) == "medium"


def test_памяти_мало_уходим_даже_без_steam():
    """Игра мимо Steam: Epic, свой лаунчер, просто exe."""
    assert decide("medium", game_running=False, free_mb=1000, **БАЗА) == "small"


def test_между_порогами_ничего_не_трогаем():
    """Гистерезис. Без мёртвой зоны Джони метался бы туда-сюда, и каждое
    метание стоит 2.4 секунды."""
    assert decide("small", game_running=False, free_mb=3000, **БАЗА) == "small"
    assert decide("medium", game_running=False, free_mb=3000, **БАЗА) == "medium"


def test_мёртвая_зона_шире_того_что_освобождает_подмена():
    """Подмена сама меняет свободную память на 1490 МБ (2138 − 648). Если бы
    зазор между порогами был уже, уход на small тут же перевёл бы порог
    возврата, и Джони закольцевался бы."""
    assert БАЗА["high_mb"] - БАЗА["low_mb"] > 1490


def test_пустая_игровая_модель_выключает_переключение():
    """Значение по умолчанию: ничего не делаем никогда."""
    assert decide(
        "medium",
        normal_model="medium",
        gaming_model="",
        game_running=True,
        free_mb=100,
        low_mb=2500,
        high_mb=4500,
    ) == "medium"


def test_без_ответа_от_видеокарты_остаёмся_на_обычной():
    """nvidia-smi не отвечает — про игру ничего не известно. Молча уходить на
    маленькую модель было бы решением на пустом месте."""
    assert decide("medium", game_running=False, free_mb=None, **БАЗА) == "medium"
