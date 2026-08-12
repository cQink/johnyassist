"""Доска состояния для кружка в панели: фаза, уровень, затухание.

Проверяем то, что на живом Tk глазами видно, а регрессию в этом — нет: кружок
зажигается на wake word, пульсирует под голос и сам гаснет, когда речь кончи-
лась. Время здесь подменяется, потому что затухание считается от него, а
sleep в тестах — это плата секундами за арифметику.
"""

import pytest

import johnny.activity as activity


class FakeClock:
    """Заглушка вместо модуля time внутри activity.

    Подменяем ссылку на модуль в пространстве activity, а не time.monotonic
    глобально: второе задело бы весь процесс, включая pytest.
    """

    def __init__(self):
        self.now = 100.0

    def monotonic(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    fake = FakeClock()
    monkeypatch.setattr(activity, "time", fake)
    return fake


@pytest.fixture(autouse=True)
def _clean():
    """Состояние модульное и общее — без сброса тесты зависели бы от порядка."""
    activity.reset()
    yield
    activity.reset()


# --- фазы -----------------------------------------------------------------


def test_starts_idle():
    assert activity.snapshot().phase == activity.IDLE


def test_phase_is_visible_to_the_reader():
    """Цикл прослушивания пишет, панель читает — связи между ними нет."""
    activity.set_phase(activity.WAKE)
    assert activity.snapshot().phase == activity.WAKE


def test_reset_clears_phase_and_level(clock):
    activity.set_phase(activity.SPEAK)
    activity.pulse(1.0)
    activity.reset()
    snap = activity.snapshot()
    assert snap.phase == activity.IDLE
    assert snap.level == 0.0


# --- уровень с микрофона --------------------------------------------------


def test_silence_gives_almost_nothing(clock):
    """Фон (замер 2026-07-27: не выше 0.00065) не должен раздувать кружок."""
    activity.push_level(0.00065)
    assert activity.snapshot().level < 0.2


def test_speech_lifts_the_level(clock):
    """Обычная речь — 0.0012–0.0145; кружок обязан заметно двинуться."""
    activity.push_level(0.0145)
    assert activity.snapshot().level > 0.5


def test_level_never_exceeds_one(clock):
    """Крик громче _LOUD_RMS не должен пробивать шкалу — панель ждёт [0, 1]."""
    activity.push_level(10.0)
    assert activity.snapshot().level == 1.0


def test_negative_rms_is_treated_as_silence(clock):
    activity.push_level(-1.0)
    assert activity.snapshot().level == 0.0


def test_quiet_block_inside_speech_does_not_collapse_the_circle(clock):
    """Главное про пульсацию: блоки по 0.25с попадают и в зазор между слогами.

    Если бы новый уровень просто затирал прошлый, кружок схлопывался бы внутри
    слова и «пульсация» превратилась бы в дрожь.
    """
    activity.push_level(0.0145)
    loud = activity.snapshot().level
    clock.advance(0.25)
    activity.push_level(0.0)
    assert activity.snapshot().level > loud * 0.5


# --- затухание ------------------------------------------------------------


def test_level_fades_when_blocks_stop_coming(clock):
    """Микрофон перестаёт присылать блоки — обновить уровень станет некому.

    Без затухания кружок замер бы раздутым на последнем блоке речи.
    """
    activity.push_level(0.02)
    assert activity.snapshot().level == 1.0
    clock.advance(0.2)
    assert activity.snapshot().level < 1.0


def test_level_reaches_zero_and_stays_there(clock):
    activity.push_level(0.02)
    clock.advance(60.0)
    assert activity.snapshot().level == 0.0


def test_fade_is_slower_than_a_gap_between_words(clock):
    """0.1с тишины внутри фразы не должны погасить кружок целиком."""
    activity.push_level(0.02)
    clock.advance(0.1)
    assert activity.snapshot().level > 0.5


def test_snapshot_does_not_consume_the_level(clock):
    """Читателей может быть несколько (и ни одного) — чтение не меняет доску."""
    activity.push_level(0.02)
    first = activity.snapshot().level
    assert activity.snapshot().level == first


# --- pulse ----------------------------------------------------------------


def test_pulse_sets_level_without_a_microphone(clock):
    """Пока говорит сам Джони, вход он же и заглушает — RMS брать негде."""
    activity.pulse(0.8)
    assert activity.snapshot().level == pytest.approx(0.8)


def test_pulse_clamps_out_of_range_values(clock):
    activity.pulse(5.0)
    assert activity.snapshot().level == 1.0
    activity.pulse(-5.0)
    assert activity.snapshot().level == 0.0
