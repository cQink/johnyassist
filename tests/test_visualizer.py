"""Геометрия кружка: зажигается на имени, пульсирует под голос.

Модуль без Tk ровно затем, чтобы это проверялось здесь, а не глазами на живой
панели. Проверяем поведение, а не конкретные числа: точные радиусы — дело
дизайна и меняются, а «на паузе кружок погашен» меняться не должно.
"""

import johnny.activity as activity
import johnny.visualizer as visualizer

SIZE = 132


def snap(phase: str, level: float = 0.0) -> activity.Snapshot:
    return activity.Snapshot(phase, level)


# --- зажигание ------------------------------------------------------------


def test_idle_circle_is_not_lit():
    """До имени кружок не горит — иначе «услышал» ничем не отмечено."""
    assert visualizer.frame(snap(activity.IDLE), 0.0, SIZE).active is False


def test_wake_word_lights_the_circle():
    """Требование в чистом виде: имя прозвучало — кружок зажёгся."""
    assert visualizer.frame(snap(activity.WAKE), 0.0, SIZE).active is True


def test_listening_and_thinking_and_speaking_stay_lit():
    """Весь ход целиком: кружок не должен моргать между фазами одного ответа."""
    for phase in (activity.LISTEN, activity.THINK, activity.SPEAK):
        assert visualizer.frame(snap(phase, 0.5), 0.0, SIZE).active is True, phase


def test_paused_circle_is_dark():
    """Погашенный кружок — единственное отличие «на паузе» от «слушаю»
    для того, кто не читает текст статуса."""
    assert visualizer.frame(snap(activity.PAUSED, 1.0), 0.0, SIZE).active is False


def test_unknown_phase_does_not_crash_and_stays_dark():
    """Фазы — строки; чужое значение не должно ронять таймер отрисовки."""
    frame = visualizer.frame(snap("что-то новое", 1.0), 0.0, SIZE)
    assert frame.active is False
    assert frame.radius > 0


def test_tone_carries_the_phase_for_the_palette():
    assert visualizer.frame(snap(activity.SPEAK), 0.0, SIZE).tone == activity.SPEAK


# --- пульсация ------------------------------------------------------------


def test_louder_voice_gives_a_bigger_circle():
    """Вторая половина требования: пульсирует при разговорах."""
    quiet = visualizer.frame(snap(activity.LISTEN, 0.1), 0.0, SIZE).radius
    loud = visualizer.frame(snap(activity.LISTEN, 1.0), 0.0, SIZE).radius
    assert loud > quiet


def test_circle_never_collapses_to_nothing():
    """Пустой центр карточки читается как поломка, а не как тишина."""
    assert visualizer.frame(snap(activity.IDLE, 0.0), 0.0, SIZE).radius >= visualizer.CELL


def test_circle_fits_the_card():
    """Радиус не должен вылезти за отведённое поле ни на каком уровне."""
    assert visualizer.frame(snap(activity.LISTEN, 1.0), 0.0, SIZE).radius <= SIZE / 2


def test_level_above_one_does_not_blow_the_circle_out():
    """Уровень приходит извне; кадр обязан пережить мусор."""
    over = visualizer.frame(snap(activity.LISTEN, 9.0), 0.0, SIZE)
    at_max = visualizer.frame(snap(activity.LISTEN, 1.0), 0.0, SIZE)
    assert over.radius == at_max.radius


def test_thinking_breathes_without_any_sound():
    """Микрофон уже не слушают и уровень затух в ноль, а работа идёт.

    Без дыхания панель в этот момент не отличить от скриншота — а именно тут
    Whisper на CPU думает десятки секунд.
    """
    radii = {
        visualizer.frame(snap(activity.THINK, 0.0), tick, SIZE).radius
        for tick in (0.0, 0.4, 0.8, 1.2, 1.6)
    }
    assert len(radii) > 1


def test_idle_does_not_breathe():
    """Дыхание значит «занят». В покое кружок обязан стоять."""
    radii = {
        visualizer.frame(snap(activity.IDLE, 0.0), tick, SIZE).radius
        for tick in (0.0, 0.4, 0.8, 1.2, 1.6)
    }
    assert len(radii) == 1


# --- пиксельная сетка -----------------------------------------------------


def test_radius_snaps_to_the_pixel_grid():
    """Дизеринг по краю работает, только если всё кратно клетке."""
    for level in (0.0, 0.13, 0.37, 0.62, 0.99, 1.0):
        frame = visualizer.frame(snap(activity.LISTEN, level), 0.3, SIZE)
        assert frame.radius % visualizer.CELL == 0, level


def test_bars_snap_to_the_grid_too():
    frame = visualizer.frame(snap(activity.LISTEN, 0.8), 0.3, SIZE)
    assert all(h % visualizer.CELL == 0 for h in frame.bars)


# --- волна ----------------------------------------------------------------


def test_wave_is_flat_when_nothing_is_heard():
    """Ровные столбики врали бы про сигнал, которого нет."""
    frame = visualizer.frame(snap(activity.LISTEN, 0.0), 0.0, SIZE)
    assert set(frame.bars) == {0}


def test_wave_is_flat_while_dark():
    frame = visualizer.frame(snap(activity.IDLE, 1.0), 0.0, SIZE)
    assert set(frame.bars) == {0}


def test_wave_rises_with_the_voice():
    frame = visualizer.frame(snap(activity.LISTEN, 1.0), 0.0, SIZE)
    assert any(h > 0 for h in frame.bars)
    assert len(frame.bars) == visualizer.BARS


def test_wave_fades_towards_the_edges():
    """Максимум у центра — иначе волна не читается как исходящая от кружка."""
    frame = visualizer.frame(snap(activity.LISTEN, 1.0), 0.0, SIZE)
    assert frame.bars[0] > frame.bars[-1]


def test_wave_moves_over_time():
    """Застывшая волна выглядит картинкой, а не звуком."""
    first = visualizer.frame(snap(activity.LISTEN, 1.0), 0.0, SIZE).bars
    later = visualizer.frame(snap(activity.LISTEN, 1.0), 0.35, SIZE).bars
    assert first != later
