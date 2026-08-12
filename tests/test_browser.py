import johnny.browser as browser


def test_parse_time_mm_ss():
    assert browser.parse_time_ru("13 42") == 822
    assert browser.parse_time_ru("2 30") == 150
    assert browser.parse_time_ru("1 05") == 65


def test_parse_time_words():
    assert browser.parse_time_ru("5 минут") == 300
    assert browser.parse_time_ru("30 секунд") == 30
    assert browser.parse_time_ru("13 минут 42 секунды") == 822


def test_parse_time_single_number_is_seconds():
    assert browser.parse_time_ru("45") == 45


def test_parse_time_hours():
    assert browser.parse_time_ru("1 час") == 3600
    assert browser.parse_time_ru("1 час 5 минут") == 3900
    assert browser.parse_time_ru("2 часа 3 минуты 4 секунды") == 7384


def test_parse_time_three_numbers_is_hms():
    assert browser.parse_time_ru("1 05 30") == 3930


def test_parse_time_garbage_returns_none():
    assert browser.parse_time_ru("абракадабра") is None
    assert browser.parse_time_ru("") is None


class _NoopSwitchTo:
    def window(self, handle):
        pass

    def new_window(self, kind):
        pass


class FakeDriver:
    """Драйвер, который «готов» только начиная с ready_after-го опроса."""

    # Непустой список: _active() не станет создавать окно (browser._tab здесь None).
    window_handles = ["one"]

    def __init__(self, ready_after: int = 0):
        self.ready_after = ready_after
        self.polls = 0
        self.clicked: list[str] = []
        self.switch_to = _NoopSwitchTo()

    def execute_script(self, script, *args):
        if "click()" in script:
            self.clicked.append(script)
            return True  # настоящий скрипт клика возвращает, состоялся ли он
        self.polls += 1
        return self.polls > self.ready_after


def _fake_driver(monkeypatch, ready_after=0):
    driver = FakeDriver(ready_after)
    monkeypatch.setattr(browser, "get_driver", lambda: driver)
    return driver


def test_wait_for_true_when_ready(monkeypatch):
    _fake_driver(monkeypatch)
    assert browser._wait_for("return true;", timeout=0.05) is True


def test_wait_for_false_on_timeout(monkeypatch):
    driver = _fake_driver(monkeypatch, ready_after=10_000)
    assert browser._wait_for("return true;", timeout=0.05) is False
    assert driver.polls >= 1  # опрашивал, а не сдался молча


def test_click_result_waits_then_clicks(monkeypatch):
    driver = _fake_driver(monkeypatch, ready_after=2)
    assert browser.click_result(1, timeout=2.0) is True
    assert driver.clicked, "клик так и не случился"


def test_click_result_false_when_no_results(monkeypatch):
    driver = _fake_driver(monkeypatch, ready_after=10_000)
    assert browser.click_result(1, timeout=0.05) is False
    assert driver.clicked == []  # вслепую не кликаем


def test_fullscreen_false_when_no_player(monkeypatch):
    _fake_driver(monkeypatch, ready_after=10_000)
    assert browser.fullscreen(timeout=0.05) is False


def test_next_video_true_when_button_present(monkeypatch):
    driver = _fake_driver(monkeypatch)
    assert browser.next_video(timeout=0.05) is True
    assert driver.clicked


def test_click_result_false_if_element_vanished(monkeypatch):
    """Элемент был на момент ожидания и пропал до клика — не падаем, а False."""

    class VanishingDriver(FakeDriver):
        def execute_script(self, script, *args):
            if "click()" in script:
                return False  # скрипт сам увидел, что элемента уже нет
            return True

    monkeypatch.setattr(browser, "get_driver", lambda: VanishingDriver())
    assert browser.click_result(1, timeout=0.05) is False
    assert browser.next_video(timeout=0.05) is False


class PlayerDriver:
    """Страница с плеером. Полный экран включается ТОЛЬКО настоящим нажатием.

    Так ведёт себя Chrome: Fullscreen API требует жеста пользователя, и клик
    из JavaScript им не считается — запрос молча отклоняется.
    """

    # Непустой список: _active() не станет создавать окно (browser._tab здесь None).
    window_handles = ["one"]

    def __init__(self):
        self.fullscreen = False
        self.clicked = []

    def execute_script(self, script, *args):
        if "fullscreenElement" in script:
            return self.fullscreen
        if "focus()" in script:
            return None
        if "click()" in script:
            self.clicked.append(script)  # клик проходит, но экран не меняется
            return True
        return True  # кнопка плеера на месте


def test_fullscreen_uses_a_real_keypress(monkeypatch):
    """Клик из JS Chrome отклоняет — разворачивать должно нажатие клавиши."""
    driver = PlayerDriver()
    keys = []

    def fake_send_key(drv, key):
        keys.append(key)
        drv.fullscreen = True

    monkeypatch.setattr(browser, "get_driver", lambda: driver)
    monkeypatch.setattr(browser, "bring_to_front", lambda: None)
    monkeypatch.setattr(browser, "_send_key", fake_send_key)
    assert browser.fullscreen(timeout=0.05) is True
    assert keys == ["f"]
    assert driver.clicked == [], "на клик из JS полагаться нельзя"


def test_fullscreen_false_when_screen_did_not_change(monkeypatch):
    """Главное: не врать. Нажали, а экран не развернулся — это провал.

    Ровно этот случай раньше уходил как успех: кнопка находилась, клик
    «проходил», Джони отвечал «Готово», а видео оставалось в окне.
    """
    driver = PlayerDriver()
    monkeypatch.setattr(browser, "get_driver", lambda: driver)
    monkeypatch.setattr(browser, "bring_to_front", lambda: None)
    monkeypatch.setattr(browser, "_send_key", lambda drv, key: None)
    assert browser.fullscreen(timeout=0.05) is False


def test_fullscreen_true_if_already_fullscreen(monkeypatch):
    driver = PlayerDriver()
    driver.fullscreen = True
    sent = []
    monkeypatch.setattr(browser, "get_driver", lambda: driver)
    monkeypatch.setattr(browser, "_send_key", lambda drv, key: sent.append(key))
    assert browser.fullscreen(timeout=0.05) is True
    assert sent == [], "уже развёрнуто — нажимать нечего, иначе свернём обратно"


def test_fullscreen_raises_window_before_pressing_key(monkeypatch):
    """ЖИВОЙ БАГ (2026-08-05): «открой видео на полный экран» из цепочки
    (в отличие от отдельной «полный экран») никогда не поднимало окно
    Chrome-Джони на передний план — Fullscreen API молча отклоняет запрос,
    если окно-источник не активно у ОС, а document.hasFocus() (внутренний
    фокус вкладки) этого не отражает. Живые логи: video/movie_player
    находились мгновенно (0.02с) в обоих прогонах, hasFocus был то False,
    то True — но fullscreenElement не появлялся НИ РАЗУ, пока bring_to_front
    не вызывался вовсе."""
    driver = PlayerDriver()
    calls = []
    monkeypatch.setattr(browser, "get_driver", lambda: driver)
    monkeypatch.setattr(browser, "bring_to_front", lambda: calls.append("front"))

    def fake_send_key(drv, key):
        calls.append("key")
        drv.fullscreen = True

    monkeypatch.setattr(browser, "_send_key", fake_send_key)
    assert browser.fullscreen(timeout=0.05) is True
    assert calls == ["front", "key"], "окно обязано подняться ДО нажатия «f»"


class YoutubeDriver(PlayerDriver):
    """Страница YouTube: в DOM есть #movie_player — фокус должен идти на
    него, а не на голый <video> (см. живой баг 2026-08-03: фокус на <video>
    работал на Twitch, но сломал YouTube — там хоткей «f» слушает именно
    контейнер плеера)."""

    def execute_script(self, script, *args):
        if "focus()" in script:
            assert "movie_player" in script, (
                "на YouTube фокус обязан идти через #movie_player, иначе "
                "хоткей «f» не сработает — см. живой баг 2026-08-03"
            )
            self.fullscreen = True  # реальный Chrome развернул бы экран
            return None
        return super().execute_script(script, *args)


def test_fullscreen_focuses_movie_player_on_youtube(monkeypatch):
    driver = YoutubeDriver()
    monkeypatch.setattr(browser, "get_driver", lambda: driver)
    monkeypatch.setattr(browser, "bring_to_front", lambda: None)
    monkeypatch.setattr(browser, "_send_key", lambda drv, key: None)
    assert browser.fullscreen(timeout=0.05) is True


class _SwitchTo:
    def __init__(self, driver):
        self._driver = driver

    def window(self, handle):
        self._driver.current = handle

    def new_window(self, kind):
        pass


class MultiTabDriver:
    """Несколько открытых вкладок, видео есть только на одной из них."""

    def __init__(self, video_on: str):
        self.window_handles = ["tab1", "tab2"]
        self.current = "tab1"
        self._video_on = video_on
        self.switch_to = _SwitchTo(self)
        self.fullscreen = False

    def execute_script(self, script, *args):
        if "fullscreenElement" in script:
            return self.fullscreen
        if "focus()" in script:
            self.fullscreen = True
            return None
        if "querySelector('video')" in script:
            return self.current == self._video_on
        return True


def test_fullscreen_finds_video_on_a_different_tab(monkeypatch):
    """ЖИВОЙ БАГ (2026-08-03): «полный экран» не находил плеер, хотя видео
    реально играло — подозрение пользователя подтвердилось: `_tab` (вкладка,
    которую Джони сам открыл в последний раз) указывала не туда, куда сейчас
    смотрит человек. fullscreen обязан поискать среди ВСЕХ открытых вкладок,
    прежде чем сдаться."""
    driver = MultiTabDriver(video_on="tab2")
    monkeypatch.setattr(browser, "get_driver", lambda: driver)
    monkeypatch.setattr(browser, "_tab", "tab1")  # Джони думает, что видео на tab1
    monkeypatch.setattr(browser, "bring_to_front", lambda: None)
    monkeypatch.setattr(browser, "_send_key", lambda drv, key: None)
    assert browser.fullscreen(timeout=0.05) is True
    assert browser._tab == "tab2", "должен запомнить вкладку, где реально нашёл видео"


def test_fullscreen_false_when_no_tab_has_video(monkeypatch):
    driver = MultiTabDriver(video_on="nowhere")
    monkeypatch.setattr(browser, "get_driver", lambda: driver)
    monkeypatch.setattr(browser, "_tab", "tab1")
    assert browser.fullscreen(timeout=0.05) is False
