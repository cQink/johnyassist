from johnny.browser_target import strip_tab_modifier


def test_strips_all_three_tails():
    for tail in ("в этой вкладке", "в текущей вкладке", "в этом окне"):
        text, new_tab = strip_tab_modifier(f"найди на ютубе котики {tail}")
        assert text == "найди на ютубе котики"
        assert new_tab is False


def test_phrase_without_tail_is_untouched():
    assert strip_tab_modifier("найди на ютубе котики") == (
        "найди на ютубе котики",
        True,
    )


def test_bare_tail_stays_whole():
    """«в этой вкладке» само по себе — не команда, отрезать нечего.

    Иначе фраза превратилась бы в пустую строку и Джони сказал бы
    «Не расслышал», хотя расслышал прекрасно.
    """
    assert strip_tab_modifier("в этой вкладке") == ("в этой вкладке", True)


def test_strips_modifier_from_the_middle():
    """В цепочке модификатор оказывается в середине сам собой."""
    text, new_tab = strip_tab_modifier(
        "найди на ютубе кино в этой вкладке и включи первое видео"
    )
    assert text == "найди на ютубе кино и включи первое видео"
    assert new_tab is False
