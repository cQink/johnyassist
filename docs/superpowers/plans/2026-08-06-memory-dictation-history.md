# Память, диктовка и живая история — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Добавить алиас «напечатай» для ввода текста, починить баг с латинским мусором «vd» вместо активатора «Джони», сделать живое окно истории команд в трее и добавить Джони короткую память разговора + долгосрочные факты по явной команде.

**Architecture:** Четыре независимых доработки в существующем модульном стиле проекта (`johnny/*.py`, по одному файлу на ответственность, тонкий диспетчер в `actions.execute`/`app.handle_command`). Память — новый модуль `johnny/memory.py`: короткий контекст в памяти процесса (`deque`), факты — в `memory.yaml` на диске. Живая история — новый модуль `johnny/history_viewer.py` на Tkinter, вызывается из `tray.py`.

**Tech Stack:** Python 3.14, PyYAML (уже зависимость), Tkinter (стандартная библиотека), pywin32 (уже зависимость, для поиска/подъёма окна).

## Global Constraints

- Каждый новый параметр функции с значением по умолчанию — существующие вызовы не должны потребовать правки, если это не тесты, мокающие саму функцию (см. Task 6 про моки `app.interpret`).
- Все новые тесты — `pytest`, стиль и именование как в существующих файлах (`tests/test_*.py`, docstring объясняет ЖИВОЙ повод, если он есть).
- Долгосрочная память — только явной командой в этой итерации, автоизвлечение НЕ делаем (решение пользователя, см. спеку).
- `memory.yaml` — в `.gitignore`, как `history.log`.

---

## Task 1: «напечатай» — алиас для ввода текста

Изначально просили «напиши», но `"напиши *"` в `config/commands.yaml:571` уже занято под Discord (`discord_message`) — решение пользователя: не трогать Discord, завести отдельное слово.

**Files:**
- Modify: `config/commands.yaml:606` (сразу после `"введи *"`, перед `"энтер"`)
- Test: `tests/test_router.py`

**Interfaces:**
- Produces: команда `"напечатай *"` → `action: type_text`, template `"{0}"` — тот же контракт, что у `"ввод *"`/`"вводи *"`/`"введи *"`.

- [ ] **Step 1: Написать падающий тест на реальном конфиге**

Добавить в конец `tests/test_router.py`:

```python
def test_напечатай_вводит_текст_а_напиши_остаётся_discord():
    """«напечатай» — отдельное слово для ввода текста (не «напиши»): то уже
    занято под Discord-сообщения («напиши Стасу привет»). Оба смысла —
    свободный текст после глагола, шаблонами неотличимы, поэтому слова
    обязаны быть разными."""
    commands = _shipped_commands()

    typed = route("напечатай привет как дела", commands)
    assert typed is not None and typed.action == "type_text"
    assert typed.argument == "привет как дела"

    messaged = route("напиши гоше привет", commands)
    assert messaged is not None and messaged.action == "discord_message"
```

- [ ] **Step 2: Убедиться, что тест падает**

Run: `.venv/Scripts/python.exe -m pytest tests/test_router.py::test_напечатай_вводит_текст_а_напиши_остаётся_discord -v`
Expected: FAIL — `typed` is `None` (или `typed.action` не `"type_text"`), потому что команды `"напечатай *"` в конфиге ещё нет.

- [ ] **Step 3: Добавить команду в `config/commands.yaml`**

После строки 606 (`  template: "{0}"` для `"введи *"`), перед `"энтер":` вставить:

```yaml
"напечатай *":
  action: type_text
  template: "{0}"
```

- [ ] **Step 4: Убедиться, что тест проходит**

Run: `.venv/Scripts/python.exe -m pytest tests/test_router.py::test_напечатай_вводит_текст_а_напиши_остаётся_discord -v`
Expected: PASS

- [ ] **Step 5: Прогнать весь набор тестов (регрессия)**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: все тесты зелёные (было 505, станет 506).

- [ ] **Step 6: Commit**

```bash
git add config/commands.yaml tests/test_router.py
git commit -m "feat: команда «напечатай» для ввода текста (напиши занято под Discord)"
```

---

## Task 2: Фикс бага «vd» — латинский мусор вместо активатора

Живые данные (`history.log`, 8 случаев 2026-08-04..06): Whisper иногда пишет активатор «Джони» коротким (1–4 буквы) латинским мусором вместо кириллицы, мусор остаётся первым словом фразы. Существующий `recover_misheard_name` (johnny/listener.py:52) это не ловит — он требует, чтобы остаток фразы БЕЗ первого слова был точной командой, а тут почти всегда обычный разговор для модели.

**Files:**
- Modify: `johnny/listener.py` (новая функция после `recover_misheard_name`, перед `class Listener` — после строки 74)
- Modify: `johnny/controller.py:7` (импорт), `johnny/controller.py:245-253` (`_joined_turn`)
- Test: `tests/test_wake_strip.py`, `tests/test_controller.py`

**Interfaces:**
- Produces: `listener.recover_latin_prefix(text: str) -> str | None` — `None`, если резать нечего; иначе фраза без первого слова.
- Consumes (в `controller.py`): подключается ВТОРЫМ рубежом, сразу после того, как `recover_misheard_name` вернул `None`.

- [ ] **Step 1: Написать падающие тесты на `recover_latin_prefix`**

Добавить в конец `tests/test_wake_strip.py`:

```python
from johnny.listener import recover_latin_prefix


def test_recovers_latin_wake_word_garbage():
    """Живой случай из history.log (2026-08-04): активатор услышан как «vd»,
    дальше — обычный связный русский текст."""
    assert (
        recover_latin_prefix("vd перезапустил и команда ввод нормально работает")
        == "перезапустил и команда ввод нормально работает"
    )


def test_recovers_latin_wake_word_garbage_before_long_conversation():
    text = "vd а еще он сам становится тише из за того что делает громкость тише"
    assert (
        recover_latin_prefix(text)
        == "а еще он сам становится тише из за того что делает громкость тише"
    )


def test_does_not_touch_all_latin_phrase():
    # Вся фраза на латинице — сравнивать не с чем (нет кириллицы в остатке),
    # резать её как «мусор перед активатором» нельзя.
    assert recover_latin_prefix("hello world") is None


def test_does_not_touch_single_word():
    assert recover_latin_prefix("vd") is None


def test_does_not_touch_long_latin_first_word():
    # Длиннее 4 букв — не похоже на короткий обрывок распознавания, скорее
    # настоящее слово (например, имя программы).
    assert recover_latin_prefix("steam завис у меня") is None


def test_does_not_touch_when_no_cyrillic_follows():
    # Мусорное слово есть, но дальше тоже не кириллица — нечего восстанавливать.
    assert recover_latin_prefix("vd 123 456") is None
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `.venv/Scripts/python.exe -m pytest tests/test_wake_strip.py -v -k recover_latin`
Expected: FAIL с `ImportError: cannot import name 'recover_latin_prefix'`.

- [ ] **Step 3: Реализовать `recover_latin_prefix` в `johnny/listener.py`**

Вставить после функции `recover_misheard_name` (после строки 74, перед `class Listener:` на строке 77):

```python
_LATIN_GARBAGE = re.compile(r"^[a-z]{1,4}$")


def recover_latin_prefix(text: str) -> str | None:
    """Первое слово — короткий латинский мусор вместо ослышанного активатора?

    Живой баг (2026-08-06, history.log): Whisper иногда пишет «Джони»
    короткими латинскими буквами («vd» и т.п.) вместо кириллицы — восемь
    живых случаев, во всех мусорный токен строго первым словом, дальше идёт
    связный русский текст. `recover_misheard_name` это не ловит: он требует,
    чтобы остаток фразы был точной командой, а тут почти всегда обычный
    разговор для модели, а не команда.
    """
    words = _words(text)
    if len(words) < 2:
        return None
    head, rest = words[0], words[1:]
    if not _LATIN_GARBAGE.match(head):
        return None
    if not any(re.search(r"[а-я]", word) for word in rest):
        return None
    return " ".join(rest)
```

- [ ] **Step 4: Убедиться, что тесты проходят**

Run: `.venv/Scripts/python.exe -m pytest tests/test_wake_strip.py -v -k recover_latin`
Expected: PASS (6 тестов).

- [ ] **Step 5: Написать падающий тест на подключение в `controller.py`**

Добавить в конец `tests/test_controller.py` (после `test_misheard_name_is_recovered_and_trusted`, использует уже существующие `_config_recovery`, `FakeListener`, `FakeRecognizer`, `talking_mic`, `_join_worker`, `SpySpeaker`):

```python
def test_latin_prefix_is_recovered_and_trusted(monkeypatch):
    """Живой баг (2026-08-06): Whisper услышал активатор «Джони» как «vd».

    recover_misheard_name тут бессилен (остаток — не команда, а обычный
    разговор), поэтому recover_latin_prefix — второй, более широкий рубеж.
    """
    import johnny.controller as c

    seen = {}
    monkeypatch.setattr(c.history, "add", lambda text, via=None: seen.setdefault("via", via))
    monkeypatch.setattr(
        c,
        "handle_command",
        lambda text, cfg, sp, **kw: (
            seen.update(text=text, use_brain=kw.get("use_brain")),
            c.Outcome("точно"),
        )[1],
    )
    ctrl = AssistantController(_config_recovery(), SpySpeaker())
    ctrl.run_one_cycle(FakeListener(), FakeRecognizer("vd а вообще всё нормально"), talking_mic())
    _join_worker(ctrl)
    assert seen["text"] == "а вообще всё нормально"
    assert seen["use_brain"] is True, "человек звал — модель спрашивать можно"
    assert seen["via"] == "слитно(имя восстановлено: латиница)[0.00]/точно"
```

- [ ] **Step 6: Убедиться, что тест падает**

Run: `.venv/Scripts/python.exe -m pytest tests/test_controller.py::test_latin_prefix_is_recovered_and_trusted -v`
Expected: FAIL — `seen["via"]` не совпадает (`recover_latin_prefix` ещё не подключён в `_joined_turn`, текст остаётся с «vd» и уходит как непонятая слитная фраза без имени).

- [ ] **Step 7: Подключить в `johnny/controller.py`**

Строка 7, изменить импорт:

```python
from .listener import recover_latin_prefix, recover_misheard_name, strip_wake_word
```

Строки 245-253 (внутри `_joined_turn`, ветка `if not found:`), заменить:

```python
        if not found:
            # Whisper не теряет имя, а подменяет его чужим словом («не»,
            # «желание»). Если без первого слова получается команда, а с ним
            # не получается ничего — звали именно Джони.
            recovered = recover_misheard_name(text, self.config.commands)
            if recovered is not None:
                logger.info("Имя услышано как %r — восстановлено", text.split()[0])
                text, found = recovered, True
                mark = "слитно(имя восстановлено)"
```

на:

```python
        if not found:
            # Whisper не теряет имя, а подменяет его чужим словом («не»,
            # «желание»). Если без первого слова получается команда, а с ним
            # не получается ничего — звали именно Джони.
            recovered = recover_misheard_name(text, self.config.commands)
            if recovered is not None:
                logger.info("Имя услышано как %r — восстановлено", text.split()[0])
                text, found = recovered, True
                mark = "слитно(имя восстановлено)"
            else:
                # Второй, более широкий рубеж: короткий латинский мусор
                # первым словом вместо активатора (живой баг «vd», не
                # обязательно команда — recover_misheard_name это не ловит).
                latin_recovered = recover_latin_prefix(text)
                if latin_recovered is not None:
                    logger.info(
                        "Имя услышано как %r (латиница) — восстановлено", text.split()[0]
                    )
                    text, found = latin_recovered, True
                    mark = "слитно(имя восстановлено: латиница)"
```

- [ ] **Step 8: Убедиться, что тест проходит**

Run: `.venv/Scripts/python.exe -m pytest tests/test_controller.py::test_latin_prefix_is_recovered_and_trusted -v`
Expected: PASS

- [ ] **Step 9: Прогнать весь набор тестов**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: все тесты зелёные.

- [ ] **Step 10: Commit**

```bash
git add johnny/listener.py johnny/controller.py tests/test_wake_strip.py tests/test_controller.py
git commit -m "fix: восстановление активатора, услышанного латинским мусором («vd»)"
```

---

## Task 3: `johnny/memory.py` — короткая память разговора

Кольцевой буфер последних обменов «пользователь/Джони» в памяти процесса — не переживает перезапуск (не нужно, это контекст живого разговора). Основной сценарий: Джони спросил «Хотите X?», пользователь сказал «Джони, да, давай» — следующий вызов модели должен видеть предыдущий обмен.

**Files:**
- Create: `johnny/memory.py`
- Test: `tests/test_memory.py`

**Interfaces:**
- Produces:
  - `memory.Turn` — dataclass `(user_text: str, reply: str, at: float)`.
  - `memory.record_turn(user_text: str, reply: str) -> None`
  - `memory.recent_context(max_age: float = 600.0) -> list[Turn]`
  - Module state: `memory._turns` (`deque`, maxlen 5) — тесты обязаны чистить его перед своим прогоном (`memory._turns.clear()`), это общее процессное состояние.

- [ ] **Step 1: Написать падающие тесты**

Создать `tests/test_memory.py`:

```python
import itertools

import johnny.memory as memory
from johnny.memory import Turn


def _reset():
    memory._turns.clear()


def test_record_turn_appears_in_recent_context():
    _reset()
    memory.record_turn("привет", "привет, сэр")
    context = memory.recent_context()
    assert len(context) == 1
    assert context[0].user_text == "привет"
    assert context[0].reply == "привет, сэр"


def test_recent_context_keeps_only_last_five(monkeypatch):
    _reset()
    counter = itertools.count(0.0)
    monkeypatch.setattr(memory.time, "monotonic", lambda: next(counter))
    for i in range(6):
        memory.record_turn(f"вопрос{i}", f"ответ{i}")
    context = memory.recent_context(max_age=100.0)
    assert len(context) == 5
    assert context[0].user_text == "вопрос1"  # вопрос0 вытеснен (maxlen=5)
    assert context[-1].user_text == "вопрос5"


def test_recent_context_drops_turns_older_than_max_age(monkeypatch):
    _reset()
    times = iter([0.0, 700.0])  # обмен в момент 0, проверка в момент 700
    monkeypatch.setattr(memory.time, "monotonic", lambda: next(times))
    memory.record_turn("вопрос", "ответ")
    assert memory.recent_context(max_age=600.0) == []


def test_recent_context_keeps_turns_within_max_age(monkeypatch):
    _reset()
    times = iter([0.0, 300.0])
    monkeypatch.setattr(memory.time, "monotonic", lambda: next(times))
    memory.record_turn("вопрос", "ответ")
    assert len(memory.recent_context(max_age=600.0)) == 1


def test_empty_memory_has_no_context():
    _reset()
    assert memory.recent_context() == []
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `.venv/Scripts/python.exe -m pytest tests/test_memory.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'johnny.memory'`.

- [ ] **Step 3: Реализовать `johnny/memory.py`**

```python
"""Память Джони: короткий контекст разговора (не переживает перезапуск) и
долгосрочные факты (файл на диске, только явной командой — см. remember/
forget)."""

import time
from collections import deque
from dataclasses import dataclass

# Сколько последних обменов держим и на сколько секунд им доверяем. Дольше
# 10 минут молчания — считаем разговор остывшим: случайное «да» через час
# не должно уехать в вопрос, который давно все забыли.
_MAX_TURNS = 5
_CONTEXT_MAX_AGE = 600.0


@dataclass
class Turn:
    user_text: str
    reply: str
    at: float


_turns: deque = deque(maxlen=_MAX_TURNS)


def record_turn(user_text: str, reply: str) -> None:
    """Запомнить обмен репликами. Только опрошенные моделью реплики (см.
    app.handle_command, шаг 4) — обычные локальные команды («громкость 5»
    по точному совпадению) контекст не создают, ссылаться там не на что."""
    _turns.append(Turn(user_text, reply, time.monotonic()))


def recent_context(max_age: float = _CONTEXT_MAX_AGE) -> list:
    """Обмены не старше max_age секунд, от самого старого к новому."""
    now = time.monotonic()
    return [turn for turn in _turns if now - turn.at <= max_age]
```

- [ ] **Step 4: Убедиться, что тесты проходят**

Run: `.venv/Scripts/python.exe -m pytest tests/test_memory.py -v`
Expected: PASS (5 тестов).

- [ ] **Step 5: Прогнать весь набор тестов**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: все тесты зелёные.

- [ ] **Step 6: Commit**

```bash
git add johnny/memory.py tests/test_memory.py
git commit -m "feat: короткая память разговора (johnny/memory.py)"
```

---

## Task 4: `johnny/memory.py` — долгосрочные факты

Файл `memory.yaml` в корне проекта, только явной командой (`remember`/`forget`), плюс сборка блока для промпта модели.

**Files:**
- Modify: `johnny/memory.py` (дополнить)
- Modify: `.gitignore`
- Test: `tests/test_memory.py` (дополнить)

**Interfaces:**
- Produces:
  - `memory.remember(text: str) -> None`
  - `memory.forget(query: str) -> str | None` — текст удалённого факта или `None`
  - `memory.list_facts() -> list[str]`
  - `memory.build_prompt_block(context: list[Turn], facts: list[str]) -> str`
  - Module state: `memory._MEMORY_FILE` (`Path`) — тесты обязаны подменять его через `monkeypatch.setattr(memory, "_MEMORY_FILE", tmp_path / "memory.yaml")`, как `tests/test_history.py` делает с `history._HISTORY_FILE`.
- Consumes: `Turn` из Task 3.

**Важная деталь по подбору порога `forget`** (проверено числами, не угадано): `difflib.SequenceMatcher` по ЦЕЛЫМ строкам не годится — короткий голосовой запрос («стим-аккаунт») против длинного факта («у пользователя стим-аккаунт art_vol_teror») даёт всего 0.45 похожести, хотя оба слова запроса буквально есть в факте. Матчинг — по ДОЛЕ СЛОВ запроса, найденных в тексте факта.

- [ ] **Step 1: Написать падающие тесты**

Добавить в конец `tests/test_memory.py`:

```python
def test_remember_appends_fact(tmp_path, monkeypatch):
    f = tmp_path / "memory.yaml"
    monkeypatch.setattr(memory, "_MEMORY_FILE", f)
    memory.remember("любит кофе без сахара")
    assert memory.list_facts() == ["любит кофе без сахара"]


def test_remember_appends_to_existing_facts(tmp_path, monkeypatch):
    f = tmp_path / "memory.yaml"
    monkeypatch.setattr(memory, "_MEMORY_FILE", f)
    memory.remember("факт раз")
    memory.remember("факт два")
    assert memory.list_facts() == ["факт раз", "факт два"]


def test_list_facts_on_missing_file_is_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "_MEMORY_FILE", tmp_path / "missing.yaml")
    assert memory.list_facts() == []


def test_forget_removes_closest_matching_fact(tmp_path, monkeypatch):
    """Живой пример из спеки: короткий запрос против длинного факта.
    SequenceMatcher по целым строкам тут дал бы 0.45 — ниже разумного
    порога; доля слов запроса, найденных в факте, — 1.0."""
    f = tmp_path / "memory.yaml"
    monkeypatch.setattr(memory, "_MEMORY_FILE", f)
    memory.remember("у пользователя стим-аккаунт art_vol_teror")
    memory.remember("любит кофе без сахара")
    removed = memory.forget("стим-аккаунт")
    assert removed == "у пользователя стим-аккаунт art_vol_teror"
    assert memory.list_facts() == ["любит кофе без сахара"]


def test_forget_matches_by_partial_word_overlap(tmp_path, monkeypatch):
    f = tmp_path / "memory.yaml"
    monkeypatch.setattr(memory, "_MEMORY_FILE", f)
    memory.remember("у пользователя стим-аккаунт art_vol_teror")
    # 2 из 3 слов запроса совпадают (0.667 ≥ порога 0.5) — «про» в факте нет.
    removed = memory.forget("про стим аккаунт")
    assert removed == "у пользователя стим-аккаунт art_vol_teror"


def test_forget_returns_none_when_nothing_close_enough(tmp_path, monkeypatch):
    f = tmp_path / "memory.yaml"
    monkeypatch.setattr(memory, "_MEMORY_FILE", f)
    memory.remember("любит кофе без сахара")
    assert memory.forget("совершенно другая тема про космос") is None
    assert memory.list_facts() == ["любит кофе без сахара"]


def test_forget_on_empty_memory_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "_MEMORY_FILE", tmp_path / "missing.yaml")
    assert memory.forget("что угодно") is None


def test_build_prompt_block_empty_when_nothing_to_show():
    assert memory.build_prompt_block([], []) == ""


def test_build_prompt_block_includes_context_and_facts():
    context = [Turn("хотите анекдот", "да, вот анекдот про...", at=0.0)]
    block = memory.build_prompt_block(context, ["любит кофе без сахара"])
    assert "хотите анекдот" in block
    assert "любит кофе без сахара" in block


def test_build_prompt_block_context_only():
    context = [Turn("привет", "привет, сэр", at=0.0)]
    block = memory.build_prompt_block(context, [])
    assert "привет" in block
    assert "Известно о пользователе" not in block


def test_build_prompt_block_facts_only():
    block = memory.build_prompt_block([], ["любит кофе без сахара"])
    assert "любит кофе без сахара" in block
    assert "Недавний разговор" not in block
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `.venv/Scripts/python.exe -m pytest tests/test_memory.py -v -k "remember or forget or build_prompt_block"`
Expected: FAIL — `AttributeError: module 'johnny.memory' has no attribute 'remember'` (и аналогично для `forget`/`list_facts`/`build_prompt_block`).

- [ ] **Step 3: Дописать `johnny/memory.py`**

Добавить в начало файла новые импорты (после существующих):

```python
import re
from datetime import datetime
from pathlib import Path

import yaml
```

Добавить в конец файла:

```python
_MEMORY_FILE = Path(__file__).resolve().parent.parent / "memory.yaml"
# Доля слов запроса, которые обязаны найтись в тексте факта, чтобы forget()
# посчитал его найденным. НЕ SequenceMatcher по целым строкам: короткий
# голосовой запрос («стим-аккаунт») против длинного факта («у пользователя
# стим-аккаунт art_vol_teror») даёт посимвольно всего 0.45 — ниже разумного
# порога, хотя оба слова запроса в факте буквально есть.
_FORGET_THRESHOLD = 0.5


def _load_facts() -> list:
    if not _MEMORY_FILE.exists():
        return []
    data = yaml.safe_load(_MEMORY_FILE.read_text(encoding="utf-8"))
    return data or []


def _save_facts(facts: list) -> None:
    _MEMORY_FILE.write_text(
        yaml.safe_dump(facts, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )


def remember(text: str) -> None:
    facts = _load_facts()
    facts.append({"text": text, "added": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
    _save_facts(facts)


def _word_overlap(query: str, fact_text: str) -> float:
    words = re.findall(r"[a-zа-я0-9_]+", query.lower().replace("ё", "е"))
    if not words:
        return 0.0
    fact_norm = fact_text.lower().replace("ё", "е")
    hits = sum(1 for word in words if word in fact_norm)
    return hits / len(words)


def forget(query: str) -> str | None:
    """Удалить ближайший по смыслу факт. См. модульный докстринг
    _FORGET_THRESHOLD про то, почему это доля слов, а не SequenceMatcher."""
    facts = _load_facts()
    if not facts:
        return None
    best_index, best_score = None, 0.0
    for index, fact in enumerate(facts):
        score = _word_overlap(query, fact["text"])
        if score > best_score:
            best_index, best_score = index, score
    if best_index is None or best_score < _FORGET_THRESHOLD:
        return None
    removed = facts.pop(best_index)
    _save_facts(facts)
    return removed["text"]


def list_facts() -> list:
    return [fact["text"] for fact in _load_facts()]


def build_prompt_block(context: list, facts: list) -> str:
    """Необязательный блок для brain._PROMPT. Пустая строка, если ни
    контекста, ни фактов нет — на месте {memory_block} останется пусто."""
    parts = []
    if context:
        lines = "\n".join(
            f'Пользователь сказал: "{turn.user_text}" — ты ответил: "{turn.reply}"'
            for turn in context
        )
        parts.append(
            "Недавний разговор (если сейчас продолжение — например, короткое "
            "«да»/«давай» отвечает на твой последний вопрос):\n" + lines
        )
    if facts:
        parts.append("Известно о пользователе:\n" + "\n".join(f"- {fact}" for fact in facts))
    return ("\n\n".join(parts) + "\n") if parts else ""
```

- [ ] **Step 4: Убедиться, что тесты проходят**

Run: `.venv/Scripts/python.exe -m pytest tests/test_memory.py -v`
Expected: PASS (все тесты файла).

- [ ] **Step 5: Добавить `memory.yaml` в `.gitignore`**

В `.gitignore`, рядом со строкой `history.log`, добавить новую строку:

```
memory.yaml
```

- [ ] **Step 6: Прогнать весь набор тестов**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: все тесты зелёные.

- [ ] **Step 7: Commit**

```bash
git add johnny/memory.py tests/test_memory.py .gitignore
git commit -m "feat: долгосрочные факты в памяти Джони (johnny/memory.py)"
```

---

## Task 5: `brain.py` — память в промпте модели

`interpret()` получает необязательный `memory_block`, подставляемый в `_PROMPT`. Функция остаётся чистой (не импортирует `memory` — блок собирает вызывающий код в `app.py`, Task 6).

**Files:**
- Modify: `johnny/brain.py`
- Test: `tests/test_brain.py`

**Interfaces:**
- Consumes: ничего нового (просто новая строка параметра).
- Produces: `interpret(text, commands=None, providers=None, memory_block: str = "") -> BrainResult | None` — четвёртый параметр, необязательный, только keyword в вызовах из `app.py` (Task 6).

- [ ] **Step 1: Написать падающие тесты**

Добавить в конец `tests/test_brain.py`:

```python
def test_memory_block_is_included_in_prompt():
    seen = {}

    def fake(prompt):
        seen["prompt"] = prompt
        return "ок"

    interpret(
        "что-то",
        CORRECTOR_COMMANDS,
        [("тест", fake)],
        memory_block="Известно о пользователе:\n- любит кофе без сахара",
    )
    assert "любит кофе без сахара" in seen["prompt"]


def test_empty_memory_block_still_produces_valid_prompt():
    # По умолчанию memory_block="" — прежнее поведение без памяти не должно
    # ломаться (interpret вызывается без этого аргумента в старых тестах).
    result = interpret("столица японии", CORRECTOR_COMMANDS, _provider("Токио, сэр."))
    assert result.reply == "Токио, сэр."
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `.venv/Scripts/python.exe -m pytest tests/test_brain.py -v -k memory_block`
Expected: FAIL — `TypeError: interpret() got an unexpected keyword argument 'memory_block'` для первого теста (второй уже проходит и без изменений — это регрессионная страховка, полезно оставить).

- [ ] **Step 3: Изменить `johnny/brain.py`**

`_PROMPT`, вставить `{memory_block}` между вступлением и «Пользователь сказал» (заменить пустую строку на строку 2-3):

```python
_PROMPT = """Ты — голосовой ассистент Джони на компьютере с Windows.
Текст ниже пришёл от распознавания речи и МОГ БЫТЬ ИСКОВЕРКАН.
{memory_block}
Пользователь сказал: "{text}"
```

(остальной текст `_PROMPT` без изменений).

Сигнатура и тело `interpret`:

```python
def interpret(
    text: str, commands=None, providers=None, memory_block: str = ""
) -> BrainResult | None:
    """Спросить модель. providers — список (имя, функция prompt->текст),
    перебираются по порядку до первого непустого ответа. memory_block —
    необязательный блок короткой памяти разговора + долгосрочных фактов
    (см. johnny.memory.build_prompt_block), подставляется в промпт как есть."""
    commands = commands or []
    if providers is None:
        providers = [("claude", brain_claude.run)]
    safe_commands = [rule for rule in commands if not is_unsafe_action(rule.action, rule.template)]
    listing = "\n".join(f"- {rule.pattern}" for rule in safe_commands)
    prompt = _PROMPT.format(
        text=text, count=len(safe_commands), commands=listing, memory_block=memory_block
    )
```

(остальное тело `interpret` без изменений — меняются только сигнатура и строка сборки `prompt`).

- [ ] **Step 4: Убедиться, что тесты проходят**

Run: `.venv/Scripts/python.exe -m pytest tests/test_brain.py -v`
Expected: PASS (весь файл, включая старые тесты — `_PROMPT.format` по-прежнему получает все нужные ключи).

- [ ] **Step 5: Прогнать весь набор тестов**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: все тесты зелёные.

- [ ] **Step 6: Commit**

```bash
git add johnny/brain.py tests/test_brain.py
git commit -m "feat: brain.interpret принимает memory_block для контекста разговора"
```

---

## Task 6: `app.py` — подключение памяти к `handle_command`

Перед вызовом модели собираем блок памяти, после ответа — записываем обмен. Существующие тесты, мокающие `app.interpret` тремя позиционными параметрами, обязаны принять новый keyword-параметр (иначе упадут на `TypeError`).

**Files:**
- Modify: `johnny/app.py`
- Modify: `tests/test_app.py`

**Interfaces:**
- Consumes: `memory.recent_context()`, `memory.list_facts()`, `memory.build_prompt_block()` (Task 3+4), `memory.record_turn()` (Task 3), `interpret(..., memory_block=...)` (Task 5).

- [ ] **Step 1: Обновить существующие моки `app.interpret` в `tests/test_app.py`**

Все 8 мест, где `monkeypatch.setattr(app, "interpret", lambda text, commands, providers: ...)`, обязаны принять `**kwargs` — иначе после Task-6-правки `app.py` они упадут на `TypeError: <lambda>() got an unexpected keyword argument 'memory_block'`. В `tests/test_app.py` заменить (везде, где сигнатура лямбды — `lambda text, commands, providers: ...`) на `lambda text, commands, providers, **kwargs: ...`, сохранив тело без изменений. Точные строки (до правки):

```
57:    monkeypatch.setattr(app, "interpret", lambda text, commands, providers: BrainResult(routed=None, reply="Отвечаю"))
78:    monkeypatch.setattr(app, "interpret", lambda text, commands, providers: BrainResult(routed=RoutedAction("launch_app", "notepad"), reply="Запускаю"))
91:    monkeypatch.setattr(app, "interpret", lambda text, commands, providers: BrainResult(routed=None, reply=None))
99:    monkeypatch.setattr(app, "interpret", lambda text, commands, providers: None)
176:    monkeypatch.setattr(app, "interpret", lambda text, commands, providers: None)
270:        app, "interpret", lambda text, commands, providers: BrainResult(routed=None, reply="Отвечаю")
```

(строки 205 и 231 уже используют `lambda *a, **k: ...` — их трогать не нужно).

Замена (пример для строки 57, аналогично для строк 78, 91, 99, 176):

```python
    monkeypatch.setattr(app, "interpret", lambda text, commands, providers, **kwargs: BrainResult(routed=None, reply="Отвечаю"))
```

Строка 270 (в `test_cancel_set_silences_brain_reply`) — многострочный вызов, найти по содержимому:

```python
    monkeypatch.setattr(
        app, "interpret", lambda text, commands, providers: BrainResult(routed=None, reply="Отвечаю")
    )
```

заменить на:

```python
    monkeypatch.setattr(
        app, "interpret", lambda text, commands, providers, **kwargs: BrainResult(routed=None, reply="Отвечаю")
    )
```

- [ ] **Step 2: Убедиться, что весь файл всё ещё проходит (изменение чисто механическое)**

Run: `.venv/Scripts/python.exe -m pytest tests/test_app.py -v`
Expected: PASS (правка сигнатуры не меняет поведение, реальный код `app.py` ещё не менялся).

- [ ] **Step 3: Написать падающий тест на запись в короткую память**

Добавить в конец `tests/test_app.py`:

```python
def test_brain_turn_is_recorded_in_memory(monkeypatch):
    """После обращения к модели обмен должен попасть в короткую память —
    иначе следующее «да, давай» не сможет сослаться на этот вопрос."""
    import johnny.app as app
    from johnny.brain import BrainResult
    import johnny.memory as memory

    memory._turns.clear()
    monkeypatch.setattr(
        app, "interpret", lambda text, commands, providers, **kwargs: BrainResult(routed=None, reply="Хотите анекдот?")
    )
    handle_command("расскажи что-нибудь", _config(), SpySpeaker())
    context = memory.recent_context()
    assert len(context) == 1
    assert context[0].user_text == "расскажи что-нибудь"
    assert context[0].reply == "Хотите анекдот?"


def test_memory_block_reaches_interpret(monkeypatch):
    """Собранный блок памяти обязан дойти до interpret(), иначе модель не
    увидит ни контекст разговора, ни факты."""
    import johnny.app as app
    import johnny.memory as memory
    from johnny.brain import BrainResult

    memory._turns.clear()
    monkeypatch.setattr(memory, "list_facts", lambda: ["любит кофе без сахара"])
    seen = {}

    def fake_interpret(text, commands, providers, **kwargs):
        seen["memory_block"] = kwargs.get("memory_block", "")
        return BrainResult(routed=None, reply="ок")

    monkeypatch.setattr(app, "interpret", fake_interpret)
    handle_command("расскажи что-нибудь", _config(), SpySpeaker())
    assert "любит кофе без сахара" in seen["memory_block"]
```

- [ ] **Step 4: Убедиться, что тесты падают**

Run: `.venv/Scripts/python.exe -m pytest tests/test_app.py -v -k "recorded_in_memory or memory_block_reaches"`
Expected: FAIL — `context` пустой (память ещё нигде не пишется), `seen["memory_block"]` не содержит факт (ничего пока не передаётся).

- [ ] **Step 5: Изменить `johnny/app.py`**

Импорты в начале файла — добавить `memory` в список локальных импортов:

```python
from . import chain, memory
```

(было `from . import chain`).

Шаг 4 в `handle_command` — заменить:

```python
        if not use_brain:
            return not_understood("Не понял команду", "мимо")
        # 4. Модель.
        answer = interpret(text, config.commands, make_providers(config))
        if cancel is not None and cancel.is_set():
            # «Стоп» пришёл, пока модель думала (Groq/claude -p не прервать
            # на лету) — результат уже никому не нужен, ни говорить, ни
            # выполнять его нельзя.
            return Outcome(answer.provider if answer else "модель")
        if answer is None:
            return not_understood("Не понял команду", "модель недоступна")
```

на:

```python
        if not use_brain:
            return not_understood("Не понял команду", "мимо")
        # 4. Модель.
        memory_block = memory.build_prompt_block(memory.recent_context(), memory.list_facts())
        answer = interpret(text, config.commands, make_providers(config), memory_block=memory_block)
        if cancel is not None and cancel.is_set():
            # «Стоп» пришёл, пока модель думала (Groq/claude -p не прервать
            # на лету) — результат уже никому не нужен, ни говорить, ни
            # выполнять его нельзя.
            return Outcome(answer.provider if answer else "модель")
        if answer is None:
            return not_understood("Не понял команду", "модель недоступна")
        # Короткая память: только опрошенные моделью реплики создают
        # контекст, на который может сослаться следующее «да, давай».
        memory.record_turn(text, answer.reply or "")
```

- [ ] **Step 6: Убедиться, что тесты проходят**

Run: `.venv/Scripts/python.exe -m pytest tests/test_app.py -v`
Expected: PASS (весь файл).

- [ ] **Step 7: Прогнать весь набор тестов**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: все тесты зелёные.

- [ ] **Step 8: Commit**

```bash
git add johnny/app.py tests/test_app.py
git commit -m "feat: handle_command передаёт память в brain и записывает обмены"
```

---

## Task 7: Команды «запомни»/«забудь»/«что ты помнишь»

Новые локальные команды (не через модель), плюс принудительная озвучка для `list_memory` — обычный путь `_respond` при успехе предпочитает короткий звук вместо речи, а список фактов так никогда бы не прозвучал.

**Files:**
- Modify: `config/commands.yaml` (конец файла, отдельная секция)
- Modify: `johnny/actions.py`
- Modify: `johnny/app.py` (`_dispatch`)
- Test: `tests/test_actions.py`, `tests/test_app.py`, `tests/test_router.py`

**Interfaces:**
- Consumes: `memory.remember`, `memory.forget`, `memory.list_facts` (Task 4).
- Produces: три новых `action` в роутере — `remember`, `forget`, `list_memory`.

- [ ] **Step 1: Написать падающие тесты на `actions.execute`**

Добавить в `tests/test_actions.py` (рядом с `test_execute_type_text_types_into_focused_field`/`test_execute_press_enter`, тот же стиль):

```python
def test_execute_remember_saves_fact(monkeypatch):
    import johnny.memory as memory

    saved = []
    monkeypatch.setattr(memory, "remember", lambda text: saved.append(text))
    result = execute(RoutedAction("remember", "любит кофе без сахара"), apps={})
    assert saved == ["любит кофе без сахара"]
    assert result.ok is True


def test_execute_forget_reports_success_when_found(monkeypatch):
    import johnny.memory as memory

    monkeypatch.setattr(memory, "forget", lambda query: "любит кофе без сахара")
    result = execute(RoutedAction("forget", "кофе"), apps={})
    assert result.ok is True


def test_execute_forget_reports_failure_when_not_found(monkeypatch):
    import johnny.memory as memory

    monkeypatch.setattr(memory, "forget", lambda query: None)
    result = execute(RoutedAction("forget", "что угодно"), apps={})
    assert result.ok is False
    assert result.message == "Не нашёл такое в памяти"


def test_execute_list_memory_speaks_facts(monkeypatch):
    import johnny.memory as memory

    monkeypatch.setattr(memory, "list_facts", lambda: ["любит кофе", "играет за art_vol_teror"])
    result = execute(RoutedAction("list_memory", "-"), apps={})
    assert result.ok is True
    assert "любит кофе" in result.message
    assert "играет за art_vol_teror" in result.message


def test_execute_list_memory_when_nothing_remembered(monkeypatch):
    import johnny.memory as memory

    monkeypatch.setattr(memory, "list_facts", lambda: [])
    result = execute(RoutedAction("list_memory", "-"), apps={})
    assert result.ok is True
    assert result.message == "Я пока ничего не помню"
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `.venv/Scripts/python.exe -m pytest tests/test_actions.py -v -k "remember or forget or list_memory"`
Expected: FAIL — `execute` возвращает `ActionResult(False, "Неизвестное действие")` для всех трёх (веток ещё нет в `execute`).

- [ ] **Step 3: Добавить команды в `config/commands.yaml`**

Файл заканчивается жадным catch-all `"открой *"`/`"фокус *"` — это единственный шаблон с одной звёздочкой без хвоста, и он обязан оставаться последним (см. предупреждающий комментарий прямо перед ним в файле). Найти в файле точную строку:

```
# ВАЖНО: стоят В САМОМ КОНЦЕ файла намеренно. "открой *"/"фокус *" — это
```

и вставить секцию памяти СРАЗУ ПЕРЕД этой строкой комментария (после последнего правила `"нажми энтер"`/пустой строки, которая идёт перед ним):

```yaml
# --- Память ---
"запомни *":
  action: remember
  template: "{0}"
"забудь *":
  action: forget
  template: "{0}"
"что ты помнишь":
  action: list_memory
  template: "-"
"что ты помнишь обо мне":
  action: list_memory
  template: "-"

```

- [ ] **Step 4: Добавить ветки в `johnny/actions.py`**

В `johnny/actions.py` строка `from .router import RoutedAction` (не в самом верху файла — идёт после блока констант `_DISCORD_*`, это нормально для этого файла) заменить на:

```python
from . import memory
from .router import RoutedAction
```

В `execute()`, перед финальным `return ActionResult(False, "Неизвестное действие")`, добавить:

```python
    if routed.action == "remember":
        memory.remember(routed.argument)
        return ActionResult(True, "Запомнил")
    if routed.action == "forget":
        removed = memory.forget(routed.argument)
        if removed is not None:
            return ActionResult(True, "Забыл")
        return ActionResult(False, "Не нашёл такое в памяти")
    if routed.action == "list_memory":
        facts = memory.list_facts()
        if not facts:
            return ActionResult(True, "Я пока ничего не помню")
        return ActionResult(True, "Вот что я помню: " + "; ".join(facts))
```

- [ ] **Step 5: Убедиться, что тесты проходят**

Run: `.venv/Scripts/python.exe -m pytest tests/test_actions.py -v -k "remember or forget or list_memory"`
Expected: PASS (5 тестов).

- [ ] **Step 6: Написать падающий тест на принудительную озвучку `list_memory` в `app._dispatch`**

Добавить в конец `tests/test_app.py`:

```python
def test_list_memory_is_always_spoken_not_chimed(monkeypatch):
    """«Что ты помнишь» обязано звучать текстом. Обычный путь _respond при
    успехе предпочитает короткий звук (play_answer) вместо речи — для
    списка фактов это означало бы полное молчание."""
    import johnny.app as app
    from johnny.actions import ActionResult
    from johnny.config import CommandRule, Config, Settings

    monkeypatch.setattr(
        app,
        "execute",
        lambda routed, apps, channels, new_tab=True, people=None: ActionResult(
            True, "Вот что я помню: любит кофе без сахара"
        ),
    )
    config = Config(
        apps={},
        commands=[CommandRule("что ты помнишь", "list_memory", "-")],
        settings=Settings("джони", "", "off", "medium", "cuda"),
    )
    sp = SpySpeaker(answer_played=True)  # звук ГОТОВ проиграться, но не должен
    handle_command("что ты помнишь", config, sp)
    assert sp.said == ["Вот что я помню: любит кофе без сахара"]
    assert sp.answer_calls == 0, "list_memory не должен даже пытаться играть звук"
```

- [ ] **Step 7: Убедиться, что тест падает**

Run: `.venv/Scripts/python.exe -m pytest tests/test_app.py::test_list_memory_is_always_spoken_not_chimed -v`
Expected: FAIL — `sp.said == []` (сейчас идёт обычный путь `_respond`, который зовёт `play_answer()`, а не `say()`).

- [ ] **Step 8: Изменить `_dispatch` в `johnny/app.py`**

Заменить:

```python
    routed = _punctuate_dictation(routed, config)
    _respond(
        speaker,
        execute(routed, config.apps, config.channels, new_tab=new_tab, people=config.people),
        cancel=cancel,
    )
    return Outcome(routed.via)
```

на:

```python
    routed = _punctuate_dictation(routed, config)
    result = execute(routed, config.apps, config.channels, new_tab=new_tab, people=config.people)
    if routed.action == "list_memory":
        # «Что ты помнишь» обязано звучать текстом. Обычный _respond при
        # успехе играет короткий звук ВМЕСТО речи (см. play_answer) — для
        # списка фактов это значило бы полное молчание.
        if cancel is None or not cancel.is_set():
            speaker.say(result.message)
        return Outcome(routed.via)
    _respond(speaker, result, cancel=cancel)
    return Outcome(routed.via)
```

- [ ] **Step 9: Убедиться, что тест проходит**

Run: `.venv/Scripts/python.exe -m pytest tests/test_app.py::test_list_memory_is_always_spoken_not_chimed -v`
Expected: PASS

- [ ] **Step 10: Написать регрессионный тест на реальном `config/commands.yaml`**

Добавить в конец `tests/test_router.py`:

```python
def test_память_команды_резолвятся_на_реальном_конфиге():
    commands = _shipped_commands()

    remembered = route("запомни у меня стим аккаунт art_vol_teror", commands)
    assert remembered is not None and remembered.action == "remember"
    assert remembered.argument == "у меня стим аккаунт art_vol_teror"

    forgotten = route("забудь про стим аккаунт", commands)
    assert forgotten is not None and forgotten.action == "forget"

    listed = route("что ты помнишь", commands)
    assert listed is not None and listed.action == "list_memory"
```

- [ ] **Step 11: Убедиться, что тест проходит**

Run: `.venv/Scripts/python.exe -m pytest tests/test_router.py::test_память_команды_резолвятся_на_реальном_конфиге -v`
Expected: PASS (если FAIL — проверить, что секция памяти в `commands.yaml` не оказалась ПОСЛЕ жадного catch-all `"открой *"`/`"фокус *"`, который перехватил бы «запомни …»/«забудь …» первым).

- [ ] **Step 12: Прогнать весь набор тестов**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: все тесты зелёные.

- [ ] **Step 13: Commit**

```bash
git add config/commands.yaml johnny/actions.py johnny/app.py tests/test_actions.py tests/test_app.py tests/test_router.py
git commit -m "feat: голосовые команды «запомни»/«забудь»/«что ты помнишь»"
```

---

## Task 8: Живая история команд в трее

Новое Tk-окно с тейлом `history.log` вместо статичного открытия в Notepad.

**Files:**
- Create: `johnny/history_viewer.py`
- Modify: `johnny/tray.py`
- Test: `tests/test_history_viewer.py`

**Interfaces:**
- Produces:
  - `history_viewer.open_or_focus(history_path: Path) -> None`
  - `history_viewer._read_new_tail(path: Path, last_size: int) -> tuple[str, int]` — чистая функция, тестируется отдельно от Tk.

- [ ] **Step 1: Написать падающие тесты на `_read_new_tail`**

Создать `tests/test_history_viewer.py`:

```python
import johnny.history_viewer as history_viewer


def test_read_new_tail_returns_full_content_first_time(tmp_path):
    f = tmp_path / "history.log"
    f.write_text("строка1\n", encoding="utf-8")
    content, size = history_viewer._read_new_tail(f, 0)
    assert content == "строка1\n"
    assert size == f.stat().st_size


def test_read_new_tail_returns_only_appended_part(tmp_path):
    f = tmp_path / "history.log"
    f.write_text("строка1\n", encoding="utf-8")
    _, size1 = history_viewer._read_new_tail(f, 0)
    with open(f, "a", encoding="utf-8") as fh:
        fh.write("строка2\n")
    content, size2 = history_viewer._read_new_tail(f, size1)
    assert content == "строка2\n"
    assert size2 > size1


def test_read_new_tail_missing_file_returns_empty():
    from pathlib import Path

    content, size = history_viewer._read_new_tail(Path("nonexistent-file.log"), 0)
    assert content == ""
    assert size == 0


def test_read_new_tail_no_change_returns_empty_and_same_size(tmp_path):
    f = tmp_path / "history.log"
    f.write_text("строка1\n", encoding="utf-8")
    _, size1 = history_viewer._read_new_tail(f, 0)
    content, size2 = history_viewer._read_new_tail(f, size1)
    assert content == ""
    assert size2 == size1
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `.venv/Scripts/python.exe -m pytest tests/test_history_viewer.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'johnny.history_viewer'`.

- [ ] **Step 3: Написать падающие тесты на `open_or_focus`**

Добавить в конец `tests/test_history_viewer.py`:

```python
def test_open_or_focus_brings_existing_window_to_front(monkeypatch):
    from pathlib import Path

    calls = []
    fake_gui = type(
        "FakeGui",
        (),
        {
            "FindWindow": staticmethod(lambda cls, title: 42),
            "IsIconic": staticmethod(lambda hwnd: False),
            "SetForegroundWindow": staticmethod(lambda hwnd: calls.append(("front", hwnd))),
            "ShowWindow": staticmethod(lambda hwnd, flag: calls.append(("show", hwnd, flag))),
        },
    )()
    monkeypatch.setattr(history_viewer, "win32gui", fake_gui)
    started = []
    monkeypatch.setattr(history_viewer.threading, "Thread", lambda *a, **k: started.append(True))

    history_viewer.open_or_focus(Path("history.log"))

    assert ("front", 42) in calls
    assert started == [], "окно уже есть — новое открывать не нужно"


def test_open_or_focus_restores_minimized_window(monkeypatch):
    from pathlib import Path

    calls = []
    fake_gui = type(
        "FakeGui",
        (),
        {
            "FindWindow": staticmethod(lambda cls, title: 42),
            "IsIconic": staticmethod(lambda hwnd: True),
            "SetForegroundWindow": staticmethod(lambda hwnd: calls.append(("front", hwnd))),
            "ShowWindow": staticmethod(lambda hwnd, flag: calls.append(("show", hwnd, flag))),
        },
    )()
    fake_con = type("FakeCon", (), {"SW_RESTORE": 9})()
    monkeypatch.setattr(history_viewer, "win32gui", fake_gui)
    monkeypatch.setattr(history_viewer, "win32con", fake_con)
    monkeypatch.setattr(history_viewer.threading, "Thread", lambda *a, **k: None)

    history_viewer.open_or_focus(Path("history.log"))

    assert ("show", 42, 9) in calls


def test_open_or_focus_spawns_new_window_when_none_exists(monkeypatch):
    from pathlib import Path

    fake_gui = type("FakeGui", (), {"FindWindow": staticmethod(lambda cls, title: 0)})()
    monkeypatch.setattr(history_viewer, "win32gui", fake_gui)

    class FakeThread:
        def __init__(self, target, args, daemon):
            self.target, self.args, self.daemon = target, args, daemon
            self.started = False

        def start(self):
            self.started = True

    created = {}

    def fake_thread_ctor(target, args, daemon):
        created["thread"] = FakeThread(target, args, daemon)
        return created["thread"]

    monkeypatch.setattr(history_viewer.threading, "Thread", fake_thread_ctor)

    history_viewer.open_or_focus(Path("history.log"))

    assert created["thread"].started is True
    assert created["thread"].daemon is True
```

- [ ] **Step 4: Убедиться, что новые тесты падают вместе с предыдущими**

Run: `.venv/Scripts/python.exe -m pytest tests/test_history_viewer.py -v`
Expected: FAIL (модуля всё ещё нет).

- [ ] **Step 5: Реализовать `johnny/history_viewer.py`**

```python
"""Живое окно истории команд — вызывается из пункта трея «История команд».

Не Notepad: тот не умеет сам обновляться, закрывать/открывать заново ради
новых строк неудобно (жалоба пользователя, 2026-08-06). Маленькое своё
Tk-окно вместо этого само дочитывает файл каждые полсекунды.
"""

import threading
import tkinter as tk
from pathlib import Path

import win32con
import win32gui

_TITLE = "Джони — история команд"
_POLL_MS = 500


def _read_new_tail(path: Path, last_size: int) -> tuple[str, int]:
    """Хвост файла, добавленный после last_size байт. Файла может не быть
    (история ещё не создана) — тогда хвоста нет, размер 0."""
    if not path.exists():
        return "", 0
    size = path.stat().st_size
    if size <= last_size:
        return "", size
    with open(path, "r", encoding="utf-8") as f:
        f.seek(last_size)
        return f.read(), size


def _run_window(history_path: Path) -> None:
    root = tk.Tk()
    root.title(_TITLE)
    root.geometry("700x400")
    text = tk.Text(root, state="disabled", wrap="word")
    text.pack(fill="both", expand=True)

    def _insert(content: str) -> None:
        text.configure(state="normal")
        text.insert("end", content)
        text.see("end")
        text.configure(state="disabled")

    initial, size = _read_new_tail(history_path, 0)
    state = {"size": size}
    if initial:
        _insert(initial)

    def _poll() -> None:
        content, new_size = _read_new_tail(history_path, state["size"])
        if content:
            _insert(content)
            state["size"] = new_size
        root.after(_POLL_MS, _poll)

    root.after(_POLL_MS, _poll)
    root.mainloop()


def open_or_focus(history_path: Path) -> None:
    """Публичная точка входа для трея. Окно уже открыто — поднимаем его
    (тот же процесс только что получил клик по трею, поэтому обычный
    SetForegroundWindow срабатывает без обходов foreground lock, в отличие
    от browser.bring_to_front, который зовётся из фонового потока без
    свежего пользовательского ввода). Иначе — новый поток с Tk-окном."""
    hwnd = win32gui.FindWindow(None, _TITLE)
    if hwnd:
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.SetForegroundWindow(hwnd)
        return
    threading.Thread(target=_run_window, args=(history_path,), daemon=True).start()
```

- [ ] **Step 6: Убедиться, что тесты проходят**

Run: `.venv/Scripts/python.exe -m pytest tests/test_history_viewer.py -v`
Expected: PASS (7 тестов).

- [ ] **Step 7: Подключить в `johnny/tray.py`**

Найти функцию `open_history` (около строки 114) и заменить её тело:

```python
    def open_history(_icon, _item) -> None:
        import os

        if not _HISTORY.exists():
            _HISTORY.write_text("", encoding="utf-8")
        os.startfile(str(_HISTORY))  # type: ignore[attr-defined]
```

на:

```python
    def open_history(_icon, _item) -> None:
        from . import history_viewer

        if not _HISTORY.exists():
            _HISTORY.write_text("", encoding="utf-8")
        history_viewer.open_or_focus(_HISTORY)
```

- [ ] **Step 8: Прогнать весь набор тестов**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: все тесты зелёные. Было 505 (на начало этой итерации), плюс новые тесты по задачам: 1 (Task 1) + 7 (Task 2) + 5 (Task 3) + 11 (Task 4) + 2 (Task 5) + 2 (Task 6) + 7 (Task 7) + 7 (Task 8) = 42 → должно стать 547.

- [ ] **Step 9: Живая проверка (только пользователь, не автотест)**

Перезапустить Джони (Выход в трее → ярлык, или `taskkill` по `pythonw *johnny_tray*` + запуск заново — см. память проекта: запущенный трей держит старый код). Кликнуть «История команд» — должно открыться окно, не Notepad. Сказать любую команду — новая строка должна появиться в окне САМА, без переоткрытия. Кликнуть «История команд» второй раз, пока окно уже открыто, — окно должно подняться поверх, а не задублироваться.

- [ ] **Step 10: Commit**

```bash
git add johnny/history_viewer.py johnny/tray.py tests/test_history_viewer.py
git commit -m "feat: живое окно истории команд в трее вместо Notepad"
```

---

## Финальная проверка всей итерации

- [ ] Run: `.venv/Scripts/python.exe -m pytest -q`
  Expected: все тесты зелёные, без исключений.
- [ ] Живая проверка пользователем (в дополнение к Step 9 Task 8): «Джони, напечатай привет как дела» печатает в сфокусированное поле; «Джони, запомни что у меня стим-аккаунт art_vol_teror» → «Джони, что ты помнишь» проговаривает факт; «Джони, забудь про стим-аккаунт» удаляет его; разговорный ответ с вопросом Джони → «Джони, да, давай» продолжает тему, а не начинает новый непонятный разговор.
