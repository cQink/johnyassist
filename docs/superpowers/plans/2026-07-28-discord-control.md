# Управление Discord голосом — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Джони звонит и пишет в личные сообщения десктопного Discord по голосовой команде — «позвони Гоше», «напиши Гоше привет».

**Architecture:** Управление идёт через дерево доступности (UI Automation) настоящего десктопного клиента, а не через браузер: голос физически живёт в одном клиенте, и им должен остаться тот, где сидит человек. Слой UIA (`discord_ui.py`) не знает про команды и алиасы, слой алиасов (`contacts.py`) не знает про UIA — первый проверяется на живом Discord, второй на моках.

**Tech Stack:** Python 3.14, `uiautomation` 2.0.29, `pywin32`, `ctypes.SendInput`, pytest.

## Global Constraints

- Только Windows. Проект и так Windows-only (`win32gui`, `pycaw`, `os.startfile`).
- Виртуальное окружение — `.venv`. Все команды: `.venv/Scripts/python.exe -m pytest ...`
- Комментарии, сообщения Джони и тексты коммитов — по-русски, как во всём проекте.
- **Нажимать только паттерном Invoke, никогда мышью.** `uiautomation` умеет `.Click()`, но он двигает настоящий курсор и выдернет мышь из-под руки.
- **Живые проверки — только с явного разрешения человека.** Это его условие с начала работы. Относится ко всему, что нажимает кнопки в Discord, а не только к звонкам.
- **Первая живая отправка сообщения — в переписку с самим собой**, не живому человеку.
- Discord должен быть развёрнут: у свёрнутого окна дерево доступности пустое (замерено: 0 элементов против 253).

---

### Task 1: Вынести морфологию в общий модуль

`_stem`/`_stem_phrase` лежат приватными в `actions.py` и нужны теперь и каналам, и людям. Импортировать приватное имя из чужого модуля нельзя — выносим.

**Files:**
- Create: `johnny/morph.py`
- Create: `tests/test_morph.py`
- Modify: `johnny/actions.py:105-118` (удалить `_VOWELS`, `_stem`, `_stem_phrase`), `johnny/actions.py:121-130` (`_resolve_channel` зовёт новый модуль)

**Interfaces:**
- Consumes: ничего
- Produces: `morph.stem(word: str) -> str`, `morph.stem_phrase(text: str) -> str`

- [ ] **Step 1: Написать падающий тест**

Создать `tests/test_morph.py`:

```python
from johnny import morph


def test_stem_отрезает_хвостовую_гласную():
    assert morph.stem("серёги") == "серег"
    assert morph.stem("гоше") == "гош"


def test_stem_не_трогает_короткие_слова():
    assert morph.stem("оба") == "оба"


def test_stem_не_трогает_согласную_на_конце():
    assert morph.stem("георгий") == "георгий"


def test_stem_phrase_по_словам():
    assert morph.stem_phrase("Дядя Вася") == "дяд вас"
```

- [ ] **Step 2: Убедиться, что тест падает**

Run: `.venv/Scripts/python.exe -m pytest tests/test_morph.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'johnny.morph'`

- [ ] **Step 3: Создать модуль**

Создать `johnny/morph.py`:

```python
"""Грубая морфология для сравнения произнесённых имён с записанными.

Whisper выдаёт имя в том падеже, в котором его сказали («Серёги», «Гоше»),
а в конфиге записана начальная форма. Полноценный стеммер тут избыточен:
достаточно отрезать одну хвостовую гласную, чтобы формы сошлись.
"""

_VOWELS = "аеиоуыэюя"


def stem(word: str) -> str:
    """Отрезать одну хвостовую гласную: «серёги»→«серег», «серёга»→«серег»."""
    word = word.replace("ё", "е")
    if len(word) > 3 and word[-1] in _VOWELS:
        return word[:-1]
    return word


def stem_phrase(text: str) -> str:
    return " ".join(stem(w) for w in text.lower().split())
```

- [ ] **Step 4: Убедиться, что тест проходит**

Run: `.venv/Scripts/python.exe -m pytest tests/test_morph.py -v`
Expected: PASS, 4 passed

- [ ] **Step 5: Перевести actions.py на новый модуль**

В `johnny/actions.py` удалить блок с `_VOWELS`, `_stem`, `_stem_phrase` (строки 105–118), оставив только константу каналов:

```python
_CHANNEL_HOSTS = {"twitch": "twitch.tv", "youtube": "youtube.com"}
```

Заменить тело `_resolve_channel` на использование общего модуля:

```python
def _resolve_channel(alias: str, platform: str, channels: dict):
    """Найти слаг канала по произнесённому имени (в любом падеже). None — не нашли."""
    from .morph import stem_phrase

    key = stem_phrase(alias)
    for info in channels.values():
        slug = info.get(platform)
        if not slug:
            continue
        if any(stem_phrase(str(a)) == key for a in info.get("aliases", [])):
            return slug
    return None
```

- [ ] **Step 6: Убедиться, что каналы не сломались**

Run: `.venv/Scripts/python.exe -m pytest tests/ -q`
Expected: PASS — все прежние тесты зелёные, включая тесты каналов в `tests/test_actions.py`

- [ ] **Step 7: Коммит**

```bash
git add johnny/morph.py johnny/actions.py tests/test_morph.py
git commit -m "refactor: морфология имён вынесена в общий модуль"
```

---

### Task 2: Справочник людей

Чистая логика: как произнесённое имя превращается в человека и как из фразы «гоше привет как дела» отделяется адресат. Ни UI Automation, ни Discord тут нет — всё проверяется обычным pytest.

**Files:**
- Create: `johnny/contacts.py`
- Create: `tests/test_contacts.py`

**Interfaces:**
- Consumes: `morph.stem_phrase(text) -> str` (Task 1)
- Produces:
  - `contacts.resolve(said: str, people: dict) -> dict | None`
  - `contacts.split_message(argument: str, people: dict) -> tuple[dict, str] | None`
  - `contacts.matches_dm(element_name: str, person: dict) -> bool`
  - `contacts.matches_input(element_name: str, person: dict) -> bool`

  `person` — это словарь из `people.yaml` вида `{"discord": "Гречка", "username": "ne_godjaj", "aliases": [...]}`.

- [ ] **Step 1: Написать падающие тесты**

Создать `tests/test_contacts.py`:

```python
from johnny import contacts

PEOPLE = {
    "гоша": {
        "discord": "Гречка",
        "username": "ne_godjaj",
        "aliases": ["гоша", "гречка", "георгий", "георгию", "гандон"],
    },
    "ярик": {
        "discord": "Грущенко",
        "username": "r1ealy",
        "aliases": ["ярик", "грущенко", "грущик"],
    },
    "дядя вася": {
        "discord": "VasyaTheGreat",
        "username": "vasya",
        "aliases": ["дядя вася"],
    },
}


def test_resolve_по_алиасу():
    assert contacts.resolve("гоша", PEOPLE)["discord"] == "Гречка"
    assert contacts.resolve("ярик", PEOPLE)["discord"] == "Грущенко"


def test_resolve_терпит_падежи():
    # «гоше» и «гоша» сходятся после отрезания хвостовой гласной
    assert contacts.resolve("гоше", PEOPLE)["discord"] == "Гречка"
    assert contacts.resolve("грущику", PEOPLE)["discord"] == "Грущенко"


def test_resolve_незнакомого_возвращает_none():
    assert contacts.resolve("петя", PEOPLE) is None


def test_split_message_отделяет_имя_от_текста():
    person, text = contacts.split_message("гоше привет как дела", PEOPLE)
    assert person["discord"] == "Гречка"
    assert text == "привет как дела"


def test_split_message_берёт_длиннейшее_имя():
    # «дядя» само по себе не алиас — имя должно съесть два слова, а не одно
    person, text = contacts.split_message("дядя вася ты где", PEOPLE)
    assert person["discord"] == "VasyaTheGreat"
    assert text == "ты где"


def test_split_message_без_текста_возвращает_none():
    assert contacts.split_message("гоше", PEOPLE) is None


def test_split_message_с_чужим_именем_возвращает_none():
    assert contacts.split_message("пете привет", PEOPLE) is None


def test_matches_dm_узнаёт_ссылку_со_статусом():
    person = PEOPLE["гоша"]
    assert contacts.matches_dm("не прочитано, Гречка (личное сообщение)", person)
    assert contacts.matches_dm("Гречка (личное сообщение)", person)


def test_matches_dm_отвергает_чужую_ссылку_и_не_лс():
    person = PEOPLE["гоша"]
    assert not contacts.matches_dm("Грущенко (личное сообщение)", person)
    assert not contacts.matches_dm("Гречка", person)


def test_matches_input_сверяет_адресата():
    person = PEOPLE["гоша"]
    assert contacts.matches_input("Написать @Гречка", person)
    assert not contacts.matches_input("Написать @Грущенко", person)
    assert not contacts.matches_input("Гречка", person)
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `.venv/Scripts/python.exe -m pytest tests/test_contacts.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'johnny.contacts'`

- [ ] **Step 3: Написать модуль**

Создать `johnny/contacts.py`:

```python
"""Кто такой «Гоша»: произнесённое имя → человек из people.yaml.

Знает только про имена и строки. Ничего не знает ни про Discord, ни про
дерево доступности — поэтому проверяется обычными тестами без живого клиента.
"""

from .morph import stem_phrase

_DM_MARK = "(личное сообщение)"
_INPUT_MARK = "Написать"


def resolve(said: str, people: dict) -> dict | None:
    """Человек по произнесённому имени в любом падеже. None — не знаем такого."""
    key = stem_phrase(said)
    for name, info in people.items():
        aliases = [str(a) for a in info.get("aliases", [])] or [str(name)]
        if any(stem_phrase(alias) == key for alias in aliases):
            return info
    return None


def split_message(argument: str, people: dict):
    """«гоше привет как дела» → (человек, «привет как дела»).

    Имя отделяется по ДЛИННЕЙШЕМУ совпавшему префиксу, а не по первому слову:
    иначе двусловное имя («дядя вася») распалось бы, и остаток фразы уехал бы
    в текст сообщения. None — имени не узнали или текста не осталось.
    """
    words = argument.split()
    for size in range(len(words) - 1, 0, -1):
        person = resolve(" ".join(words[:size]), people)
        if person is not None:
            return person, " ".join(words[size:])
    return None


def matches_dm(element_name: str, person: dict) -> bool:
    """Ссылка «не прочитано, Гречка (личное сообщение)» — про этого человека?

    Сравниваем вхождением, а не равенством: Discord дописывает рядом с именем
    статус («не прочитано», «Не беспокоить»), и приписка меняется сама собой.
    """
    if _DM_MARK not in element_name:
        return False
    return person["discord"].lower() in element_name.lower()


def matches_input(element_name: str, person: dict) -> bool:
    """Поле «Написать @Гречка» — точно того самого человека?

    Это последняя проверка перед вводом текста: поле само называет адресата,
    и сверка с ним — единственное, что физически мешает сообщению уйти не в
    тот чат.
    """
    if not element_name.startswith(_INPUT_MARK):
        return False
    return person["discord"].lower() in element_name.lower()
```

- [ ] **Step 4: Убедиться, что тесты проходят**

Run: `.venv/Scripts/python.exe -m pytest tests/test_contacts.py -v`
Expected: PASS, 10 passed

- [ ] **Step 5: Коммит**

```bash
git add johnny/contacts.py tests/test_contacts.py
git commit -m "feat: справочник людей и разбор фразы на адресата и текст"
```

---

### Task 3: `people.yaml` и загрузка конфига

**Files:**
- Create: `config/people.yaml`
- Modify: `johnny/config.py:27-37` (поле `people` в `Config`), `johnny/config.py:69-71` (чтение файла), `johnny/config.py:87-94` (передача в `Config`)
- Modify: `tests/test_config.py` (добавить тест)

**Interfaces:**
- Consumes: ничего
- Produces: `Config.people` — `dict`, пустой, если файла нет

- [ ] **Step 1: Написать падающий тест**

Дописать в `tests/test_config.py`:

```python
def test_загружает_людей(tmp_path):
    (tmp_path / "apps.yaml").write_text("обс: obs.exe\n", encoding="utf-8")
    (tmp_path / "commands.yaml").write_text(
        '"пауза":\n  action: system\n  template: "play_pause"\n', encoding="utf-8"
    )
    (tmp_path / "settings.yaml").write_text("wake_word: джони\n", encoding="utf-8")
    (tmp_path / "people.yaml").write_text(
        'гоша:\n  discord: "Гречка"\n  username: "ne_godjaj"\n  aliases: ["гоша"]\n',
        encoding="utf-8",
    )

    config = load_config(tmp_path)

    assert config.people["гоша"]["discord"] == "Гречка"


def test_без_файла_людей_словарь_пустой(tmp_path):
    (tmp_path / "apps.yaml").write_text("обс: obs.exe\n", encoding="utf-8")
    (tmp_path / "commands.yaml").write_text(
        '"пауза":\n  action: system\n  template: "play_pause"\n', encoding="utf-8"
    )
    (tmp_path / "settings.yaml").write_text("wake_word: джони\n", encoding="utf-8")

    assert load_config(tmp_path).people == {}
```

Если в `tests/test_config.py` ещё нет импорта `load_config`, добавить сверху: `from johnny.config import load_config`.

- [ ] **Step 2: Убедиться, что тест падает**

Run: `.venv/Scripts/python.exe -m pytest tests/test_config.py -v -k люд`
Expected: FAIL — `AttributeError: 'Config' object has no attribute 'people'`

- [ ] **Step 3: Добавить поле в Config**

В `johnny/config.py`, в датакласс `Config`, после `channels`:

```python
    # Люди для Discord: алиас → отображаемое имя и юзернейм. Файла может не быть.
    people: dict = field(default_factory=dict)
```

- [ ] **Step 4: Читать файл**

В `johnny/config.py`, рядом с чтением `channels.yaml`:

```python
    people_path = config_dir / "people.yaml"
    people = _read_yaml(people_path) if people_path.exists() else {}
```

И передать в конструктор `Config(...)`, добавив аргумент `people=people,`.

- [ ] **Step 5: Убедиться, что тесты проходят**

Run: `.venv/Scripts/python.exe -m pytest tests/test_config.py -v`
Expected: PASS — включая два новых теста

- [ ] **Step 6: Создать конфиг с настоящими людьми**

Создать `config/people.yaml`:

```yaml
# Люди для команд «позвони *» и «напиши *».
#
# discord   — ОТОБРАЖАЕМОЕ имя, как оно видно в списке личных сообщений.
#             Именно оно лежит в дереве доступности; сравнение идёт вхождением,
#             потому что Discord дописывает рядом статус («не прочитано»).
# username  — юзернейм. Нужен запасным путём: если человека нет в списке
#             недавних переписок, искать его придётся через «Найти или начать
#             беседу», а там ищут по юзернейму.
# aliases   — как человека зовут вслух. Падежи с хвостовой гласной («гоше»,
#             «грущику») отрабатывает morph.stem_phrase, поэтому их
#             перечислять не нужно. А вот «георгий»/«георгию» — нужно:
#             «й» не гласная, и сами по себе эти формы не сойдутся.

гоша:
  discord: "Гречка"
  username: "ne_godjaj"
  aliases: ["гоша", "гречка", "георгий", "георгию", "гандон"]

ярик:
  discord: "Грущенко"
  username: "r1ealy"
  aliases: ["ярик", "грущенко", "грущик"]

# Запасной аккаунт хозяина: за ним никто не сидит. На нём делаются живые
# проверки отправки — писать самому себе Discord не позволяет.
твикс:
  discord: "Твикс 1"
  username: "dddasdasd6081"
  aliases: ["твикс", "тест"]
```

- [ ] **Step 7: Проверить, что настоящий конфиг читается**

Run: `.venv/Scripts/python.exe -c "from johnny.config import load_config; from johnny import contacts; p=load_config('config').people; print(contacts.resolve('гоше', p)); print(contacts.resolve('ярику', p))"`
Expected: два словаря — с `'discord': 'Гречка'` и `'discord': 'Грущенко'`

- [ ] **Step 8: Коммит**

```bash
git add config/people.yaml johnny/config.py tests/test_config.py
git commit -m "feat: справочник людей в people.yaml"
```

---

### Task 4: Слой дерева доступности

Тонкая обёртка над UI Automation. Осознанно почти без логики: всё, что можно проверить тестами, уже лежит в `contacts.py`. Здесь — только то, что проверяется на живом Discord.

**Files:**
- Create: `johnny/discord_ui.py`
- Modify: `requirements.txt`

**Interfaces:**
- Consumes: ничего
- Produces:
  - `discord_ui.window() -> int | None` — hwnd окна `discord.exe`
  - `discord_ui.ensure_visible(hwnd) -> bool` — развернуть; вернуть, было ли свёрнуто
  - `discord_ui.restore_state(hwnd, was_minimized: bool) -> None`
  - `discord_ui.find(hwnd, kind: str, predicate) -> object | None` — `predicate` принимает имя элемента и возвращает bool; `kind` — строка вида `"ButtonControl"`, `"HyperlinkControl"`, `"EditControl"`
  - `discord_ui.invoke(control) -> bool`

- [ ] **Step 1: Записать зависимость**

Дописать в `requirements.txt`:

```
uiautomation==2.0.29
```

- [ ] **Step 2: Написать модуль**

Создать `johnny/discord_ui.py`:

```python
"""Чтение и нажатие элементов десктопного Discord через дерево доступности.

Три вещи, выясненные пробником на живом клиенте, и все три обязательны:

1. Окно ищется по ПРОЦЕССУ, а не по заголовку. Слово «Discord» бывает в
   заголовке чужих окон (терминал, браузер) — первая версия пробника нашла
   именно терминал.
2. Доступность в Chromium включается ЛЕНИВО: пока никто не спросил, дерева
   нет. Первый обход вернул 0 элементов, второй — 253. Поэтому спрашиваем
   дважды.
3. У СВЁРНУТОГО окна дерева нет вообще (ровно 0 элементов): свёрнутое окно
   Chromium считает невидимым. Окно обязано быть развёрнуто.
"""

import logging
import time

import uiautomation as auto
import win32api
import win32con
import win32gui
import win32process

logger = logging.getLogger(__name__)

# Пауза между «разбудили» и «читаем»: см. пункт 2 в шапке модуля.
_WAKE_PAUSE = 1.2
# Глубина обхода. Дерево Discord — около 30 уровней; замер полного обхода 1.4 с.
_DEPTH = 30
# Пауза после разворачивания окна: дереву нужно появиться.
_SHOW_PAUSE = 0.6


def _process_name(hwnd) -> str:
    _tid, pid = win32process.GetWindowThreadProcessId(hwnd)
    try:
        handle = win32api.OpenProcess(
            win32con.PROCESS_QUERY_LIMITED_INFORMATION, False, pid
        )
    except Exception:
        return ""
    try:
        return win32process.GetModuleFileNameEx(handle, 0).rsplit("\\", 1)[-1].lower()
    except Exception:
        return ""
    finally:
        win32api.CloseHandle(handle)


def window():
    """hwnd видимого окна Discord. None, если клиент не запущен."""
    found = []

    def _cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd) and win32gui.GetWindowText(hwnd):
            if _process_name(hwnd) == "discord.exe":
                found.append(hwnd)

    win32gui.EnumWindows(_cb, None)
    return found[0] if found else None


def ensure_visible(hwnd) -> bool:
    """Развернуть окно, если оно свёрнуто. Возвращает, БЫЛО ли оно свёрнуто."""
    was_minimized = bool(win32gui.IsIconic(hwnd))
    if was_minimized:
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        time.sleep(_SHOW_PAUSE)
    return was_minimized


def restore_state(hwnd, was_minimized: bool) -> None:
    """Вернуть окно как было: свёрнутое — свернуть обратно."""
    if was_minimized:
        win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)


def _walk(node, depth: int, out: list) -> None:
    if depth > _DEPTH:
        return
    try:
        children = node.GetChildren()
    except Exception:
        return
    for child in children:
        out.append(child)
        _walk(child, depth + 1, out)


def _elements(hwnd) -> list:
    """Все элементы дерева. Пустой список — дерево недоступно."""
    for attempt in (1, 2):
        root = auto.ControlFromHandle(hwnd)
        out: list = []
        if root is not None:
            _walk(root, 0, out)
        if out:
            return out
        # Первый заход мог лишь разбудить доступность — даём ей появиться.
        logger.debug("Дерево Discord пустое (попытка %d)", attempt)
        time.sleep(_WAKE_PAUSE)
    return []


def find(hwnd, kind: str, predicate):
    """Первый элемент нужного типа, чьё имя устраивает predicate. None — нет."""
    for element in _elements(hwnd):
        try:
            if element.ControlTypeName != kind:
                continue
            name = (element.Name or "").strip()
        except Exception:
            continue
        if name and predicate(name):
            return element
    return None


def invoke(control) -> bool:
    """Нажать элемент программно.

    Мышью не нажимаем принципиально: .Click() двигает настоящий курсор и
    выдернет мышь из-под руки. Сначала InvokePattern, затем действие по
    умолчанию — не все элементы Chromium отдают Invoke.
    """
    for attempt in (
        lambda: control.GetInvokePattern().Invoke(),
        lambda: control.GetLegacyIAccessiblePattern().DoDefaultAction(),
    ):
        try:
            attempt()
            return True
        except Exception:
            continue
    return False
```

- [ ] **Step 3: Проверить, что модуль импортируется и находит окно**

Это чтение, а не нажатие — разрешения не требует.

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -c "from johnny import discord_ui; h=discord_ui.window(); print('hwnd:', h); print('кнопка звонка:', discord_ui.find(h, 'ButtonControl', lambda n: n == 'Начать голосовой звонок') is not None)"`

Expected: `hwnd: <число>` и `кнопка звонка: True`, если Discord запущен и открыт на любой личной переписке.

Если `hwnd: None` — Discord не запущен, запустить и повторить. Если кнопка `False` — открыт не диалог, а сервер; переключиться на любую личную переписку.

- [ ] **Step 4: Коммит**

```bash
git add johnny/discord_ui.py requirements.txt
git commit -m "feat: чтение дерева доступности Discord"
```

---

### Task 5: Печать текста

**Files:**
- Create: `johnny/keyboard.py`
- Create: `tests/test_keyboard.py`

**Interfaces:**
- Consumes: ничего
- Produces: `keyboard.type_text(text: str) -> None`, `keyboard.press_enter() -> None`

- [ ] **Step 1: Написать падающий тест**

Проверяем разбор на нажатия, а не саму отправку в Windows: `_send` подменяется.

Создать `tests/test_keyboard.py`:

```python
from johnny import keyboard


def test_каждый_символ_даёт_нажатие_и_отпускание(monkeypatch):
    sent = []
    monkeypatch.setattr(keyboard, "_send", lambda scan, flags: sent.append((scan, flags)))

    keyboard.type_text("ок")

    assert [scan for scan, _flags in sent] == [ord("о"), ord("о"), ord("к"), ord("к")]
    down, up = sent[0][1], sent[1][1]
    assert down == keyboard.KEYEVENTF_UNICODE
    assert up == keyboard.KEYEVENTF_UNICODE | keyboard.KEYEVENTF_KEYUP


def test_пустой_текст_ничего_не_печатает(monkeypatch):
    sent = []
    monkeypatch.setattr(keyboard, "_send", lambda scan, flags: sent.append((scan, flags)))

    keyboard.type_text("")

    assert sent == []
```

- [ ] **Step 2: Убедиться, что тест падает**

Run: `.venv/Scripts/python.exe -m pytest tests/test_keyboard.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'johnny.keyboard'`

- [ ] **Step 3: Написать модуль**

> **ИСПРАВЛЕНО ПОСЛЕ РЕВЬЮ (коммит 76b4bd6).** Код ниже содержал критический дефект: объединение внутри `_INPUT` объявлено только с полем `ki`, из-за чего `ctypes.sizeof(_INPUT)` равен 32 байтам вместо настоящих 40 на x64. Этот размер уходит третьим аргументом `SendInput`, а при неверном размере функция проваливается всегда — замерено: возврат 0, `GetLastError()==87`. Не печатался ни один символ, и тесты этого не видели, потому что подменяют `_send` целиком. В объединение нужно добавить `MOUSEINPUT` и `HARDWAREINPUT`, а возврат `SendInput` — проверять. Смотри актуальный `johnny/keyboard.py`, а не код ниже.

Создать `johnny/keyboard.py`:

```python
"""Печать текста в активное поле через SendInput.

Юникодом, а не виртуальными кодами клавиш: раскладка клавиатуры в момент
команды неизвестна, а KEYEVENTF_UNICODE от неё не зависит — русский текст
напечатается и на английской раскладке.
"""

import ctypes
from ctypes import wintypes

KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_KEYUP = 0x0002
_INPUT_KEYBOARD = 1
_VK_RETURN = 0x0D


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class _INPUT(ctypes.Structure):
    class _UNION(ctypes.Union):
        _fields_ = [("ki", _KEYBDINPUT)]

    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _UNION)]


def _send(scan: int, flags: int) -> None:
    event = _INPUT()
    event.type = _INPUT_KEYBOARD
    event.ki = _KEYBDINPUT(0, scan, flags, 0, None)
    ctypes.windll.user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(event))


def type_text(text: str) -> None:
    for char in text:
        code = ord(char)
        _send(code, KEYEVENTF_UNICODE)
        _send(code, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP)


def press_enter() -> None:
    ctypes.windll.user32.keybd_event(_VK_RETURN, 0, 0, 0)
    ctypes.windll.user32.keybd_event(_VK_RETURN, 0, KEYEVENTF_KEYUP, 0)
```

- [ ] **Step 4: Убедиться, что тесты проходят**

Run: `.venv/Scripts/python.exe -m pytest tests/test_keyboard.py -v`
Expected: PASS, 2 passed

- [ ] **Step 5: Коммит**

```bash
git add johnny/keyboard.py tests/test_keyboard.py
git commit -m "feat: печать текста в активное поле"
```

---

### Task 6: Отправка сообщения

Сообщения идут раньше звонков сознательно: ошибку в сообщении можно удалить, ошибочный звонок — нет.

**Files:**
- Modify: `johnny/actions.py` (добавить `_open_dm`, `discord_message`, ветку в `execute`, параметр `people`)
- Modify: `johnny/app.py:61`, `johnny/app.py:123` (передать `people`)
- Modify: `johnny/chain.py:157` (передать `people`)
- Modify: `config/commands.yaml` (новые команды)
- Create: `tests/test_discord_actions.py`

**Interfaces:**
- Consumes: `contacts.split_message`, `contacts.matches_dm`, `contacts.matches_input` (Task 2); `discord_ui.window/ensure_visible/restore_state/find/invoke` (Task 4); `keyboard.type_text/press_enter` (Task 5)
- Produces: `actions.discord_message(argument: str, people: dict) -> ActionResult`, `actions._open_dm(hwnd, person) -> bool`, `execute(..., people: dict | None = None)`

- [ ] **Step 1: Написать падающие тесты**

Создать `tests/test_discord_actions.py`:

```python
import pytest

from johnny import actions, discord_ui, keyboard
from johnny.router import RoutedAction

PEOPLE = {
    "гоша": {"discord": "Гречка", "username": "ne_godjaj", "aliases": ["гоша"]},
    "ярик": {"discord": "Грущенко", "username": "r1ealy", "aliases": ["ярик"]},
}


class FakeElement:
    def __init__(self, name):
        self.Name = name
        self.focused = False

    def SetFocus(self):
        self.focused = True


@pytest.fixture
def discord(monkeypatch):
    """Поддельный Discord: дерево из пар (тип, имя) + журнал действий."""

    state = {
        "tree": [
            ("HyperlinkControl", "не прочитано, Гречка (личное сообщение)"),
            ("HyperlinkControl", "Грущенко (личное сообщение)"),
            ("EditControl", "Написать @Гречка"),
            ("ButtonControl", "Начать голосовой звонок"),
        ],
        "typed": [],
        "invoked": [],
        "minimized": False,
        "restored": [],
    }

    def fake_find(hwnd, kind, predicate):
        for element_kind, name in state["tree"]:
            if element_kind == kind and predicate(name):
                return FakeElement(name)
        return None

    monkeypatch.setattr(discord_ui, "window", lambda: 42)
    monkeypatch.setattr(discord_ui, "ensure_visible", lambda hwnd: state["minimized"])
    monkeypatch.setattr(
        discord_ui, "restore_state", lambda hwnd, was: state["restored"].append(was)
    )
    monkeypatch.setattr(discord_ui, "find", fake_find)
    monkeypatch.setattr(
        discord_ui, "invoke", lambda control: state["invoked"].append(control.Name) or True
    )
    monkeypatch.setattr(keyboard, "type_text", lambda text: state["typed"].append(text))
    monkeypatch.setattr(keyboard, "press_enter", lambda: state["typed"].append("<enter>"))
    monkeypatch.setattr(actions, "_DISCORD_OPEN_DELAY", 0)
    return state


def test_сообщение_печатается_и_отправляется(discord):
    result = actions.discord_message("гоше привет как дела", PEOPLE)

    assert result.ok
    assert discord["typed"] == ["привет как дела", "<enter>"]
    assert "не прочитано, Гречка (личное сообщение)" in discord["invoked"]


def test_незнакомому_человеку_не_пишем(discord):
    result = actions.discord_message("пете привет", PEOPLE)

    assert not result.ok
    assert discord["typed"] == []


def test_человека_нет_в_списке_переписок(discord):
    discord["tree"] = [("EditControl", "Написать @Гречка")]

    result = actions.discord_message("гоше привет", PEOPLE)

    assert not result.ok
    assert discord["typed"] == []


def test_чужое_поле_ввода_останавливает_отправку(discord):
    # Открылась переписка не с тем человеком: поле называет другого адресата
    discord["tree"] = [
        ("HyperlinkControl", "Гречка (личное сообщение)"),
        ("EditControl", "Написать @Грущенко"),
    ]

    result = actions.discord_message("гоше привет", PEOPLE)

    assert not result.ok
    assert discord["typed"] == []


def test_свёрнутое_окно_сворачивается_обратно(discord):
    discord["minimized"] = True

    actions.discord_message("гоше привет", PEOPLE)

    assert discord["restored"] == [True]


def test_execute_доводит_людей_до_действия(discord):
    routed = RoutedAction(action="discord_message", argument="гоше привет")

    result = actions.execute(routed, apps={}, people=PEOPLE)

    assert result.ok
    assert discord["typed"] == ["привет", "<enter>"]
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `.venv/Scripts/python.exe -m pytest tests/test_discord_actions.py -v`
Expected: FAIL — `AttributeError: module 'johnny.actions' has no attribute 'discord_message'`

- [ ] **Step 3: Добавить действие в actions.py**

В `johnny/actions.py`, рядом с остальными константами задержек вверху файла:

```python
# Сколько ждать открытия переписки после нажатия на неё в списке.
_DISCORD_OPEN_DELAY = 1.0
```

И новые функции (перед `def execute(`):

```python
def _open_dm(hwnd, person: dict) -> bool:
    """Открыть личную переписку с человеком, нажав её в боковом списке."""
    from . import contacts, discord_ui

    link = discord_ui.find(
        hwnd, "HyperlinkControl", lambda name: contacts.matches_dm(name, person)
    )
    if link is None:
        return False
    if not discord_ui.invoke(link):
        return False
    time.sleep(_DISCORD_OPEN_DELAY)
    return True


def discord_message(argument: str, people: dict) -> ActionResult:
    """«гоше привет как дела» — открыть переписку и отправить сообщение."""
    from . import contacts, discord_ui, keyboard

    split = contacts.split_message(argument, people or {})
    if split is None:
        return ActionResult(False, "Не понял, кому писать")
    person, text = split
    hwnd = discord_ui.window()
    if hwnd is None:
        return ActionResult(False, "Discord не запущен")
    was_minimized = discord_ui.ensure_visible(hwnd)
    try:
        if not _open_dm(hwnd, person):
            return ActionResult(False, f"Не нашёл {person['discord']} в переписках")
        # Поле ввода само называет адресата («Написать @Гречка») — сверяем с
        # тем, кого назвали. Единственное, что физически мешает сообщению
        # уйти не в тот чат, если переписка открылась не та.
        field = discord_ui.find(
            hwnd, "EditControl", lambda name: contacts.matches_input(name, person)
        )
        if field is None:
            return ActionResult(False, "Не нашёл поле ввода нужной переписки")
        field.SetFocus()
        keyboard.type_text(text)
        keyboard.press_enter()
        return ActionResult(True, f"Отправил {person['discord']}: {text}")
    finally:
        discord_ui.restore_state(hwnd, was_minimized)
```

- [ ] **Step 4: Провести people через execute**

В `johnny/actions.py` изменить сигнатуру `execute`:

```python
def execute(
    routed: RoutedAction,
    apps: dict[str, str],
    channels: dict | None = None,
    new_tab: bool = True,
    people: dict | None = None,
) -> ActionResult:
```

И добавить ветку рядом с остальными (перед финальным `return`):

```python
    if routed.action == "discord_message":
        return discord_message(routed.argument, people or {})
```

- [ ] **Step 5: Передать people во всех вызовах execute**

`johnny/app.py:61`:

```python
    _respond(
        speaker,
        execute(routed, config.apps, config.channels, new_tab=new_tab, people=config.people),
    )
```

`johnny/app.py:123`:

```python
                execute(
                    answer.routed,
                    config.apps,
                    config.channels,
                    new_tab=new_tab,
                    people=config.people,
                ),
```

`johnny/chain.py:157`:

```python
        result = actions.execute(
            step, config.apps, config.channels, new_tab=new_tab, people=config.people
        )
```

- [ ] **Step 6: Добавить команды**

Дописать в конец `config/commands.yaml`:

```yaml
# --- Discord: сообщения ---
# Порядок важен: «напиши сообщение *» обязано стоять ПЕРЕД «напиши *»,
# иначе жадный шаблон заберёт фразу себе и слово «сообщение» уедет в имя.
"напиши сообщение *":
  action: discord_message
  template: "{0}"
"отправь сообщение *":
  action: discord_message
  template: "{0}"
"напиши *":
  action: discord_message
  template: "{0}"
"отправь *":
  action: discord_message
  template: "{0}"
```

- [ ] **Step 7: Убедиться, что всё зелёное**

Run: `.venv/Scripts/python.exe -m pytest tests/ -q`
Expected: PASS — новые 6 тестов плюс все прежние

- [ ] **Step 8: Проверить разбор команды без выполнения**

Run: `.venv/Scripts/python.exe -c "from johnny.config import load_config; from johnny.router import route; c=load_config('config'); r=route('напиши гоше привет как дела', c.commands); print(r.action, '|', r.argument)"`
Expected: `discord_message | гоше привет как дела`

- [ ] **Step 9: Коммит**

```bash
git add johnny/actions.py johnny/app.py johnny/chain.py config/commands.yaml tests/test_discord_actions.py
git commit -m "feat: отправка сообщений в Discord голосом"
```

---

### Task 7: Звонок

**Files:**
- Modify: `johnny/actions.py` (добавить `discord_call`, ветку в `execute`)
- Modify: `johnny/router.py:31-39` (`is_unsafe_action`)
- Modify: `config/commands.yaml`
- Modify: `tests/test_discord_actions.py`, `tests/test_router.py`

**Interfaces:**
- Consumes: `contacts.resolve` (Task 2), `_open_dm` (Task 6), `discord_ui.*` (Task 4)
- Produces: `actions.discord_call(argument: str, people: dict) -> ActionResult`

- [ ] **Step 1: Написать падающие тесты**

Дописать в `tests/test_discord_actions.py`:

```python
def test_звонок_нажимает_кнопку(discord):
    result = actions.discord_call("гоше", PEOPLE)

    assert result.ok
    assert "Начать голосовой звонок" in discord["invoked"]


def test_звонок_незнакомому_не_проходит(discord):
    result = actions.discord_call("пете", PEOPLE)

    assert not result.ok
    assert discord["invoked"] == []


def test_звонок_не_сворачивает_окно_обратно(discord):
    # Идёт разговор — окно должно остаться на экране
    discord["minimized"] = True

    actions.discord_call("гоше", PEOPLE)

    assert discord["restored"] == []
```

Дописать в `tests/test_router.py`:

```python
def test_звонок_не_угадывается():
    from johnny.config import CommandRule
    from johnny.router import route

    commands = [CommandRule(pattern="позвони *", action="discord_call", template="{0}")]

    # Точно — работает
    assert route("позвони гоше", commands).action == "discord_call"
    # Похоже — НЕ работает: звонок живому человеку угадывать нельзя
    assert route("покажи гошу", commands) is None
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `.venv/Scripts/python.exe -m pytest tests/test_discord_actions.py tests/test_router.py -v -k "звон"`
Expected: FAIL — `AttributeError: module 'johnny.actions' has no attribute 'discord_call'`

- [ ] **Step 3: Добавить действие**

В `johnny/actions.py`, после `discord_message`:

```python
def discord_call(argument: str, people: dict) -> ActionResult:
    """«гоше» — открыть переписку и начать голосовой звонок.

    Окно НЕ возвращается в свёрнутое состояние, в отличие от сообщения:
    идёт разговор, его надо видеть.
    """
    from . import contacts, discord_ui

    person = contacts.resolve(argument, people or {})
    if person is None:
        return ActionResult(False, "Не знаю такого человека")
    hwnd = discord_ui.window()
    if hwnd is None:
        return ActionResult(False, "Discord не запущен")
    discord_ui.ensure_visible(hwnd)
    if not _open_dm(hwnd, person):
        return ActionResult(False, f"Не нашёл {person['discord']} в переписках")
    button = discord_ui.find(
        hwnd, "ButtonControl", lambda name: name == "Начать голосовой звонок"
    )
    if button is None:
        return ActionResult(False, "Не нашёл кнопку звонка")
    if not discord_ui.invoke(button):
        return ActionResult(False, "Не смог нажать кнопку звонка")
    return ActionResult(True, f"Звоню {person['discord']}")
```

И ветку в `execute`, рядом с `discord_message`:

```python
    if routed.action == "discord_call":
        return discord_call(routed.argument, people or {})
```

- [ ] **Step 4: Запретить угадывание звонка**

В `johnny/router.py` заменить `is_unsafe_action`:

```python
def is_unsafe_action(action: str, argument: str) -> bool:
    """Действие, которое нельзя угадывать: только точное совпадение.

    Единственное определение «разрушительности» в проекте: им пользуется как
    сам роутер (см. `_is_unsafe`), так и brain — модель не должна уметь
    протащить такое действие в обход этой проверки ни через «исправленную»
    команду, ни через свободный action.

    Звонок сюда попадает не потому, что ломает компьютер, а потому, что
    необратим по-человечески: у сообщения ошибку исправляет удаление, а
    случайный звонок живому человеку отозвать нельзя. «Позвони Гоше» не
    должно вылетать из «покажи Гошу» по похожести.
    """
    if action == "discord_call":
        return True
    return action == "system" and argument in _UNSAFE
```

- [ ] **Step 5: Добавить команды**

Дописать в `config/commands.yaml`:

```yaml
# --- Discord: звонки ---
# Только точное совпадение (см. is_unsafe_action): звонок живому человеку
# угадывать нельзя, поэтому вариантов фразы больше, чем обычно.
"позвони *":
  action: discord_call
  template: "{0}"
"позвони ка *":
  action: discord_call
  template: "{0}"
"набери *":
  action: discord_call
  template: "{0}"
"свяжись с *":
  action: discord_call
  template: "{0}"
```

- [ ] **Step 6: Убедиться, что всё зелёное**

Run: `.venv/Scripts/python.exe -m pytest tests/ -q`
Expected: PASS — все тесты, включая прежние тесты роутера про `shutdown`

- [ ] **Step 7: Проверить разбор без выполнения**

Run: `.venv/Scripts/python.exe -c "from johnny.config import load_config; from johnny.router import route; c=load_config('config'); print(route('позвони гоше', c.commands)); print(route('покажи гошу', c.commands))"`
Expected: первая строка — `RoutedAction(action='discord_call', argument='гоше')`, вторая — `None`

- [ ] **Step 8: Коммит**

```bash
git add johnny/actions.py johnny/router.py config/commands.yaml tests/test_discord_actions.py tests/test_router.py
git commit -m "feat: звонок в Discord голосом"
```

---

### Task 8: Живая проверка

Кода не пишется. Здесь закрываются четыре неизвестности из спеки — те, что пробник не проверял, потому что ничего не нажимал.

**СТОП-УСЛОВИЕ: не начинать без явного разрешения человека на каждый шаг.** Это его условие с самого начала работы.

**Files:** правки по итогам, если что-то не сработало

- [ ] **Step 1: Спросить разрешение и подготовить сцену**

Попросить человека: открыть Discord на любой личной переписке и сказать, что можно начинать.

Все живые проверки отправки идут в **«Твикс 1»** — запасной аккаунт хозяина, за которым никто не сидит. Писать самому себе Discord не позволяет, поэтому цель проверки именно такая. Живому человеку в этой задаче не пишем и не звоним до отдельного разрешения на последнем шаге.

- [ ] **Step 2: Проверить Invoke на ссылке переписки**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -c "from johnny import discord_ui, contacts; from johnny.config import load_config; p=load_config('config').people; h=discord_ui.window(); person=contacts.resolve('гоша',p); link=discord_ui.find(h,'HyperlinkControl',lambda n: contacts.matches_dm(n,person)); print('нашёл:', link.Name if link else None); print('нажалось:', discord_ui.invoke(link) if link else False)"`

Expected: `нашёл: ... Гречка (личное сообщение)` и `нажалось: True`, в Discord открылась переписка.

Если `нажалось: False` — Invoke не поддерживается; запасной путь: координатный клик по `element.BoundingRectangle`. Дописать его в `discord_ui.invoke` третьей попыткой.

- [ ] **Step 3: Проверить отправку сообщения на запасной аккаунт**

Цель — «Твикс 1», он уже прописан в `config/people.yaml` под алиасом `твикс`.

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -c "from johnny import actions; from johnny.config import load_config; print(actions.discord_message('твиксу проверка связи', load_config('config').people))"`

Expected: `ActionResult(ok=True, ...)` и сообщение «проверка связи» в переписке с собой.

Если текст не напечатался — `SetFocus` не поставил курсор в поле; запасной путь: после `SetFocus` нажать пробел и Backspace, либо кликнуть по `BoundingRectangle` поля.

- [ ] **Step 4: Проверить работу из свёрнутого состояния**

Свернуть Discord, повторить команду из шага 3.

Expected: окно развернулось, сообщение ушло, окно свернулось обратно.

- [ ] **Step 5: Проверить, что дерево не засыпает**

Повторить команду из шага 3 дважды подряд, не трогая Discord.

Expected: оба раза `ok=True`. Если второй раз медленнее первого — доступность засыпает, и `_WAKE_PAUSE` работает как задумано; менять ничего не надо.

- [ ] **Step 6: Проверить, что оба человека вообще есть в списке недавних**

Боковой список показывает только недавние переписки — если человека там нет, ни сообщение, ни звонок его не найдут.

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -c "from johnny import discord_ui, contacts; from johnny.config import load_config; p=load_config('config').people; h=discord_ui.window(); [print(alias, '->', bool(discord_ui.find(h,'HyperlinkControl',lambda n: contacts.matches_dm(n, person)))) for alias, person in p.items()]"`

Expected: `гоша -> True`, `ярик -> True`, `твикс -> True`.

Если кто-то `False` — переписка уехала из недавних. Это **не** чинится в рамках этого плана: запасной путь через кнопку «Найти или начать беседу» (`Ctrl+K`) с поиском по `username` — отдельная задача. Пока достаточно записать, кого не нашли, и сказать человеку.

- [ ] **Step 7: Спросить отдельное разрешение на звонок и проверить**

Только после явного «да»: сказать Джони «позвони Гоше» вживую и сразу сбросить.

Expected: открылась переписка, пошёл звонок.

- [ ] **Step 8: Закоммитить правки**

Записи `твикс` в `config/people.yaml` — постоянная, убирать её не надо: она пригодится для всех будущих живых проверок.

```bash
git add -A
git commit -m "fix: правки по итогам живой проверки Discord"
```

---

### Task 9: Уверенность распознавания в истории

Последний кусок из спеки. Порог сейчас **не вводится** — сначала копим данные. Whisper отдаёт `avg_logprob` по каждому сегменту, а `transcribe` их выбрасывает.

**Files:**
- Modify: `johnny/recognizer.py:146-168`
- Modify: `johnny/controller.py:95-128`
- Create: `tests/test_recognizer_confidence.py`

**Interfaces:**
- Consumes: ничего
- Produces: `Recognizer.last_confidence: float` — минимальный `avg_logprob` последней расшифровки; `0.0`, если сегментов не было

- [ ] **Step 1: Написать падающий тест**

Создать `tests/test_recognizer_confidence.py`:

```python
from johnny.recognizer import Recognizer


class FakeSegment:
    def __init__(self, text, avg_logprob):
        self.text = text
        self.avg_logprob = avg_logprob


class FakeModel:
    def __init__(self, segments):
        self.segments = segments

    def transcribe(self, audio, **kwargs):
        return iter(self.segments), None


def _recognizer(segments):
    recognizer = Recognizer.__new__(Recognizer)   # без загрузки настоящей модели
    recognizer._model = FakeModel(segments)
    recognizer._prompt = "Джони. Русские голосовые команды."
    recognizer.last_confidence = 0.0
    return recognizer


def test_запоминает_худшую_уверенность():
    recognizer = _recognizer([FakeSegment("привет", -0.2), FakeSegment("гоше", -0.9)])

    recognizer.transcribe(None)

    assert recognizer.last_confidence == -0.9


def test_без_сегментов_уверенность_нулевая():
    recognizer = _recognizer([])

    assert recognizer.transcribe(None) == ""
    assert recognizer.last_confidence == 0.0
```

- [ ] **Step 2: Убедиться, что тест падает**

Run: `.venv/Scripts/python.exe -m pytest tests/test_recognizer_confidence.py -v`
Expected: FAIL — `AttributeError: 'Recognizer' object has no attribute 'last_confidence'` либо сравнение не сходится

- [ ] **Step 3: Запоминать уверенность**

В `johnny/recognizer.py`, в `__init__` добавить последней строкой:

```python
        # Худшая уверенность последней расшифровки. Пока только пишется в
        # историю: порог отсечения выбирается по накопленным данным, а не
        # угадывается заранее.
        self.last_confidence = 0.0
```

В `transcribe` материализовать сегменты (генератор нельзя пройти дважды) и запомнить минимум:

```python
        segments = list(segments)
        self.last_confidence = min(
            (seg.avg_logprob for seg in segments), default=0.0
        )
        text = " ".join(seg.text.strip() for seg in segments).strip()
```

- [ ] **Step 4: Убедиться, что тесты проходят**

Run: `.venv/Scripts/python.exe -m pytest tests/test_recognizer_confidence.py tests/test_recognizer_vocab.py tests/test_recognizer_echo.py -v`
Expected: PASS

- [ ] **Step 5: Писать уверенность в историю**

В `johnny/controller.py`, в `_joined_turn`, дописать уверенность в `mark` **один раз** — обе строки `history.add(text, f"{mark}/...")` ниже подхватят её сами, править их не нужно.

Вставить сразу после блока восстановления имени, перед `self.last_command = text`:

```python
        mark = f"{mark}[{recognizer.last_confidence:.2f}]"
```

В `_summoned_turn` заменить запись в историю:

```python
        finally:
            # Строку истории пишем всегда: даже если обработка упала, факт
            # распознавания должен остаться — на нём настраиваются пороги.
            history.add(text, f"{outcome.via}[{recognizer.last_confidence:.2f}]")
```

- [ ] **Step 6: Убедиться, что всё зелёное**

Run: `.venv/Scripts/python.exe -m pytest tests/ -q`
Expected: PASS — включая `tests/test_controller.py`

- [ ] **Step 7: Коммит**

```bash
git add johnny/recognizer.py johnny/controller.py tests/test_recognizer_confidence.py
git commit -m "feat: уверенность распознавания пишется в историю"
```

---

## Что этот план НЕ делает

- Не трогает мьют, «оглохнуть» и демонстрацию экрана — они остаются на горячих клавишах Discord.
- Не вводит команду «Ввод \*». Фундамент под неё (`johnny/keyboard.py`) появляется в Task 5, сама команда — отдельная задача.
- Не работает с серверными голосовыми каналами — только личные сообщения.
- Не читает входящие и не отвечает на входящие звонки.
- Не вводит порог отсечения по уверенности — Task 9 только копит данные для его выбора.
