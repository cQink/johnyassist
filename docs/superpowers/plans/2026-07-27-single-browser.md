# Единый браузер и вкладка Джони — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Джони работает в видимой вкладке, которую сам открыл и на которую переключил человека; браузер в системе остаётся один.

**Architecture:** В `browser.py` появляется единственная точка переключения `_active()` — драйвер, наведённый на вкладку Джони. Все действия ходят через неё вместо `get_driver()`. Модификатор фразы «в этой вкладке» отрезается до разбора и доходит до `browser.open` параметром. Отдельный скрипт регистрирует «Chrome (Джони)» как браузер Windows.

**Tech Stack:** Python 3.14, Selenium (уже в проекте), pywin32 (уже в проекте), pytest. Новых зависимостей нет.

## Global Constraints

- Спека: `docs/superpowers/specs/2026-07-27-single-browser-design.md`.
- Тесты: `.\.venv\Scripts\python.exe -m pytest -q` из `D:\assistent`. На старте 309 зелёных.
- Профиль и порт — единственный источник истины `johnny/browser.py` (`_PROFILE`, `_PORT`). Дублировать строкой нельзя: разъедется с кодом при смене порта.
- Реестр только `HKEY_CURRENT_USER`. Ничего в `HKLM`, прав администратора не требуется.
- Всё, что пишет скрипт регистрации, обязано сниматься ключом `--uninstall`.
- Комментарии и сообщения — по-русски.
- Коммиты по одному на задачу, с трейлером `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.

---

## Структура файлов

| Файл | Ответственность |
|---|---|
| `johnny/browser.py` (менять) | Вкладка Джони, `_active()`, оживление браузера без окон |
| `johnny/browser_target.py` (создать) | Отрезание модификатора «в этой вкладке» — чистая функция |
| `johnny/actions.py` (менять) | Проброс `new_tab` до `browser_open`/`open_channel` |
| `johnny/chain.py` (менять) | Проброс `new_tab` в `execute` |
| `johnny/app.py` (менять) | Отрезать модификатор до лестницы разбора |
| `johnny/browser_app.py` (создать) | Регистрация «Chrome (Джони)» в реестре + ярлык |
| `install_browser.py` (создать) | Скрипт установки/снятия, по образцу `install_autostart.py` |
| `tests/test_browser_tabs.py` (создать) | Поддельный драйвер, поведение вкладок |
| `tests/test_browser_target.py` (создать) | Модификатор |

Порядок задач: сначала вкладки (они чинят живой баг сами по себе), потом модификатор, потом регистрация.

---

### Task 1: вкладка Джони

Живой баг: Chrome-Джони существует процессом без окон (замерено — 7 целей, 0 страниц), а драйвер ни разу не перенацеливается. Обе беды лечатся одной точкой входа.

**Files:**
- Modify: `johnny/browser.py`
- Test: `tests/test_browser_tabs.py` (создать)

**Interfaces:**
- Produces: `browser._active()` — драйвер, наведённый на вкладку Джони
- Produces: `browser.open(url: str, new_tab: bool = True) -> None`
- Produces: модульная `browser._tab` — handle текущей вкладки Джони

**Определение «текущей вкладки».** `new_tab=False` означает «переиспользуй вкладку Джони, не плоди новую». Именно вкладку Джони, а не ту, что визуально активна у человека: Selenium не умеет спрашивать браузер, какая вкладка на экране, а полагаться на недокументированный порядок в `/json/list` нельзя. Практически это одно и то же — человек говорит «в этой вкладке», глядя на то, что Джони уже открыл.

- [ ] **Step 1: Write the failing tests**

Создать `tests/test_browser_tabs.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_browser_tabs.py -q`
Expected: FAIL — `AttributeError: module 'johnny.browser' has no attribute '_active'`

- [ ] **Step 3: Implement**

В `johnny/browser.py` рядом с `_driver` добавить:

```python
_tab = None  # handle вкладки, в которой Джони работает сейчас
```

После `get_driver()` добавить:

```python
def _pages(driver) -> list:
    """Список вкладок. Пустой — у браузера нет ни одной страницы."""
    try:
        return driver.window_handles
    except Exception:
        return []


def _active():
    """Драйвер, наведённый на вкладку Джони.

    ЕДИНСТВЕННАЯ точка переключения: раньше её не было вовсе, драйвер
    цеплялся к произвольной вкладке один раз и работал с ней всегда — из-за
    этого цепочка «найди -> включи первое видео -> на полный экран» могла
    отработать в трёх разных местах.

    Браузер без единой страницы (процесс жив, окна закрыты — расширения
    держат его в памяти) получает ОКНО: вкладка там бесполезна, показать её
    негде. Пропавшая вкладка Джони не ошибка — работаем с текущей.
    """
    driver = get_driver()
    if not _pages(driver):
        driver.switch_to.new_window("window")
        return driver
    if _tab is not None and _tab in _pages(driver):
        driver.switch_to.window(_tab)
    return driver
```

Заменить `open`:

```python
def open(url: str, new_tab: bool = True) -> None:
    """Открыть URL. По умолчанию — в новой вкладке, на которую переключаемся.

    new_tab=False («в этой вкладке») переиспользует вкладку Джони.
    """
    global _tab
    full = url if url.startswith(("http://", "https://")) else "https://" + url
    driver = get_driver()
    if not _pages(driver):
        driver.switch_to.new_window("window")
    elif new_tab:
        driver.switch_to.new_window("tab")
    else:
        _active()
    _tab = driver.current_window_handle
    driver.get(full)
    bring_to_front()
```

Заменить `get_driver()` на `_active()` внутри действий — `_wait_for`, `_click_when_ready`, `seek`, `seek_relative`, `click_result`, `fullscreen`, `bring_to_front`. Низкоуровневый `get_driver()` остаётся только для подключения.

Конкретно:
- `_wait_for`: `driver = _active()` вместо `driver = get_driver()`
- `_click_when_ready`: `_active().execute_script(...)` вместо `get_driver().execute_script(...)`
- `seek`, `seek_relative`: `_active().execute_script(...)`
- `click_result`: `_active().execute_script(...)` в теле клика
- `fullscreen`: `driver = _active()` вместо `driver = get_driver()`
- `bring_to_front`: `driver = _active()` — иначе заголовок берётся у чужой вкладки и окно ищется не то

- [ ] **Step 4: Run the whole suite**

**Сначала почини существующие фейки — иначе половина `tests/test_browser.py` упадёт.**

`_active()` первым делом спрашивает `driver.window_handles`. У фейков в `tests/test_browser.py` (`FakeDriver`, `PlayerDriver`, `VanishingDriver`) такого атрибута нет, `_pages` съест исключение и вернёт пустой список — а на пустом списке `_active()` полезет создавать окно через `switch_to`, которого у них тоже нет.

Достаточно объявить в каждом из трёх классов:

```python
    window_handles = ["one"]  # непустой список: _active() не станет создавать окно
```

`switch_to` им не нужен: `browser._tab` в этих тестах остаётся `None`, поэтому переключения не будет.

Run: `.\.venv\Scripts\python.exe -m pytest -q`
Expected: PASS, 314 тестов.

- [ ] **Step 5: Commit**

```bash
git add johnny/browser.py tests/test_browser_tabs.py
git commit -m "fix: Джони работает в своей видимой вкладке

Драйвер ни разу не перенацеливался: цеплялся к произвольной вкладке и
работал с ней всегда, поэтому цепочка могла отработать в трёх разных
местах, а фуллскрин честно не находил плеер.

Плюс лечится браузер-зомби: процесс жив, страниц ноль (замерено — 7
целей, все фоновые). Такому нужно ОКНО, а не вкладка.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: модификатор «в этой вкладке»

**Files:**
- Create: `johnny/browser_target.py`
- Modify: `johnny/actions.py` (`browser_open`, `open_channel`, `execute`)
- Modify: `johnny/chain.py` (`run`)
- Modify: `johnny/app.py` (`handle_command`, `_dispatch`)
- Test: `tests/test_browser_target.py` (создать), `tests/test_app.py`

**Interfaces:**
- Produces: `browser_target.strip_tab_modifier(text: str) -> tuple[str, bool]`
- Changes: `actions.execute(routed, apps, channels=None, new_tab=True)`
- Changes: `actions.browser_open(url, new_tab=True)`, `actions.open_channel(argument, channels, new_tab=True)`
- Changes: `chain.run(steps, config, speaker, new_tab=True)`

- [ ] **Step 1: Write the failing tests**

Создать `tests/test_browser_target.py`:

```python
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
```

Дописать в `tests/test_app.py`:

```python
def test_tab_modifier_reaches_the_browser(monkeypatch):
    import johnny.actions as actions
    from johnny.actions import ActionResult

    seen = {}
    monkeypatch.setattr(
        actions,
        "execute",
        lambda routed, apps, channels, new_tab=True: seen.setdefault("new_tab", new_tab)
        or ActionResult(True, "Готово"),
    )
    config = _config_chain(
        [
            CommandRule("найди на ютубе *", "browser_open", "yt?q={0}"),
            CommandRule("включи первое видео", "browser_click_result", "1"),
        ]
    )
    handle_command(
        "найди на ютубе котики в этой вкладке и включи первое видео",
        config,
        SpySpeaker(answer_played=True),
    )
    assert seen["new_tab"] is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_browser_target.py tests/test_app.py -q -k "modifier or tail or untouched"`
Expected: FAIL — `ModuleNotFoundError: No module named 'johnny.browser_target'`

- [ ] **Step 3: Implement browser_target.py**

```python
"""Модификатор фразы, выбирающий вкладку для открытия.

Отдельные команды вида «найди на ютубе * в этой вкладке» пришлось бы
дописывать к каждой из полусотни сайтовых команд — сто строк и вечная
обязанность помнить про вторую половину. Модификатор снимается с фразы
целиком, поэтому работает со всеми командами сразу, включая будущие.
"""

# Хвосты в нормальной форме: сравнение идёт по нижнему регистру.
_TAILS = ("в этой вкладке", "в текущей вкладке", "в этом окне")


def strip_tab_modifier(text: str) -> tuple[str, bool]:
    """«найди на ютубе кино в этой вкладке» → («найди на ютубе кино», False).

    Второе значение — открывать ли в НОВОЙ вкладке. Хвоста нет → (текст, True).
    """
    lowered = text.lower().strip()
    for tail in _TAILS:
        if lowered.endswith(tail):
            head = lowered[: -len(tail)].strip()
            if head:  # «в этой вкладке» само по себе командой не является
                return head, False
    return text, True
```

- [ ] **Step 4: Implement the new_tab thread**

`johnny/actions.py` — три подписи:

```python
def browser_open(url: str, new_tab: bool = True) -> None:
    """Открыть URL в окне Chrome-Джони; при сбое — в браузере по умолчанию."""
    from . import browser

    try:
        browser.open(url, new_tab=new_tab)
    except Exception:
        open_url(url)


def open_channel(argument: str, channels: dict, new_tab: bool = True) -> None:
    """argument = «platform|имя»: открыть канал по словарю, иначе — буквально."""
    platform, _, alias = argument.partition("|")
    host = _CHANNEL_HOSTS.get(platform, "twitch.tv")
    slug = _resolve_channel(alias, platform, channels or {}) or alias
    browser_open(f"{host}/{slug}", new_tab=new_tab)


def execute(
    routed: RoutedAction, apps: dict[str, str], channels: dict | None = None,
    new_tab: bool = True,
) -> ActionResult:
```

И две ветки внутри `execute`:

```python
    if routed.action == "browser_open":
        browser_open(routed.argument, new_tab=new_tab)
        return ActionResult(True, "Открываю")
    ...
    if routed.action == "open_channel":
        open_channel(routed.argument, channels or {}, new_tab=new_tab)
        return ActionResult(True, "Открываю")
```

`johnny/chain.py` — `run` принимает и пробрасывает:

```python
def run(steps: list[RoutedAction], config, speaker, new_tab: bool = True) -> ChainResult:
```
```python
        result = actions.execute(step, config.apps, config.channels, new_tab=new_tab)
```

`johnny/app.py`:

```python
from .browser_target import strip_tab_modifier
```

`_dispatch` принимает `new_tab` и передаёт дальше:

```python
def _dispatch(routed, config, speaker, new_tab: bool = True) -> Outcome | None:
    if routed.action == "scenario":
        steps = chain.resolve(config.scenarios.get(routed.argument, []), config.commands)
        if steps is None:
            logger.warning("Сценарий %r не собрался: шаг не совпал с командой", routed.argument)
            return None
        return _chain_outcome(chain.run(steps, config, speaker, new_tab), "сценарий")
    _respond(speaker, execute(routed, config.apps, config.channels, new_tab=new_tab))
    return Outcome(routed.via)
```

В `handle_command` сразу после проверки на пустую строку:

```python
    # Модификатор снимается ДО разбора: тогда он работает со всеми командами
    # сразу, включая цепочки, и ни одной команде не нужен парный шаблон.
    text, new_tab = strip_tab_modifier(text)
```

И передать `new_tab` во все четыре места, где вызываются `_dispatch` и `chain.run`.

- [ ] **Step 5: Почини подделки `execute` в существующих тестах**

`execute` теперь зовут с именованным `new_tab`, а фейки в тестах объявлены тремя позиционными параметрами и упадут с `TypeError`.

Найти их:

```bash
grep -rn "routed, apps, channels" tests/
```

В каждой найденной подделке дописать четвёртый параметр со значением по умолчанию:

```python
lambda routed, apps, channels, new_tab=True: ...
```

То же самое в именованных функциях-подделках (`def fake_execute(routed, apps, channels)` → `def fake_execute(routed, apps, channels, new_tab=True)`). Значение по умолчанию обязательно: часть тестов зовёт `execute` напрямую тремя аргументами.

- [ ] **Step 6: Run the whole suite**

Run: `.\.venv\Scripts\python.exe -m pytest -q`
Expected: PASS, 318 тестов.

- [ ] **Step 7: Commit**

```bash
git add johnny/browser_target.py johnny/actions.py johnny/chain.py johnny/app.py tests/
git commit -m "feat: модификатор «в этой вкладке»

Снимается с фразы до разбора, поэтому работает со всеми командами сразу
и внутри цепочек. Парные шаблоны в commands.yaml не нужны.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: «Chrome (Джони)» как браузер Windows

**Files:**
- Create: `johnny/browser_app.py`
- Create: `install_browser.py`
- Test: `tests/test_browser_app.py` (создать)

**Interfaces:**
- Produces: `browser_app.launch_command(chrome: str, profile: str, port: int) -> str`
- Produces: `browser_app.install() -> str` (возвращает имя приложения)
- Produces: `browser_app.uninstall() -> bool`
- Produces: `browser_app.make_shortcut() -> Path`

- [ ] **Step 1: Write the failing test**

Создать `tests/test_browser_app.py`:

```python
import johnny.browser as browser
from johnny import browser_app


def test_launch_command_carries_profile_and_port():
    line = browser_app.launch_command(r"C:\chrome.exe", r"D:\p", 9222)
    assert '--user-data-dir="D:\\p"' in line
    assert "--remote-debugging-port=9222" in line
    assert line.endswith('"%1"'), "Windows подставляет ссылку вместо %1"


def test_launch_command_uses_the_same_profile_as_browser():
    """Профиль и порт — из browser.py, а не переписаны строкой.

    Иначе при смене порта регистрация тихо разъедется с кодом, и ссылки
    начнут открывать браузер, к которому Джони не может подключиться.
    """
    line = browser_app.launch_command(browser._chrome_exe(), str(browser._PROFILE), browser._PORT)
    assert str(browser._PROFILE) in line
    assert str(browser._PORT) in line
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_browser_app.py -q`
Expected: FAIL — `ImportError: cannot import name 'browser_app'`

- [ ] **Step 3: Implement browser_app.py**

```python
"""Регистрация «Chrome (Джони)» как браузера Windows.

Отдельный профиль Джони нужен вынужденно: с Chrome 136 отладочный порт
запрещён на профиле по умолчанию. Значит основным браузером должен стать
профиль Джони — иначе ссылки из других программ открывают второй Chrome,
до которого Джони не дотягивается.

Ассоциацию Windows защищает хешем, программно её не переставить. Скрипт
только РЕГИСТРИРУЕТ приложение, чтобы оно появилось в списке браузеров;
выбирает человек руками, один раз.
"""

import winreg
from pathlib import Path

from . import browser

APP_NAME = "Chrome (Джони)"
PROG_ID = "JohnnyChromeHTML"

_CLIENT_KEY = rf"Software\Clients\StartMenuInternet\{APP_NAME}"
_CAPABILITIES = rf"{_CLIENT_KEY}\Capabilities"
_PROGID_KEY = rf"Software\Classes\{PROG_ID}"


def launch_command(chrome: str, profile: str, port: int) -> str:
    """Строка запуска для реестра. %1 — ссылка, которую подставит Windows."""
    return (
        f'"{chrome}" --user-data-dir="{profile}" '
        f'--remote-debugging-port={port} -- "%1"'
    )


def _set(path: str, name: str, value: str) -> None:
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, path) as key:
        winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)


def _delete_tree(path: str) -> bool:
    """Удалить ключ со всеми подключами. False — его и не было."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
            while True:
                try:
                    child = winreg.EnumKey(key, 0)
                except OSError:
                    break
                _delete_tree(f"{path}\\{child}")
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
        return True
    except FileNotFoundError:
        return False


def install() -> str:
    """Зарегистрировать приложение. Возвращает имя, видимое в настройках."""
    chrome = browser._chrome_exe()
    command = launch_command(chrome, str(browser._PROFILE), browser._PORT)

    _set(rf"{_PROGID_KEY}\shell\open\command", "", command)
    _set(rf"{_PROGID_KEY}\DefaultIcon", "", f"{chrome},0")
    _set(_PROGID_KEY, "", "Джони: ссылка в Chrome")

    _set(_CLIENT_KEY, "", APP_NAME)
    _set(rf"{_CLIENT_KEY}\DefaultIcon", "", f"{chrome},0")
    _set(rf"{_CLIENT_KEY}\shell\open\command", "", command)
    _set(_CAPABILITIES, "ApplicationName", APP_NAME)
    _set(_CAPABILITIES, "ApplicationDescription", "Chrome на профиле Джони")
    for protocol in ("http", "https"):
        _set(rf"{_CAPABILITIES}\URLAssociations", protocol, PROG_ID)
    for suffix in (".htm", ".html"):
        _set(rf"{_CAPABILITIES}\FileAssociations", suffix, PROG_ID)

    _set("Software\\RegisteredApplications", APP_NAME, _CAPABILITIES)
    return APP_NAME


def uninstall() -> bool:
    """Снять регистрацию. False — её и не было."""
    removed = _delete_tree(_CLIENT_KEY) | _delete_tree(_PROGID_KEY)
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, "Software\\RegisteredApplications", 0,
            winreg.KEY_SET_VALUE,
        ) as key:
            winreg.DeleteValue(key, APP_NAME)
            removed = True
    except FileNotFoundError:
        pass
    return bool(removed)


def make_shortcut() -> Path:
    """Ярлык на рабочем столе — его человек закрепит на панели задач."""
    import win32com.client  # из pywin32

    desktop = Path.home() / "Desktop"
    path = desktop / f"{APP_NAME}.lnk"
    chrome = browser._chrome_exe()
    shell = win32com.client.Dispatch("WScript.Shell")
    shortcut = shell.CreateShortcut(str(path))
    shortcut.TargetPath = chrome
    shortcut.Arguments = (
        f'--user-data-dir="{browser._PROFILE}" --remote-debugging-port={browser._PORT}'
    )
    shortcut.IconLocation = chrome
    shortcut.Save()
    return path
```

- [ ] **Step 4: Implement install_browser.py**

```python
"""Сделать профиль Джони основным браузером.

  python install_browser.py             # зарегистрировать
  python install_browser.py --uninstall # убрать
"""
import sys

from johnny import browser_app


def main() -> None:
    if "--uninstall" in sys.argv:
        print("Регистрация убрана" if browser_app.uninstall() else "Её и не было")
        return
    name = browser_app.install()
    path = browser_app.make_shortcut()
    print(f"Зарегистрировано приложение: {name}")
    print(f"Ярлык на рабочем столе: {path}")
    print()
    print("Остался один шаг руками — Windows не даёт выбрать браузер программно:")
    print("  1. Параметры → Приложения → Приложения по умолчанию")
    print(f"  2. Найти «{name}» в списке")
    print("  3. Назначить его для HTTP и HTTPS")
    print()
    print("Потом перетащи ярлык с рабочего стола на панель задач вместо старого Chrome.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run the whole suite**

Run: `.\.venv\Scripts\python.exe -m pytest -q`
Expected: PASS, 320 тестов.

- [ ] **Step 6: Commit**

```bash
git add johnny/browser_app.py install_browser.py tests/test_browser_app.py
git commit -m "feat: регистрация «Chrome (Джони)» как браузера Windows

Профиль и порт берутся из browser.py, а не переписаны строкой — иначе при
смене порта регистрация разъедется с кодом. Всё снимается --uninstall.
Выбор в настройках Windows остаётся за человеком: ассоциация защищена
хешем и программно не переставляется.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Живая проверка (делает человек)

1. `.\.venv\Scripts\python.exe install_browser.py`, дальше по напечатанным шагам.
2. Перетащить ярлык «Chrome (Джони)» на панель задач, старый Chrome открепить.
3. Закрыть оба браузера полностью, перезапустить Джони (Выход в трее → ярлык).
4. Сказать: «Джони, найди на ютубе Exile Show и включи первое видео на полный экран». Всё должно произойти в **одной новой видимой вкладке**, куда Джони переключит сам.
5. Сказать: «Джони, открой твич в этой вкладке» — новая вкладка не появляется.
6. Кликнуть ссылку в Discord — открывается тот же браузер, второго Chrome нет.

Если что-то пошло не так, откат: `python install_browser.py --uninstall` и вернуть старый Chrome в настройках по умолчанию.
