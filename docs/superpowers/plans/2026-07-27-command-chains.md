# Цепочки команд — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Одна фраза — несколько действий: «запусти обс и громкость 20», «найди X на ютубе и включи первое видео на весь экран», «режим стрима».

**Architecture:** Новый модуль `johnny/chain.py` превращает список фраз в список `RoutedAction` и выполняет его по очереди. Три источника списка (локальный сплит по союзам, сценарии из `scenarios.yaml`, модель) сходятся в одну функцию `chain.resolve`, которая прогоняет каждую фразу через `route_exact` — поэтому ни один источник не может изобрести действие. Первый провал останавливает остаток, звук успеха один в конце.

**Tech Stack:** Python 3.14, pytest, PyYAML, Selenium (уже в проекте). Новых зависимостей нет.

## Global Constraints

- Спека: `docs/superpowers/specs/2026-07-27-command-chains-design.md`. При расхождении плана и спеки — прав план, спека уже устарела в мелочах.
- Запуск тестов: `.\.venv\Scripts\python.exe -m pytest -q` из `D:\assistent`. На старте 262 теста зелёные.
- Потолок цепочки — **5 шагов**, константа `chain.MAX_STEPS`.
- Опасные действия (`shutdown`/`restart`/`sleep`) запрещены в цепочках на всех источниках. Проверка — существующая `router.is_unsafe_action`.
- Внутри цепочки только **точное** совпадение. Нечёткое сравнение (`_fuzzy_match*`) внутрь кусков не пускать.
- Комментарии и сообщения — по-русски, как во всём проекте.
- Коммиты — по одному на задачу, сообщение по-русски, с трейлером:
  `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`

---

## Структура файлов

| Файл | Ответственность |
|---|---|
| `johnny/chain.py` (создать) | Сплит фразы, сборка списка шагов, выполнение цепочки |
| `johnny/router.py` (менять) | + `route_exact` — точное совпадение отдельной функцией |
| `johnny/browser.py` (менять) | + `_wait_for`; `click_result`/`fullscreen`/`next_video` возвращают `bool` |
| `johnny/actions.py` (менять) | Проброс `bool` из браузера в `ActionResult` |
| `johnny/config.py` (менять) | Чтение `scenarios.yaml`, сценарии как `CommandRule` |
| `johnny/brain.py` (менять) | Разбор `{"steps": [...]}` от модели |
| `johnny/app.py` (менять) | Новый порядок разбора, разворачивание сценария |
| `config/scenarios.yaml` (создать) | Сценарии пользователя |
| `tests/test_chain.py` (создать) | Сплиттер, сборка, выполнение |

Задачи идут снизу вверх: сначала фундамент (`route_exact`, браузер), потом `chain.py`, потом подключение к `app.py`. Каждая задача оставляет проект с зелёными тестами.

---

### Task 1: `route_exact` — точное совпадение отдельной функцией

Сейчас точное совпадение — первый цикл внутри `route()`. Цепочкам оно нужно отдельно, причём в двух режимах: со шаблонами (`"запусти *"`) и без них.

**Files:**
- Modify: `johnny/router.py:199-215`
- Test: `tests/test_router.py`

**Interfaces:**
- Produces: `route_exact(text: str, commands: list[CommandRule], *, literal_only: bool = False) -> RoutedAction | None`

- [ ] **Step 1: Write the failing tests**

Дописать в конец `tests/test_router.py`:

```python
def test_route_exact_matches_template():
    commands = [CommandRule(pattern="запусти *", action="launch_app", template="{0}")]
    assert router.route_exact("запусти обс", commands) == RoutedAction("launch_app", "обс")


def test_route_exact_literal_only_skips_templates():
    commands = [CommandRule(pattern="запусти *", action="launch_app", template="{0}")]
    assert router.route_exact("запусти обс", commands, literal_only=True) is None


def test_route_exact_literal_only_matches_fixed_phrase():
    commands = [CommandRule(pattern="сверни все", action="system", template="minimize_all")]
    assert router.route_exact(
        "сверни всё", commands, literal_only=True
    ) == RoutedAction("system", "minimize_all")


def test_route_exact_does_not_guess():
    """Кривую фразу route() поймает нечётко, а route_exact обязана промолчать."""
    commands = [CommandRule(pattern="громкость 5", action="set_volume", template="5")]
    assert router.route("стронкость 5", commands) is not None
    assert router.route_exact("стронкость 5", commands) is None
```

Проверить шапку файла: нужны импорты `router`, `RoutedAction`, `CommandRule`. Если их нет — добавить в существующем стиле файла.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_router.py -q -k route_exact`
Expected: FAIL — `AttributeError: module 'johnny.router' has no attribute 'route_exact'`

- [ ] **Step 3: Implement**

Заменить `route()` в `johnny/router.py` на две функции:

```python
def route_exact(
    text: str, commands: list[CommandRule], *, literal_only: bool = False
) -> RoutedAction | None:
    """Только точное совпадение, без всякого угадывания.

    literal_only=True игнорирует шаблоны со «звёздочкой» и совпадает лишь с
    фиксированными фразами. Это нужно цепочкам: фразу, целиком совпавшую с
    фиксированной командой, резать по союзам нельзя — она заведомо одна.
    """
    norm = _normalize(text)
    for rule in commands:
        if literal_only and "*" in rule.pattern:
            continue
        match = _pattern_to_regex(rule.pattern).match(norm)
        if match:
            return RoutedAction(action=rule.action, argument=rule.template.format(*match.groups()))
    return None


def route(text: str, commands: list[CommandRule]) -> RoutedAction | None:
    # 1. Точное совпадение (в т.ч. шаблоны со «*»).
    exact = route_exact(text, commands)
    if exact is not None:
        return exact
    # 2. Нечёткое: и целые фразы, и шаблоны. Берём совпадение с большей похожестью.
    norm = _normalize(text)
    candidates = [
        found
        for found in (_fuzzy_match(norm, commands), _fuzzy_match_template(norm, commands))
        if found is not None
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda pair: pair[0])[1]
```

- [ ] **Step 4: Run the whole suite**

Run: `.\.venv\Scripts\python.exe -m pytest -q`
Expected: PASS, тестов стало 266 (было 262). Все старые тесты `route()` обязаны пройти без правок — это и есть доказательство, что извлечение ничего не изменило.

- [ ] **Step 5: Commit**

```bash
git add johnny/router.py tests/test_router.py
git commit -m "refactor: route_exact выделена из route

Цепочкам нужно точное совпадение отдельно, причём в двух режимах:
со шаблонами и без. Поведение route() не меняется.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: браузер дожидается страницы и честно сообщает о провале

Сегодня `browser.click_result` — это `if(el) el.click()`: пустой список результатов означает «молча ничего не сделал», а `actions.execute` отвечает `ok=True`. Весь «стоп при провале» держится на сигнале об ошибке, поэтому без этой задачи связанные цепочки бессмысленны.

**Files:**
- Modify: `johnny/browser.py:123-139`
- Modify: `johnny/actions.py:168-184` и ветки `execute` для `browser_click_result` / `browser_next` / `browser_fullscreen`
- Test: `tests/test_browser.py`, `tests/test_actions.py`

**Interfaces:**
- Produces: `browser._wait_for(script: str, timeout: float = 8.0) -> bool`
- Produces: `browser.click_result(n: int, timeout: float = 8.0) -> bool`
- Produces: `browser.next_video(timeout: float = 8.0) -> bool`
- Produces: `browser.fullscreen(timeout: float = 8.0) -> bool`
- Produces: `actions.browser_click_result(n: str) -> bool`, `actions.browser_next() -> bool`, `actions.browser_fullscreen() -> bool`

- [ ] **Step 1: Write the failing tests**

Дописать в конец `tests/test_browser.py`:

```python
class FakeDriver:
    """Драйвер, который «готов» только начиная с ready_after-го опроса."""

    def __init__(self, ready_after: int = 0):
        self.ready_after = ready_after
        self.polls = 0
        self.clicked: list[str] = []

    def execute_script(self, script, *args):
        if "click()" in script:
            self.clicked.append(script)
            return None
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
```

Дописать в конец `tests/test_actions.py`:

```python
def test_browser_click_result_failure_is_not_ok(monkeypatch):
    import johnny.browser as browser

    monkeypatch.setattr(browser, "click_result", lambda n: False)
    result = actions.execute(RoutedAction("browser_click_result", "1"), {}, {})
    assert result.ok is False
    assert "видео" in result.message.lower()


def test_browser_click_result_success_is_ok(monkeypatch):
    import johnny.browser as browser

    monkeypatch.setattr(browser, "click_result", lambda n: True)
    assert actions.execute(RoutedAction("browser_click_result", "1"), {}, {}).ok is True


def test_browser_fullscreen_failure_is_not_ok(monkeypatch):
    import johnny.browser as browser

    monkeypatch.setattr(browser, "fullscreen", lambda: False)
    assert actions.execute(RoutedAction("browser_fullscreen", ""), {}, {}).ok is False
```

Проверить шапку `tests/test_actions.py`: нужны `actions` и `RoutedAction`. Если импортов нет — добавить в существующем стиле.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_browser.py tests/test_actions.py -q`
Expected: FAIL — `AttributeError: module 'johnny.browser' has no attribute '_wait_for'` и падения на `ok is False`.

- [ ] **Step 3: Implement browser.py**

Заменить `click_result`, `next_video`, `fullscreen` в `johnny/browser.py` на:

```python
# Верхняя граница ожидания, а не задержка: обычно результаты поиска
# отрисовываются за 0.3–1с и шаг идёт дальше сразу. Восемь секунд человек
# ждёт только когда что-то действительно сломалось.
_WAIT_TIMEOUT = 8.0
_WAIT_POLL = 0.25

_RESULTS = "ytd-video-renderer #video-title, a#video-title-link"


def _wait_for(script: str, timeout: float = _WAIT_TIMEOUT) -> bool:
    """Опрашивать страницу, пока скрипт не вернёт истину. False — не дождались.

    Не WebDriverWait: вся работа со страницей в проекте уже написана на JS,
    заводить второй способ обращения к ней ради одной функции незачем.
    """
    driver = get_driver()
    deadline = time.monotonic() + timeout
    while True:
        if driver.execute_script(script):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(_WAIT_POLL)


def _click_when_ready(selector: str, timeout: float) -> bool:
    """Дождаться элемента и нажать. False — элемент так и не появился."""
    if not _wait_for(f"return !!document.querySelector('{selector}');", timeout):
        return False
    get_driver().execute_script(f"document.querySelector('{selector}').click();")
    return True


def click_result(n: int, timeout: float = _WAIT_TIMEOUT) -> bool:
    """Кликнуть N-й (1-based) видео-результат поиска YouTube.

    Ждёт, пока результатов станет хотя бы n: в цепочке этот шаг идёт сразу
    за поиском, и страница ещё грузится. Раньше пустой список означал
    «молча ничего не сделал», и вызывающий считал это успехом.
    """
    n = int(n)  # подставляется в JS, поэтому число обязано быть числом
    if not _wait_for(f"return document.querySelectorAll('{_RESULTS}').length >= {n};", timeout):
        return False
    get_driver().execute_script(
        f"var l=document.querySelectorAll('{_RESULTS}'); l[{n} - 1].click();"
    )
    return True


def next_video(timeout: float = _WAIT_TIMEOUT) -> bool:
    return _click_when_ready(".ytp-next-button", timeout)


def fullscreen(timeout: float = _WAIT_TIMEOUT) -> bool:
    return _click_when_ready(".ytp-fullscreen-button", timeout)
```

`time` в `johnny/browser.py` уже импортирован (строка 5) — проверить и не дублировать.

- [ ] **Step 4: Implement actions.py**

Заменить три обёртки (`johnny/actions.py:168-184`):

```python
def browser_click_result(n: str) -> bool:
    from . import browser

    return browser.click_result(int(n))


def browser_next() -> bool:
    from . import browser

    return browser.next_video()


def browser_fullscreen() -> bool:
    from . import browser

    return browser.fullscreen()
```

И три ветки в `execute`:

```python
    if routed.action == "browser_click_result":
        if browser_click_result(routed.argument):
            return ActionResult(True, "Включаю")
        return ActionResult(False, "Не нашёл видео на странице")
    if routed.action == "browser_next":
        if browser_next():
            return ActionResult(True, "Дальше")
        return ActionResult(False, "Не нашёл кнопку следующего видео")
    if routed.action == "browser_fullscreen":
        if browser_fullscreen():
            return ActionResult(True, "Готово")
        return ActionResult(False, "Не нашёл плеер на странице")
```

- [ ] **Step 5: Run the whole suite**

Run: `.\.venv\Scripts\python.exe -m pytest -q`
Expected: PASS, 275 тестов.

- [ ] **Step 6: Commit**

```bash
git add johnny/browser.py johnny/actions.py tests/test_browser.py tests/test_actions.py
git commit -m "fix: браузер дожидается страницы и сообщает о провале

click_result/fullscreen/next_video были fire-and-forget: пустая страница
означала молчаливое бездействие при ok=True. Теперь ждут элемент до 8с и
возвращают bool. Чинит и одиночную команду «включи первое видео», которая
на незагруженной странице бодро отвечала «Roger that», не сделав ничего.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: `chain.resolve` и `chain.split_local`

Ядро итерации. Здесь же — единственное место, где решается, что считать цепочкой.

**Files:**
- Create: `johnny/chain.py`
- Test: `tests/test_chain.py` (создать)

**Interfaces:**
- Consumes: `router.route_exact` (Task 1), `router.is_unsafe_action`, `router.RoutedAction`
- Produces: `chain.MAX_STEPS = 5`
- Produces: `chain.resolve(phrases: list[str], commands) -> list[RoutedAction] | None`
- Produces: `chain.split_local(text: str, commands) -> list[RoutedAction] | None`

- [ ] **Step 1: Write the failing tests**

Создать `tests/test_chain.py`:

```python
import johnny.chain as chain
from johnny.config import CommandRule

COMMANDS = [
    CommandRule(pattern="запусти *", action="launch_app", template="{0}"),
    CommandRule(pattern="громкость *", action="set_volume", template="{0}"),
    CommandRule(pattern="открой твич", action="browser_open", template="twitch.tv"),
    CommandRule(pattern="найди на ютубе *", action="browser_open", template="yt?q={0}"),
    CommandRule(pattern="включи музыку", action="system", template="play_pause"),
    CommandRule(pattern="сверни все", action="system", template="minimize_all"),
    CommandRule(pattern="выключи компьютер", action="system", template="shutdown"),
    CommandRule(pattern="режим стрима", action="scenario", template="режим стрима"),
]


def test_split_two_commands():
    steps = chain.split_local("запусти обс и громкость 20", COMMANDS)
    assert [(s.action, s.argument) for s in steps] == [
        ("launch_app", "обс"),
        ("set_volume", "20"),
    ]


def test_split_rejected_when_part_is_not_a_command():
    """«рок и ролл» — это аргумент, а не цепочка: «ролл» ни с чем не совпадает."""
    assert chain.split_local("найди на ютубе рок и ролл", COMMANDS) is None


def test_single_part_is_not_a_chain():
    assert chain.split_local("открой твич", COMMANDS) is None


def test_split_rejects_more_than_max_steps():
    text = " и ".join(["открой твич"] * (chain.MAX_STEPS + 1))
    assert chain.split_local(text, COMMANDS) is None


def test_split_rejects_unsafe_step():
    assert chain.split_local("сверни всё и выключи компьютер", COMMANDS) is None


def test_split_separators():
    for separator in ("потом", "затем", "а потом", "а также", "плюс"):
        text = f"открой твич {separator} включи музыку"
        assert chain.split_local(text, COMMANDS) is not None, separator


def test_and_inside_word_does_not_split():
    """«и» режет только как отдельное слово: «игра», «или», «история» целы."""
    commands = COMMANDS + [
        CommandRule(pattern="открой историю", action="open_url", template="history")
    ]
    assert chain.split_local("открой историю", commands) is None


def test_resolve_rejects_nested_scenario():
    assert chain.resolve(["режим стрима", "открой твич"], COMMANDS) is None


def test_resolve_single_phrase_is_allowed():
    """Сценарий из одного шага — законен; ограничение «≥2» только у сплиттера."""
    assert len(chain.resolve(["открой твич"], COMMANDS)) == 1


def test_resolve_empty_is_none():
    assert chain.resolve([], COMMANDS) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_chain.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'johnny.chain'`

- [ ] **Step 3: Implement**

Создать `johnny/chain.py`:

```python
"""Цепочки команд: одна фраза — несколько действий.

Три источника списка шагов (сплит по союзам, сценарий из scenarios.yaml,
модель) сходятся в одну функцию `resolve`. Она прогоняет каждую фразу через
`route_exact`, поэтому ни один источник не может изобрести действие или
собрать кривой аргумент — ровно тот же приём, что уже применён к
«исправленной» команде в brain.py.
"""

from dataclasses import dataclass

from .router import RoutedAction, is_unsafe_action, route_exact

# Потолок на любую цепочку. Больше пяти шагов одной фразой человек не
# произносит — такой список означает, что сплит или модель ошиблись.
MAX_STEPS = 5

# Длинные разделители идут первыми: «а потом» должен съесть оба слова, а не
# распасться на «а» + «потом».
_SEPARATORS = ("а также", "а потом", "потом", "затем", "плюс", "и")


def _split_phrases(text: str) -> list[str]:
    """Разрезать фразу по союзам. Режем ТОЛЬКО по отдельным словам —
    иначе «игра», «или», «история» распались бы на куски."""
    words = text.lower().replace(",", " ").split()
    parts: list[str] = []
    current: list[str] = []
    i = 0
    while i < len(words):
        matched = 0
        for separator in _SEPARATORS:
            sep_words = separator.split()
            if words[i : i + len(sep_words)] == sep_words:
                matched = len(sep_words)
                break
        if matched:
            parts.append(" ".join(current))
            current = []
            i += matched
            continue
        current.append(words[i])
        i += 1
    parts.append(" ".join(current))
    return [part for part in parts if part]


def resolve(phrases: list[str], commands) -> list[RoutedAction] | None:
    """Список фраз → список действий. None, если цепочка не собирается.

    Достаточно одной неразобранной фразы, чтобы отвергнуть цепочку целиком:
    «сделать половину» хуже, чем не сделать ничего, — человек не услышит,
    где именно его не поняли.
    """
    if not phrases or len(phrases) > MAX_STEPS:
        return None
    steps = []
    for phrase in phrases:
        routed = route_exact(phrase, commands)
        if routed is None:
            return None
        if is_unsafe_action(routed.action, routed.argument):
            # Выключение/перезагрузку/сон можно произнести только целой
            # фразой с точным совпадением — в цепочке их нет никогда.
            return None
        if routed.action == "scenario":
            return None  # вложенные сценарии запрещены, рекурсии нет
        steps.append(routed)
    return steps


def split_local(text: str, commands) -> list[RoutedAction] | None:
    """Разрезать фразу по союзам и принять разбиение, только если КАЖДЫЙ
    кусок точно совпал с известной командой. Иначе None — и фраза пойдёт
    дальше по обычному пути целой."""
    phrases = _split_phrases(text)
    if len(phrases) < 2:
        return None
    return resolve(phrases, commands)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_chain.py -q`
Expected: PASS, 10 тестов.

- [ ] **Step 5: Commit**

```bash
git add johnny/chain.py tests/test_chain.py
git commit -m "feat: сплиттер цепочек и сборка шагов

Разбиение принимается, только если каждый кусок точно совпал с командой.
Иначе фраза идёт дальше целой — «найди на ютубе рок и ролл» не распадётся.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: `chain.run` — выполнение со стопом при провале

**Files:**
- Modify: `johnny/chain.py`
- Test: `tests/test_chain.py`

**Interfaces:**
- Consumes: `actions.execute(routed, apps, channels) -> ActionResult` (существует)
- Produces: `chain.ChainResult(done: int, total: int, failure: str | None)`
- Produces: `chain.run(steps: list[RoutedAction], config, speaker) -> ChainResult`

`chain.run` возвращает свой маленький результат, а не `Outcome` из `app.py`: `app` импортирует `chain`, значит обратный импорт замкнул бы кольцо.

- [ ] **Step 1: Write the failing tests**

Дописать в `tests/test_chain.py`:

```python
from dataclasses import dataclass, field

import johnny.actions as actions
from johnny.actions import ActionResult
from johnny.router import RoutedAction


@dataclass
class FakeSpeaker:
    said: list = field(default_factory=list)
    answers: int = 0

    def say(self, text):
        self.said.append(text)

    def play_answer(self):
        self.answers += 1
        return True


@dataclass
class FakeConfig:
    apps: dict = field(default_factory=dict)
    channels: dict = field(default_factory=dict)


def _steps(n):
    return [RoutedAction("system", f"step{i}") for i in range(n)]


def test_run_all_steps_one_sound(monkeypatch):
    done = []
    monkeypatch.setattr(
        actions, "execute", lambda routed, apps, channels: done.append(routed) or ActionResult(True, "Готово")
    )
    speaker = FakeSpeaker()
    result = chain.run(_steps(3), FakeConfig(), speaker)
    assert len(done) == 3
    assert result.done == 3 and result.failure is None
    assert speaker.answers == 1, "звук успеха должен прозвучать ровно один раз"
    assert speaker.said == []


def test_run_stops_on_first_failure(monkeypatch):
    done = []

    def fake_execute(routed, apps, channels):
        done.append(routed)
        if len(done) == 2:
            return ActionResult(False, "Не нашёл видео на странице")
        return ActionResult(True, "Готово")

    monkeypatch.setattr(actions, "execute", fake_execute)
    speaker = FakeSpeaker()
    result = chain.run(_steps(3), FakeConfig(), speaker)
    assert len(done) == 2, "третий шаг выполняться не должен"
    assert result.done == 1 and result.total == 3
    assert result.failure == "Не нашёл видео на странице"
    assert speaker.answers == 0
    assert "1 из 3" in speaker.said[0]
    assert "Не нашёл видео" in speaker.said[0]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_chain.py -q -k run_`
Expected: FAIL — `AttributeError: module 'johnny.chain' has no attribute 'run'`

- [ ] **Step 3: Implement**

В `johnny/chain.py` заменить строку импорта и дописать в конец:

```python
from . import actions
```

(именно `from . import actions`, а не `from .actions import execute` — иначе `monkeypatch` в тестах не подменит связанное имя)

```python
@dataclass
class ChainResult:
    done: int
    total: int
    failure: str | None = None


def run(steps: list[RoutedAction], config, speaker) -> ChainResult:
    """Выполнить шаги по очереди. Первый провал останавливает остаток.

    Звук успеха играется один раз в конце: три «Roger that» подряд за две
    секунды — это шум, а не подтверждение.
    """
    total = len(steps)
    for index, step in enumerate(steps):
        result = actions.execute(step, config.apps, config.channels)
        if not result.ok:
            # Шаги чаще всего связаны: разворачивать на весь экран, когда
            # видео не открылось, — значит развернуть чужую вкладку.
            speaker.say(f"Сделал {index} из {total}, не смог: {result.message}")
            return ChainResult(done=index, total=total, failure=result.message)
    if not speaker.play_answer():
        speaker.say("Готово")
    return ChainResult(done=total, total=total)
```

- [ ] **Step 4: Run the whole suite**

Run: `.\.venv\Scripts\python.exe -m pytest -q`
Expected: PASS, 287 тестов.

- [ ] **Step 5: Commit**

```bash
git add johnny/chain.py tests/test_chain.py
git commit -m "feat: выполнение цепочки со стопом при первом провале

Звук успеха один в конце; при провале — «Сделал N из M, не смог: ...».

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: сценарии из `scenarios.yaml`

Сценарий не выполняется через `actions.execute` — это не действие над системой, а список фраз. `load_config` превращает каждый ключ в обычный `CommandRule(action="scenario")`, и сценарию даром достаётся вся лестница совпадений: «режим стрима» поймается точно, «режым стрима» — нечётко.

**Files:**
- Modify: `johnny/config.py:27-35` (поле `scenarios`) и `johnny/config.py:42-75` (чтение)
- Create: `config/scenarios.yaml`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `Config.scenarios: dict[str, list[str]]`
- Produces: правила `CommandRule(pattern=<фраза>, action="scenario", template=<фраза>)` в `Config.commands`

- [ ] **Step 1: Write the failing tests**

Дописать в конец `tests/test_config.py`:

```python
def test_scenarios_loaded_as_commands(tmp_path):
    (tmp_path / "apps.yaml").write_text("обс: obs.exe", encoding="utf-8")
    (tmp_path / "commands.yaml").write_text(
        '"запусти *":\n  action: launch_app\n  template: "{0}"\n', encoding="utf-8"
    )
    (tmp_path / "settings.yaml").write_text("wake_word: джони", encoding="utf-8")
    (tmp_path / "scenarios.yaml").write_text(
        "режим стрима:\n  - запусти обс\n  - запусти твич\n", encoding="utf-8"
    )
    config = load_config(tmp_path)
    assert config.scenarios["режим стрима"] == ["запусти обс", "запусти твич"]
    rules = [rule for rule in config.commands if rule.action == "scenario"]
    assert len(rules) == 1
    assert rules[0].pattern == "режим стрима"
    assert rules[0].template == "режим стрима"


def test_missing_scenarios_file_is_fine(tmp_path):
    (tmp_path / "apps.yaml").write_text("обс: obs.exe", encoding="utf-8")
    (tmp_path / "commands.yaml").write_text(
        '"пауза":\n  action: system\n  template: play_pause\n', encoding="utf-8"
    )
    (tmp_path / "settings.yaml").write_text("wake_word: джони", encoding="utf-8")
    config = load_config(tmp_path)
    assert config.scenarios == {}


def test_shipped_scenarios_all_resolve():
    """Каждый шаг реального scenarios.yaml обязан совпасть с реальной командой.

    Опечатка в шаге иначе всплыла бы только голосом: Джони промолчал бы,
    и понять почему было бы нечем.
    """
    import johnny.chain as chain

    config = load_config("config")
    for name, steps in config.scenarios.items():
        assert chain.resolve(steps, config.commands) is not None, f"сценарий «{name}» не собрался"
```

Проверить, что `load_config` импортирована в шапке `tests/test_config.py`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_config.py -q -k scenario`
Expected: FAIL — `AttributeError: 'Config' object has no attribute 'scenarios'`

- [ ] **Step 3: Implement config.py**

В `@dataclass class Config` добавить поле после `channels`:

```python
    # Сценарии: фраза → список обычных команд. Файла может не быть.
    scenarios: dict = field(default_factory=dict)
```

В `load_config`, после блока `channels`, добавить:

```python
    scenarios_path = config_dir / "scenarios.yaml"
    scenarios_raw = _read_yaml(scenarios_path) if scenarios_path.exists() else {}
    scenarios = {
        str(phrase).lower(): [str(step) for step in steps]
        for phrase, steps in scenarios_raw.items()
    }
    # Сценарий — обычное правило роутера, поэтому ему бесплатно достаётся
    # вся лестница совпадений, включая нечёткую.
    commands += [
        CommandRule(pattern=phrase, action="scenario", template=phrase) for phrase in scenarios
    ]
```

И передать в конструктор:

```python
    return Config(
        apps=apps,
        commands=commands,
        settings=settings,
        channels=channels,
        secrets=secrets,
        scenarios=scenarios,
    )
```

- [ ] **Step 4: Create config/scenarios.yaml**

**Важно:** каждый шаг обязан точно совпадать с командой из `config/commands.yaml`. Перед записью проверить реальные формулировки:

```bash
grep -nE '^"(запусти обс|открой твич|громкость \*)"' config/commands.yaml
```

Если какая-то фраза в `commands.yaml` звучит иначе — взять оттуда, а не из этого плана. Файл:

```yaml
# Сценарий — одна фраза, за которой стоит несколько обычных команд.
# Каждый шаг должен ТОЧНО совпадать с командой из commands.yaml:
# нечёткое сравнение внутри сценария не работает намеренно.
# Максимум 5 шагов. Опасные команды (выключение/перезагрузка/сон) запрещены.
режим стрима:
  - запусти обс
  - открой твич
  - громкость 20
```

- [ ] **Step 5: Run the whole suite**

Run: `.\.venv\Scripts\python.exe -m pytest -q`
Expected: PASS, 290 тестов. Если `test_shipped_scenarios_all_resolve` падает — в `scenarios.yaml` опечатка в шаге, исправить по `commands.yaml`.

- [ ] **Step 6: Commit**

```bash
git add johnny/config.py config/scenarios.yaml tests/test_config.py
git commit -m "feat: сценарии из scenarios.yaml

Ключ становится обычным CommandRule(action=scenario), поэтому сценарию
даром достаётся вся лестница совпадений. Тест проверяет, что каждый шаг
реального файла совпадает с реальной командой.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: модель возвращает `{"steps": [...]}`

**Files:**
- Modify: `johnny/brain.py:9-27` (промпт), `johnny/brain.py:40-46` (`BrainResult`), `johnny/brain.py:83-107` (разбор)
- Test: `tests/test_brain.py`

**Interfaces:**
- Consumes: `chain.resolve` (Task 3)
- Produces: `BrainResult.steps: list[RoutedAction] | None = None`

- [ ] **Step 1: Write the failing tests**

Дописать в конец `tests/test_brain.py`:

```python
CHAIN_COMMANDS = [
    CommandRule(pattern="открой твич", action="browser_open", template="twitch.tv"),
    CommandRule(pattern="громкость *", action="set_volume", template="{0}"),
    CommandRule(pattern="выключи компьютер", action="system", template="shutdown"),
]


def _provider(raw):
    return [("тест", lambda prompt: raw)]


def test_steps_are_routed():
    result = interpret(
        "открой твич и громкость 20",
        CHAIN_COMMANDS,
        _provider('{"steps": ["открой твич", "громкость 20"]}'),
    )
    assert [(s.action, s.argument) for s in result.steps] == [
        ("browser_open", "twitch.tv"),
        ("set_volume", "20"),
    ]


def test_unknown_step_rejects_whole_chain():
    result = interpret(
        "что-то",
        CHAIN_COMMANDS,
        _provider('{"steps": ["открой твич", "поговори со мной"]}'),
    )
    assert result.steps is None


def test_unsafe_step_rejects_whole_chain():
    result = interpret(
        "что-то",
        CHAIN_COMMANDS,
        _provider('{"steps": ["открой твич", "выключи компьютер"]}'),
    )
    assert result.steps is None


def test_too_many_steps_rejected():
    steps = ", ".join(['"открой твич"'] * 6)
    result = interpret("что-то", CHAIN_COMMANDS, _provider('{"steps": [' + steps + "]}"))
    assert result.steps is None
```

Проверить шапку `tests/test_brain.py`: нужны `interpret` и `CommandRule`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_brain.py -q -k step`
Expected: FAIL — `AttributeError: 'BrainResult' object has no attribute 'steps'`

- [ ] **Step 3: Implement**

В `johnny/brain.py` добавить импорт:

```python
from . import brain_claude, brain_groq, chain
```

В `_PROMPT` вставить новый случай между пунктами 1 и 2, а остальные перенумеровать (было 1/2/3, станет 1/2/3/4):

```python
2) Если это НЕСКОЛЬКО команд из списка в одной фразе — ответь ТОЛЬКО JSON со
списком точных фраз из списка, по одной на действие (максимум 5):
{{"steps": ["найди на ютубе кино", "включи первое видео"]}}
```

Старый пункт 2 («команда, которой в списке нет») становится 3, старый 3 («вопрос») — 4.

В `BrainResult` добавить поле:

```python
    # Цепочка шагов, если модель разложила фразу на несколько команд.
    steps: list[RoutedAction] | None = None
```

Поле идёт ПОСЛЕ `provider`, у которого есть значение по умолчанию, — иначе dataclass не соберётся.

В `interpret`, сразу после блока `corrected = data.get("command")`, добавить:

```python
        steps = data.get("steps")
        if steps:
            # Модель предлагает только ФРАЗЫ: список собирает chain.resolve
            # через route_exact, поэтому изобрести действие или протащить
            # опасную команду она не может.
            return BrainResult(
                routed=None,
                reply=None,
                provider=name,
                steps=chain.resolve([str(step) for step in steps], commands),
            )
```

- [ ] **Step 4: Run the whole suite**

Run: `.\.venv\Scripts\python.exe -m pytest -q`
Expected: PASS, 294 теста.

- [ ] **Step 5: Commit**

```bash
git add johnny/brain.py tests/test_brain.py
git commit -m "feat: модель раскладывает фразу на шаги

{\"steps\": [...]} — только фразы, список собирает chain.resolve через
route_exact. Нераспознанный или опасный шаг отвергает цепочку целиком.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 7: новый порядок разбора в `handle_command`

Финальная сборка. Здесь же живёт главный регрессионный тест итерации.

**Files:**
- Modify: `johnny/app.py:37-84`
- Test: `tests/test_app.py`

**Interfaces:**
- Consumes: всё из Task 1–6

Порядок:

```
1. route_exact(literal_only=True)  → фиксированная фраза целиком
2. chain.split_local               → цепочка
3. route()                         → шаблоны + нечёткое
4. brain.interpret                 → шаги или одиночное
```

Ступень 2 обязана стоять **впереди** ступени 3: в `commands.yaml` есть жадный шаблон `"запусти *"`, и «запусти обс и громкость 20» совпадает с ним точно, с аргументом `"обс и громкость 20"`.

- [ ] **Step 1: Write the failing tests**

Дописать в конец `tests/test_app.py`:

**Внимание на подмену `execute`.** В файле уже есть тесты, подменяющие `app.execute` — это СВЯЗАННОЕ имя внутри `johnny/app.py` (там `from .actions import execute`). А `chain.run` вызывает `actions.execute` через модуль. Это два разных пути, и подменять надо тот, по которому пойдёт конкретный тест: цепочки — `johnny.actions.execute`, одиночное действие — `app.execute`.

Существующие `_config()` (без аргументов) и `SpySpeaker` в файле уже есть — их не трогать. Добавить рядом свою фабрику:

```python
def _config_chain(commands, scenarios=None):
    return Config(
        apps={},
        commands=commands,
        settings=Settings("джони", "", "off", "medium", "cuda"),
        scenarios=scenarios or {},
    )
```

Сами тесты:

```python
def test_greedy_template_does_not_swallow_a_chain(monkeypatch):
    """Главный регрессионный тест итерации.

    «запусти *» точно совпадает со всей фразой «запусти обс и громкость 20»,
    поэтому сплиттер обязан стоять впереди шаблонов. Иначе Джони будет
    искать программу с именем «обс и громкость 20».
    """
    import johnny.actions as actions
    from johnny.actions import ActionResult

    done = []
    monkeypatch.setattr(
        actions,
        "execute",
        lambda routed, apps, channels: done.append((routed.action, routed.argument))
        or ActionResult(True, "Готово"),
    )
    config = _config_chain(
        [
            CommandRule("запусти *", "launch_app", "{0}"),
            CommandRule("громкость *", "set_volume", "{0}"),
        ]
    )
    speaker = SpySpeaker(answer_played=True)
    outcome = handle_command("запусти обс и громкость 20", config, speaker)
    assert done == [("launch_app", "обс"), ("set_volume", "20")]
    assert outcome.via == "цепочка(2)/локально"
    assert speaker.answer_calls == 1, "звук успеха должен прозвучать один раз на всю цепочку"


def test_scenario_expands_into_chain(monkeypatch):
    import johnny.actions as actions
    from johnny.actions import ActionResult

    done = []
    monkeypatch.setattr(
        actions,
        "execute",
        lambda routed, apps, channels: done.append(routed.action) or ActionResult(True, "Готово"),
    )
    config = _config_chain(
        [
            CommandRule("открой твич", "browser_open", "twitch.tv"),
            CommandRule("громкость *", "set_volume", "{0}"),
            CommandRule("режим стрима", "scenario", "режим стрима"),
        ],
        scenarios={"режим стрима": ["открой твич", "громкость 20"]},
    )
    outcome = handle_command("режим стрима", config, SpySpeaker(answer_played=True))
    assert done == ["browser_open", "set_volume"]
    assert outcome.via == "цепочка(2)/сценарий"


def test_fixed_phrase_with_and_is_not_split(monkeypatch):
    """Фиксированная фраза, внутри которой есть «и», остаётся одной командой."""
    import johnny.app as app
    from johnny.actions import ActionResult

    done = []
    monkeypatch.setattr(
        app,
        "execute",
        lambda routed, apps, channels: done.append(routed.argument)
        or ActionResult(True, "Готово"),
    )
    config = _config_chain(
        [
            CommandRule("сверни все и покажи стол", "system", "show_desktop"),
            CommandRule("сверни все", "system", "minimize_all"),
            CommandRule("покажи стол", "system", "show_desktop"),
        ]
    )
    handle_command("сверни всё и покажи стол", config, SpySpeaker(answer_played=True))
    assert done == ["show_desktop"], "фраза распалась на две команды"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_app.py -q -k "chain or scenario or fixed_phrase"`
Expected: FAIL — цепочка не собирается, `done == [("launch_app", "обс и громкость 20")]`.

- [ ] **Step 3: Implement**

В `johnny/app.py` заменить импорты:

```python
from . import chain
from .actions import execute
from .brain import interpret, make_providers
from .config import load_config
from .router import route, route_exact
from .speaker import make_speaker
```

Добавить перед `handle_command`:

```python
def _dispatch(routed, config, speaker) -> Outcome | None:
    """Выполнить действие. Сценарий разворачивается в цепочку.

    None означает «сценарий не собрался» (шаг из scenarios.yaml не совпал ни
    с одной командой) — фраза идёт дальше по лестнице разбора.
    """
    if routed.action == "scenario":
        steps = chain.resolve(config.scenarios.get(routed.argument, []), config.commands)
        if steps is None:
            logger.warning("Сценарий %r не собрался: шаг не совпал с командой", routed.argument)
            return None
        result = chain.run(steps, config, speaker)
        return Outcome(f"цепочка({result.total})/сценарий")
    _respond(speaker, execute(routed, config.apps, config.channels))
    return Outcome(routed.via)
```

Заменить тело `try` в `handle_command` (строки 60-79) на:

```python
    try:
        # 1. Фиксированная фраза целиком. Стоит первой, чтобы команду, внутри
        # которой есть «и», не разрезал сплиттер: такая фраза заведомо одна.
        routed = route_exact(text, config.commands, literal_only=True)
        if routed is not None:
            outcome = _dispatch(routed, config, speaker)
            if outcome is not None:
                return outcome
        # 2. Цепочка. ОБЯЗАНА идти впереди жадных шаблонов: «запусти *»
        # совпадает со всей фразой «запусти обс и громкость 20» точно.
        steps = chain.split_local(text, config.commands)
        if steps is not None:
            result = chain.run(steps, config, speaker)
            return Outcome(f"цепочка({result.total})/локально")
        # 3. Обычная лестница: шаблоны и нечёткое сравнение.
        routed = route(text, config.commands)
        if routed is not None:
            outcome = _dispatch(routed, config, speaker)
            if outcome is not None:
                return outcome
        if not use_brain:
            return not_understood("Не понял команду", "мимо")
        # 4. Модель.
        answer = interpret(text, config.commands, make_providers(config))
        if answer is None:
            return not_understood("Не понял команду", "модель недоступна")
        if answer.steps:
            result = chain.run(answer.steps, config, speaker)
            return Outcome(f"цепочка({result.total})/{answer.provider or 'модель'}")
        if answer.routed is not None:
            _respond(speaker, execute(answer.routed, config.apps, config.channels), answer.reply)
            return Outcome(answer.provider or "модель")
        if answer.reply:
            speaker.say(answer.reply)
            return Outcome(answer.provider or "модель")
        # routed=None без reply — модель не поняла/не смогла исправить
        # ослышку. Раньше тут говорилось бодрое «Готово», хотя ничего
        # не произошло.
        return not_understood("Не понял команду", answer.provider or "модель")
    except Exception:
        logger.exception("Ошибка при выполнении команды")
        # Озвучиваем даже в тихом режиме: команду поняли, значит ждут ответа.
        speaker.say("Не смог выполнить команду")
        return Outcome("ошибка")
```

- [ ] **Step 4: Run the whole suite**

Run: `.\.venv\Scripts\python.exe -m pytest -q`
Expected: PASS, 297 тестов.

- [ ] **Step 5: Commit**

```bash
git add johnny/app.py tests/test_app.py
git commit -m "feat: цепочки подключены к разбору команд

Порядок: фиксированная фраза -> цепочка -> шаблоны и нечёткое -> модель.
Сплиттер стоит впереди шаблонов, иначе жадный «запусти *» съедает всю
фразу «запусти обс и громкость 20» целиком.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Живая проверка (делает пользователь)

Автотесты реальный YouTube не покрывают. После перезапуска Джони (Выход в трее → ярлык «Джони») сказать три фразы:

1. **Независимая:** «Джони, открой твич и сделай громкость 20» — оба действия, один звук в конце.
2. **Связанная браузерная:** «Джони, найди на ютубе Exile Show и включи первое видео» — второй шаг дожидается загрузки страницы.
3. **Сценарий:** «Джони, режим стрима» — три действия подряд.

Дальше посмотреть `history.log` через трей: у строк должна стоять пометка вида `цепочка(2)/локально` или `цепочка(2)/groq`.

Если шаг молчит — смотреть `johnny.log`: сценарий, не собравшийся из-за опечатки в шаге, пишет туда предупреждение с именем сценария.
