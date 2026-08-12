import johnny.browser as browser


class TabDriver:
    """Драйвер с вкладками: помнит переключения и созданные окна."""

    def __init__(self, handles=("main",)):
        self.handles = list(handles)
        self.current = self.handles[0] if self.handles else None
        self.switched = []
        self.opened = []
        self.created = []
        self.switch_to = _SwitchTo(self)

    @property
    def window_handles(self):
        return list(self.handles)

    @property
    def current_window_handle(self):
        return self.current

    def get(self, url):
        self.opened.append((self.current, url))

    def execute_script(self, script, *args):
        return True

    @property
    def title(self):
        return "тест"


class _SwitchTo:
    def __init__(self, driver):
        self.driver = driver

    def window(self, handle):
        self.driver.current = handle
        self.driver.switched.append(handle)

    def new_window(self, kind):
        handle = f"{kind}-{len(self.driver.handles)}"
        self.driver.handles.append(handle)
        self.driver.current = handle
        self.driver.created.append(kind)


def _use(monkeypatch, driver):
    monkeypatch.setattr(browser, "get_driver", lambda: driver)
    monkeypatch.setattr(browser, "bring_to_front", lambda: None)
    monkeypatch.setattr(browser, "_tab", None)
    return driver


def test_open_creates_a_new_tab_and_remembers_it(monkeypatch):
    driver = _use(monkeypatch, TabDriver())
    browser.open("youtube.com")
    assert driver.created == ["tab"]
    assert browser._tab == driver.current
    assert driver.opened == [(driver.current, "https://youtube.com")]


def test_action_switches_to_johnny_tab(monkeypatch):
    """Без этого цепочка «найди -> включи видео -> фуллскрин» работала
    в трёх разных вкладках."""
    driver = _use(monkeypatch, TabDriver())
    browser.open("youtube.com")
    tab = browser._tab
    driver.switch_to.window("main")  # человек ушёл на свою вкладку
    driver.switched.clear()
    browser._active()
    assert driver.switched == [tab]


def test_lost_johnny_tab_falls_back_to_current(monkeypatch):
    """Человек закрыл вкладку Джони — работаем с текущей, а не падаем."""
    driver = _use(monkeypatch, TabDriver())
    browser.open("youtube.com")
    driver.handles.remove(browser._tab)
    driver.current = "main"
    driver.switched.clear()
    browser._active()
    assert driver.switched == []
    assert driver.current == "main"


def test_browser_without_pages_gets_a_window(monkeypatch):
    """Живой случай: процесс Chrome жив, страниц ноль.

    Нужна не вкладка, а ОКНО — иначе страница создаётся там, где её
    физически некому показать.
    """
    driver = _use(monkeypatch, TabDriver(handles=()))
    browser.open("youtube.com")
    assert driver.created == ["window"], "в браузере без окон вкладка бесполезна"


def test_open_in_current_tab_does_not_create_one(monkeypatch):
    driver = _use(monkeypatch, TabDriver())
    browser.open("youtube.com")
    driver.created.clear()
    browser.open("twitch.tv", new_tab=False)
    assert driver.created == []
    assert driver.opened[-1][1] == "https://twitch.tv"
