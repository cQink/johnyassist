# Устойчивое распознавание команд — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Джони перестаёт терять команды из-за ослышек Whisper: шаблонные команды получают нечёткое совпадение, опасные команды защищаются от угадывания, а эхо собственной подсказки больше не выдаётся за команду.

**Architecture:** Лестница совпадений в `route()`: точное → шаблонный fuzzy → Claude-корректор. Whisper биасится списком ключевых слов вместо целых фраз. Распознаватель фильтрует эхо собственного `initial_prompt`. Границы модулей не меняются, `route()` остаётся единственной точкой принятия решения.

**Tech Stack:** Python 3.14, faster-whisper, difflib (стандартная библиотека), pytest. Новых зависимостей нет.

**Спека:** `docs/superpowers/specs/2026-07-25-recognition-hardening-design.md`

## Global Constraints

- Интерпретатор: `.\.venv\Scripts\python.exe` (основной venv, НЕ `.venv-xtts`).
- Прогон тестов: `.\.venv\Scripts\python.exe -m pytest -q` — на старте плана 106 тестов зелёные, ни один не должен упасть.
- Новых зависимостей не добавлять: только стандартная библиотека и то, что уже в `requirements.txt`.
- Комментарии и docstring — по-русски, как во всём проекте.
- Нормализация текста везде проходит через `router._normalize` (нижний регистр, ё→е, хвостовая пунктуация, схлопывание пробелов).
- Порог для команд без шаблона остаётся **0.82** (`_FUZZY_THRESHOLD`) — он проверен на живых кейсах, менять его нельзя.
- Порог для шаблонных команд — **0.72** (`_TEMPLATE_THRESHOLD`).
- Эхо подсказки ловится при длине **от 4 слов** (`_ECHO_MIN_WORDS`) — на всех 164 командах конфига это даёт 0 ложных срабатываний.
- Ветка работы: `feature/recognition-hardening` от `master`.

---

### Task 1: Фильтр эха промпта

Живой баг: пользователь сказал «пауза», а Whisper вернул «Голосовые команды на русском» — начало собственного `initial_prompt`. Распознаватель обязан узнавать свою подсказку и не выдавать её наружу.

**Files:**
- Modify: `johnny/recognizer.py`
- Test: `tests/test_recognizer_echo.py` (создать)

**Interfaces:**
- Consumes: ничего из других задач.
- Produces: `recognizer.is_prompt_echo(text: str, prompt: str, min_words: int = 4) -> bool` — чистая функция, используется в `Recognizer.listen_command`.

- [ ] **Step 1: Создать ветку**

```bash
git checkout -b feature/recognition-hardening
```

- [ ] **Step 2: Написать падающий тест**

Создать `tests/test_recognizer_echo.py`:

```python
from johnny.recognizer import is_prompt_echo

PROMPT = (
    "Голосовые команды на русском: открой ютуб, запусти доту, зайди на смурф. "
    "дота апекс спотифай импульс"
)


def test_prompt_beginning_is_echo():
    # Ровно то, что Whisper вернул вместо «пауза» 2026-07-26 02:04.
    assert is_prompt_echo("Голосовые команды на русском", PROMPT) is True


def test_vocabulary_slice_is_echo():
    assert is_prompt_echo("дота апекс спотифай импульс", PROMPT) is True


def test_short_command_is_not_echo():
    # 2 слова: короче порога, хоть и встречается в подсказке.
    assert is_prompt_echo("открой ютуб", PROMPT) is False


def test_three_words_from_prompt_are_not_echo():
    # Порог 4 слова: «зайди на смурф» — настоящая команда, не трогаем.
    assert is_prompt_echo("зайди на смурф", PROMPT) is False


def test_real_command_is_not_echo():
    assert is_prompt_echo("поставь на паузу пожалуйста", PROMPT) is False


def test_case_and_yo_are_ignored():
    assert is_prompt_echo("ГОЛОСОВЫЕ КОМАНДЫ НА РУССКОМ", PROMPT) is True


def test_words_out_of_order_are_not_echo():
    # Не непрерывный кусок подсказки — значит настоящая речь.
    assert is_prompt_echo("на русском голосовые команды", PROMPT) is False


def test_empty_text_is_not_echo():
    assert is_prompt_echo("", PROMPT) is False
```

- [ ] **Step 3: Прогнать тест, убедиться что падает**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_recognizer_echo.py -q`
Expected: FAIL — `ImportError: cannot import name 'is_prompt_echo'`

- [ ] **Step 4: Реализовать функцию**

В `johnny/recognizer.py` после блока `import numpy as np ... from faster_whisper import WhisperModel` добавить `import re` в шапку файла (к существующим `import glob, os, sys`) и `import logging`, затем после `_BASE_PROMPT` вставить:

```python
logger = logging.getLogger(__name__)

# Сколько слов подряд из подсказки считаем эхом. 4 — проверено на всех 164
# командах конфига: ни одна не отсеивается ложно (при 3 отсеивались «зайди
# на йети», «найди на твиче *» и др.).
_ECHO_MIN_WORDS = 4


def _words(text: str) -> list[str]:
    """Слова в нижнем регистре, ё→е, без пунктуации."""
    return re.findall(r"[a-zа-я0-9]+", text.lower().replace("ё", "е"))


def is_prompt_echo(text: str, prompt: str, min_words: int = _ECHO_MIN_WORDS) -> bool:
    """Текст целиком — непрерывный кусок подсказки длиной от min_words слов?

    Whisper держит initial_prompt в контексте как начало текста и на невнятном
    аудио просто продолжает его вместо транскрипции («пауза» → «Голосовые
    команды на русском»). Метрики уверенности такое не отличают от тихой речи,
    поэтому ловим детерминированно: свою же подсказку наружу не выдаём.
    """
    said = _words(text)
    if len(said) < min_words:
        return False
    known = _words(prompt)
    return any(
        known[i : i + len(said)] == said for i in range(len(known) - len(said) + 1)
    )
```

- [ ] **Step 5: Прогнать тест, убедиться что проходит**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_recognizer_echo.py -q`
Expected: PASS, 8 passed

- [ ] **Step 6: Подключить фильтр в listen_command**

В `johnny/recognizer.py` заменить последнюю строку метода `listen_command`:

```python
        return " ".join(seg.text.strip() for seg in segments).strip()
```

на:

```python
        text = " ".join(seg.text.strip() for seg in segments).strip()
        if is_prompt_echo(text, self._prompt):
            # Whisper продолжил подсказку вместо расшифровки — считаем, что не
            # расслышали, иначе Джони выполнит собственный словарь как команду.
            logger.warning("Отброшено эхо подсказки: %r", text)
            return ""
        return text
```

- [ ] **Step 7: Прогнать весь набор**

Run: `.\.venv\Scripts\python.exe -m pytest -q`
Expected: PASS, 114 passed

- [ ] **Step 8: Коммит**

```bash
git add johnny/recognizer.py tests/test_recognizer_echo.py
git commit -m "fix: не выдавать эхо initial_prompt за распознанную команду"
```

---

### Task 2: Защита опасных команд от угадывания

«включи компьютер» похоже на «выключи компьютер» на 0.97 — то есть уже сегодня существующий нечёткий матчинг может выключить ПК по ошибке. Опасные команды должны требовать точного совпадения.

**Files:**
- Modify: `johnny/router.py`
- Test: `tests/test_router.py`

**Interfaces:**
- Consumes: ничего из других задач.
- Produces: `router._is_unsafe(rule: CommandRule) -> bool` — используется всеми ветками нечёткого матчинга (в т.ч. шаблонной из Task 4).

- [ ] **Step 1: Написать падающий тест**

Дописать в конец `tests/test_router.py`:

```python
UNSAFE_COMMANDS = [
    CommandRule("выключи компьютер", "system", "shutdown"),
    CommandRule("перезагрузи компьютер", "system", "restart"),
    CommandRule("усыпи компьютер", "system", "sleep"),
    CommandRule("отмени выключение", "system", "cancel_shutdown"),
]


def test_unsafe_command_needs_exact_match():
    # 0.97 похожести, но выключать компьютер по догадке нельзя.
    assert route("включи компьютер", UNSAFE_COMMANDS) is None


def test_unsafe_command_still_works_on_exact_match():
    assert route("выключи компьютер", UNSAFE_COMMANDS) == RoutedAction(
        "system", "shutdown"
    )


def test_cancel_shutdown_is_not_unsafe():
    # Отмена выключения безопасна — её угадывать можно.
    assert route("отмени выключения", UNSAFE_COMMANDS) == RoutedAction(
        "system", "cancel_shutdown"
    )
```

- [ ] **Step 2: Прогнать тест, убедиться что падает**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_router.py -q -k unsafe`
Expected: FAIL — `test_unsafe_command_needs_exact_match`: получен `RoutedAction("system", "shutdown")` вместо `None`

- [ ] **Step 3: Реализовать защиту**

В `johnny/router.py` после `_FUZZY_THRESHOLD` добавить:

```python
# Разрушительные действия: угадывать их нельзя. «включи компьютер» похоже на
# «выключи компьютер» на 0.97 — никакой порог их не разведёт, поэтому такие
# правила участвуют ТОЛЬКО в точном совпадении.
_UNSAFE = frozenset({"shutdown", "restart", "sleep"})


def _is_unsafe(rule: CommandRule) -> bool:
    return rule.action == "system" and rule.template in _UNSAFE
```

В функции `_fuzzy_match` заменить тело цикла:

```python
    for rule in commands:
        if "*" in rule.pattern:
            continue
```

на:

```python
    for rule in commands:
        if "*" in rule.pattern or _is_unsafe(rule):
            continue
```

- [ ] **Step 4: Прогнать тесты**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_router.py -q`
Expected: PASS

- [ ] **Step 5: Прогнать весь набор**

Run: `.\.venv\Scripts\python.exe -m pytest -q`
Expected: PASS, 117 passed

- [ ] **Step 6: Коммит**

```bash
git add johnny/router.py tests/test_router.py
git commit -m "fix: разрушительные команды только по точному совпадению"
```

---

### Task 3: Числительные словами → цифрами

Whisper на коротких фразах пишет число то цифрой, то словом. «громкость пять» сейчас не работает вообще.

**Files:**
- Modify: `johnny/router.py`
- Test: `tests/test_router.py`

**Interfaces:**
- Consumes: ничего из других задач.
- Produces: `_normalize(text)` дополнительно переводит количественные числительные 0–100 в цифры. Порядковые не трогает.

- [ ] **Step 1: Написать падающий тест**

Дописать в конец `tests/test_router.py`:

```python
NUMBER_COMMANDS = [
    CommandRule("громкость *", "set_volume", "{0}"),
    CommandRule("включи первое видео", "browser_click", "1"),
    CommandRule("на второй монитор", "move_window", "2"),
]


def test_number_word_becomes_digit():
    assert route("громкость пять", NUMBER_COMMANDS) == RoutedAction("set_volume", "5")


def test_compound_number_word():
    assert route("громкость двадцать пять", NUMBER_COMMANDS) == RoutedAction(
        "set_volume", "25"
    )


def test_round_tens_number_word():
    assert route("громкость пятьдесят", NUMBER_COMMANDS) == RoutedAction(
        "set_volume", "50"
    )


def test_hundred_number_word():
    assert route("громкость сто", NUMBER_COMMANDS) == RoutedAction("set_volume", "100")


def test_ordinal_words_are_untouched():
    # «первое»/«второй» — порядковые, их перевод сломал бы эти команды.
    assert route("включи первое видео", NUMBER_COMMANDS) == RoutedAction(
        "browser_click", "1"
    )
    assert route("на второй монитор", NUMBER_COMMANDS) == RoutedAction(
        "move_window", "2"
    )
```

- [ ] **Step 2: Прогнать тест, убедиться что падает**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_router.py -q -k number`
Expected: FAIL — `test_number_word_becomes_digit`: получен `None`

- [ ] **Step 3: Реализовать перевод числительных**

В `johnny/router.py` перед функцией `_normalize` добавить:

```python
# Количественные числительные 0–100. Порядковых («первое», «второй») здесь
# сознательно НЕТ: их перевод сломал бы команды вроде «включи первое видео».
_UNITS = {
    "ноль": 0, "один": 1, "одна": 1, "два": 2, "две": 2, "три": 3, "четыре": 4,
    "пять": 5, "шесть": 6, "семь": 7, "восемь": 8, "девять": 9, "десять": 10,
    "одиннадцать": 11, "двенадцать": 12, "тринадцать": 13, "четырнадцать": 14,
    "пятнадцать": 15, "шестнадцать": 16, "семнадцать": 17, "восемнадцать": 18,
    "девятнадцать": 19,
}
_TENS = {
    "двадцать": 20, "тридцать": 30, "сорок": 40, "пятьдесят": 50,
    "шестьдесят": 60, "семьдесят": 70, "восемьдесят": 80, "девяносто": 90,
    "сто": 100,
}


def _words_to_digits(text: str) -> str:
    """«двадцать пять» → «25», «пять» → «5». Десятки склеиваются с единицами."""
    words = text.split()
    out: list[str] = []
    i = 0
    while i < len(words):
        word = words[i]
        if word in _TENS:
            value = _TENS[word]
            nxt = words[i + 1] if i + 1 < len(words) else ""
            if value < 100 and nxt in _UNITS and 1 <= _UNITS[nxt] <= 9:
                out.append(str(value + _UNITS[nxt]))
                i += 2
                continue
            out.append(str(value))
        elif word in _UNITS:
            out.append(str(_UNITS[word]))
        else:
            out.append(word)
        i += 1
    return " ".join(out)
```

В `_normalize` добавить вызов перед `return`:

```python
def _normalize(text: str) -> str:
    text = text.lower().strip()
    text = text.replace("ё", "е")             # ё=е: «вперёд»≈«вперед», «всё»≈«все»
    text = re.sub(r"[.,!?;:]+$", "", text)   # убрать хвостовую пунктуацию
    text = re.sub(r"\s+", " ", text)          # схлопнуть пробелы
    return _words_to_digits(text)             # «пять» → «5»
```

- [ ] **Step 4: Прогнать тесты**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_router.py -q`
Expected: PASS

- [ ] **Step 5: Прогнать весь набор**

Run: `.\.venv\Scripts\python.exe -m pytest -q`
Expected: PASS, 122 passed

- [ ] **Step 6: Коммит**

```bash
git add johnny/router.py tests/test_router.py
git commit -m "feat: числительные словами превращаются в цифры"
```

---

### Task 4: Нечёткое совпадение для шаблонных команд

Главная дырка: команды со «\*» полностью выпадают из нечёткого матчинга, поэтому «Стронкость 5» не выполняется, хотя отличается от «громкость \*» одной буквой.

**Files:**
- Modify: `johnny/router.py`
- Test: `tests/test_router.py`

**Interfaces:**
- Consumes: `_is_unsafe` (Task 2), `_normalize` с числительными (Task 3).
- Produces: `route()` возвращает `RoutedAction` для шаблонных команд с ослышкой в литеральной части.

- [ ] **Step 1: Написать падающий тест**

Дописать в конец `tests/test_router.py`:

```python
TEMPLATE_COMMANDS = [
    CommandRule("громкость *", "set_volume", "{0}"),
    CommandRule("открой канал * на твиче", "open_channel", "twitch|{0}"),
    CommandRule("перемотай на *", "browser_seek", "{0}"),
    CommandRule("выключи компьютер", "system", "shutdown"),
]


def test_misheard_head_still_matches_template():
    # Живой случай: Whisper услышал «Стронкость 5» вместо «громкость 5».
    assert route("стронкость 5", TEMPLATE_COMMANDS) == RoutedAction("set_volume", "5")


def test_misheard_head_with_tail():
    assert route("открой канал серега на твоче", TEMPLATE_COMMANDS) == RoutedAction(
        "open_channel", "twitch|серега"
    )


def test_template_without_argument_is_rejected():
    # «громкость» без числа не должна уехать в set_volume с мусором.
    assert route("стронкость", TEMPLATE_COMMANDS) is None


def test_too_different_head_is_rejected():
    # 0.20 похожести — угадывать не по чему, уходит к Claude.
    assert route("академика с 5", TEMPLATE_COMMANDS) is None


def test_exact_match_still_wins():
    assert route("громкость 5", TEMPLATE_COMMANDS) == RoutedAction("set_volume", "5")


def test_multiword_argument_survives():
    assert route("перемотай на 13 42", TEMPLATE_COMMANDS) == RoutedAction(
        "browser_seek", "13 42"
    )
```

- [ ] **Step 2: Прогнать тест, убедиться что падает**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_router.py -q -k template`
Expected: FAIL — `test_misheard_head_still_matches_template`: получен `None`

- [ ] **Step 3: Реализовать шаблонный матчинг**

В `johnny/router.py` после `_UNSAFE`/`_is_unsafe` добавить порог:

```python
# Порог для литеральной части шаблонных команд. Ниже общего 0.82, потому что
# сравниваем короткие куски: «стронкость»≈«громкость» это 0.74.
_TEMPLATE_THRESHOLD = 0.72
```

Заменить `_fuzzy_match` целиком на две функции:

```python
def _fuzzy_match(norm: str, commands: list[CommandRule]) -> tuple[float, RoutedAction] | None:
    """Ближайшая команда БЕЗ шаблона (фраза целиком), если Whisper услышал криво."""
    best = None
    best_ratio = _FUZZY_THRESHOLD
    for rule in commands:
        if "*" in rule.pattern or _is_unsafe(rule):
            continue
        ratio = SequenceMatcher(None, norm, _normalize(rule.pattern)).ratio()
        if ratio >= best_ratio:
            best_ratio = ratio
            best = rule
    if best is None:
        return None
    return best_ratio, RoutedAction(action=best.action, argument=best.template)


def _split_pattern(pattern: str) -> tuple[list[str], list[str]] | None:
    """«открой канал * на твиче» → (['открой','канал'], ['на','твиче']).

    None, если звёздочек не ровно одна: при нескольких слотах границы
    переменных частей не определить, такие шаблоны пропускаем.
    """
    norm = _normalize(pattern)
    if norm.count("*") != 1:
        return None
    head, tail = norm.split("*")
    return head.split(), tail.split()


def _part_ratio(said: list[str], expected: list[str]) -> tuple[float, int]:
    """Похожесть куска фразы на литерал шаблона + вес (длина литерала)."""
    if not expected:
        return 1.0, 0
    text = " ".join(expected)
    return SequenceMatcher(None, " ".join(said), text).ratio(), len(text)


def _fuzzy_match_template(
    norm: str, commands: list[CommandRule]
) -> tuple[float, RoutedAction] | None:
    """Ближайшая ШАБЛОННАЯ команда: сравниваем только литералы, слот — аргумент."""
    best = None
    best_ratio = _TEMPLATE_THRESHOLD
    words = norm.split()
    for rule in commands:
        if _is_unsafe(rule):
            continue
        parts = _split_pattern(rule.pattern)
        if parts is None:
            continue
        head, tail = parts
        if len(words) < len(head) + len(tail) + 1:  # аргументу нужно хотя бы слово
            continue
        argument = " ".join(words[len(head) : len(words) - len(tail)])
        if not argument:
            continue
        r_head, w_head = _part_ratio(words[: len(head)], head)
        r_tail, w_tail = _part_ratio(words[len(words) - len(tail) :] if tail else [], tail)
        weight = w_head + w_tail
        if not weight:
            continue
        ratio = (r_head * w_head + r_tail * w_tail) / weight
        if ratio >= best_ratio:
            best_ratio = ratio
            best = (rule, argument)
    if best is None:
        return None
    rule, argument = best
    return best_ratio, RoutedAction(action=rule.action, argument=rule.template.format(argument))
```

Заменить `route` целиком:

```python
def route(text: str, commands: list[CommandRule]) -> RoutedAction | None:
    norm = _normalize(text)
    # 1. Точное совпадение (в т.ч. шаблоны со «*»).
    for rule in commands:
        match = _pattern_to_regex(rule.pattern).match(norm)
        if match:
            argument = rule.template.format(*match.groups())
            return RoutedAction(action=rule.action, argument=argument)
    # 2. Нечёткое: и целые фразы, и шаблоны. Берём совпадение с большей похожестью.
    candidates = [
        found
        for found in (_fuzzy_match(norm, commands), _fuzzy_match_template(norm, commands))
        if found is not None
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda pair: pair[0])[1]
```

- [ ] **Step 4: Прогнать тесты**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_router.py -q`
Expected: PASS

- [ ] **Step 5: Прогнать весь набор**

Run: `.\.venv\Scripts\python.exe -m pytest -q`
Expected: PASS, 128 passed

- [ ] **Step 6: Проверить на живом конфиге**

Создать временный файл `check_live.py` в корне проекта:

```python
# -*- coding: utf-8 -*-
"""Прогон реальных ослышек по настоящему commands.yaml."""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from johnny.config import load_config
from johnny.router import route

cfg = load_config("config")
CASES = [
    ("стронкость 5", "set_volume"),
    ("стронкость ноль", "set_volume"),
    ("громкость 5", "set_volume"),
    ("зделай громче", "system"),
    ("поставь на пузу", "system"),
    ("включи компьютер", None),
    ("выключи компьютер", "system"),
    ("расскажи анекдот", None),
]
for text, expected in CASES:
    got = route(text, cfg.commands)
    action = got.action if got else None
    mark = "OK " if action == expected else "!! "
    print(f"{mark}{text!r:<24} → {action!r} (ждали {expected!r})")
```

Run: `.\.venv\Scripts\python.exe check_live.py`
Expected: во всех строках `OK`

- [ ] **Step 7: Удалить временный файл и закоммитить**

```bash
rm check_live.py
git add johnny/router.py tests/test_router.py
git commit -m "feat: нечёткое совпадение для шаблонных команд"
```

---

### Task 5: Словарь ключевых слов для Whisper вместо целых фраз

`initial_prompt` обрезается на 224 токенах. Целые фразы команд туда не влезут и вытеснят имена каналов и игр — а именно на именах Whisper ошибается чаще всего.

**Files:**
- Modify: `johnny/recognizer.py`
- Modify: `johnny/app.py:49-52`
- Modify: `johnny/tray.py:48,53,55`
- Test: `tests/test_recognizer_vocab.py`

**Interfaces:**
- Consumes: `CommandRule` из `johnny.config`.
- Produces: `build_vocabulary(apps: dict, channels: dict, commands: list[CommandRule] | None = None) -> str` — обратно совместима, третий аргумент необязателен.

- [ ] **Step 1: Написать падающий тест**

Дописать в конец `tests/test_recognizer_vocab.py`:

```python
from johnny.config import CommandRule
from johnny.recognizer import _MAX_VOCAB_WORDS


def test_vocabulary_includes_command_keywords():
    commands = [
        CommandRule("громкость *", "set_volume", "{0}"),
        CommandRule("сделай скриншот", "system", "screenshot"),
    ]
    vocab = build_vocabulary({}, {}, commands)
    assert "громкость" in vocab
    assert "скриншот" in vocab


def test_vocabulary_drops_stopwords_and_star():
    commands = [CommandRule("открой канал * на твиче", "open_channel", "twitch|{0}")]
    words = build_vocabulary({}, {}, commands).split()
    assert "*" not in words
    assert "на" not in words
    assert "твиче" in words


def test_proper_names_come_before_command_words():
    commands = [CommandRule("громкость *", "set_volume", "{0}")]
    words = build_vocabulary({"дота": "x"}, {}, commands).split()
    assert words.index("дота") < words.index("громкость")


def test_vocabulary_respects_word_budget():
    commands = [
        CommandRule(f"команда{i} слово{i}", "system", "x") for i in range(200)
    ]
    words = build_vocabulary({}, {}, commands).split()
    assert len(words) <= _MAX_VOCAB_WORDS
```

- [ ] **Step 2: Прогнать тест, убедиться что падает**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_recognizer_vocab.py -q`
Expected: FAIL — `ImportError: cannot import name '_MAX_VOCAB_WORDS'`

- [ ] **Step 3: Реализовать сбор ключевых слов**

В `johnny/recognizer.py` заменить `_BASE_PROMPT` и `build_vocabulary` на:

```python
# Короткая шапка + словарь имён. Целых командных фраз здесь СОЗНАТЕЛЬНО нет:
# initial_prompt обрезается на 224 токенах, а фразы вытеснили бы имена
# собственные — именно на них Whisper ошибается чаще всего.
_BASE_PROMPT = "Русские голосовые команды."

# Бюджет слов словаря. У Whisper на подсказку ~224 токена, русское слово — это
# в среднем 3 токена, поэтому берём с запасом.
_MAX_VOCAB_WORDS = 60

# Служебные слова: биасить их бессмысленно, они и так известны модели.
_STOPWORDS = frozenset({
    "на", "в", "во", "и", "с", "со", "по", "за", "к", "у", "о", "об", "от",
    "до", "из", "мне", "мой", "моя", "это", "все", "всё", "как", "что",
    "пожалуйста", "давай", "ка",
})


def build_vocabulary(apps: dict, channels: dict, commands=None) -> str:
    """Словарь имён и ключевых слов для initial_prompt.

    Порядок важен: сначала имена собственные (игры, программы, каналы) — они
    уникальны и Whisper ошибается на них чаще; затем значимые слова команд.
    При переполнении бюджета обрезается хвост, то есть слова команд, а имена
    остаются.
    """
    words: list[str] = list(apps.keys())
    for info in channels.values():
        words += [str(a) for a in info.get("aliases", [])]
    for rule in commands or []:
        for word in rule.pattern.replace("*", " ").split():
            if word not in _STOPWORDS and len(word) > 2:
                words.append(word)
    unique = list(dict.fromkeys(words))  # уникальные, порядок сохранён
    return " ".join(unique[:_MAX_VOCAB_WORDS])
```

- [ ] **Step 4: Прогнать тесты распознавателя**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_recognizer_vocab.py tests/test_recognizer_echo.py -q`
Expected: PASS

- [ ] **Step 5: Пробросить команды из app.run**

В `johnny/app.py` заменить строку 49:

```python
    vocabulary = build_vocabulary(config.apps, config.channels)
```

на:

```python
    vocabulary = build_vocabulary(config.apps, config.channels, config.commands)
```

- [ ] **Step 6: Починить трей — он создаёт распознаватель БЕЗ словаря**

`johnny/tray.py:55` вызывает `Recognizer(model, device)` без третьего аргумента,
поэтому в реальном запуске (а Джони запускается именно из трея) имена игр и
каналов в подсказку не попадали вообще. Там же `make_speaker` вызывается без
голоса, из-за чего `tts_voice` из `settings.yaml` игнорируется.

Заменить в `johnny/tray.py` строку 48:

```python
    speaker = make_speaker(config.settings.response_mode)
```

на:

```python
    speaker = make_speaker(config.settings.response_mode, config.settings.tts_voice)
```

Заменить строки 53 и 55:

```python
    from .recognizer import Recognizer

    recognizer = Recognizer(config.settings.whisper_model, config.settings.whisper_device)
```

на:

```python
    from .recognizer import Recognizer, build_vocabulary

    vocabulary = build_vocabulary(config.apps, config.channels, config.commands)
    recognizer = Recognizer(
        config.settings.whisper_model, config.settings.whisper_device, vocabulary
    )
```

- [ ] **Step 6b: Убедиться, что других мест создания распознавателя нет**

Run: `grep -rn "Recognizer(" johnny/ johnny_tray.pyw main.py`
Expected: только `johnny/app.py:50`, `johnny/tray.py` (исправленный) и
`johnny/listener.py` (это `KaldiRecognizer` от Vosk — не трогать).

- [ ] **Step 7: Прогнать весь набор**

Run: `.\.venv\Scripts\python.exe -m pytest -q`
Expected: PASS, 132 passed

- [ ] **Step 8: Коммит**

```bash
git add johnny/recognizer.py johnny/app.py johnny/tray.py tests/test_recognizer_vocab.py
git commit -m "feat: словарь ключевых слов команд в подсказке Whisper"
```

---

### Task 6: Claude-корректор ослышек

Последний рубеж: непонятая фраза уходит к Claude вместе со списком команд, он возвращает исправленную фразу, а выполняет её обычный `route()`.

**Files:**
- Modify: `johnny/claude_brain.py`
- Modify: `johnny/app.py:30`
- Test: `tests/test_claude_brain.py`

**Interfaces:**
- Consumes: `route` из `johnny.router` (Task 4).
- Produces: `interpret(text: str, commands: list[CommandRule] | None = None, run_claude=_run_claude) -> ClaudeResult | None` — третий позиционный аргумент `run_claude` сохраняет своё место, существующие тесты вызывают его по имени.

- [ ] **Step 1: Написать падающий тест**

Дописать в конец `tests/test_claude_brain.py`:

```python
from johnny.config import CommandRule
from johnny.claude_brain import interpret
from johnny.router import RoutedAction

CORRECTOR_COMMANDS = [
    CommandRule("громкость *", "set_volume", "{0}"),
    CommandRule("сверни всё", "system", "minimize_all"),
]


def test_claude_correction_is_rerouted():
    fake = lambda prompt: '{"command": "громкость 5"}'
    result = interpret("академика с 5", CORRECTOR_COMMANDS, fake)
    assert result.routed == RoutedAction("set_volume", "5")


def test_invented_command_is_rejected():
    # Такой команды в конфиге нет — выполнять нечего.
    fake = lambda prompt: '{"command": "станцуй лезгинку"}'
    result = interpret("станцуй", CORRECTOR_COMMANDS, fake)
    assert result.routed is None


def test_action_mode_still_works():
    fake = lambda prompt: '{"action": "open_url", "argument": "pogoda.ru", "reply": "Открываю"}'
    result = interpret("открой сайт погоды", CORRECTOR_COMMANDS, fake)
    assert result.routed == RoutedAction("open_url", "pogoda.ru")
    assert result.reply == "Открываю"


def test_conversation_still_works():
    fake = lambda prompt: "Токио, сэр."
    result = interpret("столица японии", CORRECTOR_COMMANDS, fake)
    assert result.routed is None
    assert result.reply == "Токио, сэр."


def test_commands_are_listed_in_prompt():
    seen = {}

    def fake(prompt):
        seen["prompt"] = prompt
        return "ок"

    interpret("что-то", CORRECTOR_COMMANDS, fake)
    assert "громкость *" in seen["prompt"]
    assert "сверни всё" in seen["prompt"]
```

- [ ] **Step 2: Прогнать тест, убедиться что падает**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_claude_brain.py -q -k "correction or invented"`
Expected: FAIL — `TypeError: interpret() takes 1 positional argument but 3 were given`

Существующие тесты в этом файле зовут `interpret("...", run_claude=fake)` по
имени, поэтому новый второй аргумент `commands` их не ломает.

- [ ] **Step 3: Обновить промпт и interpret**

В `johnny/claude_brain.py` заменить `_PROMPT` на:

```python
_PROMPT = """Ты — голосовой ассистент Джони на компьютере с Windows.
Текст ниже пришёл от распознавания речи и МОГ БЫТЬ ИСКОВЕРКАН.

Пользователь сказал: "{text}"

Вот команды, которые Джони умеет ({count} шт.):
{commands}

1) Если это исковерканная КОМАНДА из списка — ответь ТОЛЬКО JSON с точной
фразой из списка (звёздочку замени тем, что сказал пользователь):
{{"command": "громкость 5"}}

2) Если это команда управления компьютером, которой в списке НЕТ (открыть
произвольный сайт, запустить программу) — ответь ТОЛЬКО JSON:
{{"action": "<open_url|launch_app|system>", "argument": "<url | имя программы | volume_up/volume_down/mute/lock>", "reply": "<короткая фраза для озвучки>"}}

3) Если это ВОПРОС, просьба рассказать, пошутить или просто поговорить —
ответь живой человеческой речью по-русски, 1–3 коротких предложения, БЕЗ JSON
(твой ответ будет прочитан вслух)."""
```

Добавить импорт в шапку файла:

```python
from .router import RoutedAction, route
```

(строка `from .router import RoutedAction` заменяется на эту).

Заменить `interpret` на:

```python
def interpret(text: str, commands=None, run_claude=_run_claude) -> ClaudeResult | None:
    commands = commands or []
    listing = "\n".join(f"- {rule.pattern}" for rule in commands)
    raw = run_claude(_PROMPT.format(text=text, count=len(commands), commands=listing))
    if not raw or not raw.strip():
        return None  # пусто = claude недоступен

    data = _extract_json(raw)
    if data is not None:
        # Режим коррекции: Claude узнал ослышку. Выполняет обычный route —
        # так Claude не может изобрести действие или собрать кривой аргумент.
        corrected = data.get("command")
        if corrected:
            return ClaudeResult(routed=route(corrected, commands), reply=None)
        action = data.get("action")
        reply = data.get("reply") or None
        if action in ("open_url", "launch_app", "system"):
            routed = RoutedAction(action=action, argument=data.get("argument", ""))
            return ClaudeResult(routed=routed, reply=reply)
        if action == "answer":
            return ClaudeResult(routed=None, reply=reply)

    # Не JSON — значит это обычный разговорный ответ, озвучиваем его как есть.
    return ClaudeResult(routed=None, reply=raw.strip())
```

- [ ] **Step 4: Прогнать тесты**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_claude_brain.py -q`
Expected: PASS

- [ ] **Step 5: Пробросить команды из handle_command**

В `johnny/app.py` заменить строку 30:

```python
        claude = interpret(text)
```

на:

```python
        claude = interpret(text, config.commands)
```

- [ ] **Step 6: Прогнать весь набор**

Run: `.\.venv\Scripts\python.exe -m pytest -q`
Expected: PASS, 137 passed

- [ ] **Step 7: Живая проверка с настоящим Claude**

Создать временный файл `check_claude.py` в корне проекта:

```python
# -*- coding: utf-8 -*-
"""Живая проверка корректора: настоящий claude -p, настоящий commands.yaml."""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from johnny.config import load_config
from johnny.claude_brain import interpret

cfg = load_config("config")
for phrase in ["академика с 5", "расскажи анекдот"]:
    result = interpret(phrase, cfg.commands)
    print(f"{phrase!r} → routed={result.routed if result else None} reply={(result.reply if result else None)!r:.60}")
```

Run: `.\.venv\Scripts\python.exe check_claude.py`
Expected: для «академика с 5» — `routed=RoutedAction(action='set_volume', argument='5')`; для анекдота — `routed=None` и непустой `reply`. Ответ занимает 5–15 секунд на фразу.

- [ ] **Step 8: Удалить временный файл и закоммитить**

```bash
rm check_claude.py
git add johnny/claude_brain.py johnny/app.py tests/test_claude_brain.py
git commit -m "feat: Claude исправляет ослышки по списку команд"
```

---

### Task 7: Журнал способа срабатывания

Чтобы через неделю подкручивать пороги по фактам, а не на глаз: в `history.log` пишется, как сработало — точно, похоже (с числом) или через Claude.

**Files:**
- Modify: `johnny/router.py`
- Modify: `johnny/history.py`
- Modify: `johnny/app.py:21-40`
- Modify: `johnny/controller.py:42-46`
- Test: `tests/test_history.py` (создать)
- Test: `tests/test_router.py`

**Interfaces:**
- Consumes: `route` (Task 4), `interpret` (Task 6).
- Produces: `RoutedAction.via: str` (не участвует в сравнении), `history.add(text: str, via: str = "") -> None`, `handle_command(...) -> str` возвращает способ.

- [ ] **Step 1: Написать падающий тест**

Создать `tests/test_history.py`:

```python
from johnny import history


def test_add_writes_text_and_via(tmp_path, monkeypatch):
    log = tmp_path / "history.log"
    monkeypatch.setattr(history, "_HISTORY_FILE", log)
    history.add("громкость 5", "похоже (0.74)")
    line = log.read_text(encoding="utf-8")
    assert "громкость 5" in line
    assert "похоже (0.74)" in line


def test_add_without_via_is_backwards_compatible(tmp_path, monkeypatch):
    log = tmp_path / "history.log"
    monkeypatch.setattr(history, "_HISTORY_FILE", log)
    history.add("открой ютуб")
    assert "открой ютуб" in log.read_text(encoding="utf-8")


def test_empty_text_is_marked(tmp_path, monkeypatch):
    log = tmp_path / "history.log"
    monkeypatch.setattr(history, "_HISTORY_FILE", log)
    history.add("")
    assert "(пусто)" in log.read_text(encoding="utf-8")
```

Дописать в конец `tests/test_router.py`:

```python
def test_exact_match_reports_via():
    assert route("открой ютуб", COMMANDS).via == "точно"


def test_fuzzy_match_reports_ratio():
    assert route("зделай громче", COMMANDS).via.startswith("похоже")


def test_via_does_not_break_equality():
    # Сравнение действий не должно зависеть от способа совпадения.
    assert route("зделай громче", COMMANDS) == RoutedAction("system", "volume_up")
```

- [ ] **Step 2: Прогнать тесты, убедиться что падают**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_history.py tests/test_router.py -q -k "via or history"`
Expected: FAIL — `TypeError: add() takes 1 positional argument but 2 were given` и `AttributeError: 'RoutedAction' object has no attribute 'via'`

- [ ] **Step 3: Добавить поле via в RoutedAction**

В `johnny/router.py` заменить объявление:

```python
@dataclass
class RoutedAction:
    action: str
    argument: str
```

на:

```python
@dataclass
class RoutedAction:
    action: str
    argument: str
    # Как совпало: «точно» / «похоже (0.74)» / «claude». compare=False, чтобы
    # способ не влиял на сравнение действий в тестах и в коде.
    via: str = field(default="точно", compare=False)
```

и добавить импорт в шапку:

```python
from dataclasses import dataclass, field
```

- [ ] **Step 4: Проставить via в местах создания**

В `_fuzzy_match` заменить возврат:

```python
    return best_ratio, RoutedAction(action=best.action, argument=best.template)
```

на:

```python
    return best_ratio, RoutedAction(
        action=best.action, argument=best.template, via=f"похоже ({best_ratio:.2f})"
    )
```

В `_fuzzy_match_template` заменить возврат:

```python
    return best_ratio, RoutedAction(action=rule.action, argument=rule.template.format(argument))
```

на:

```python
    return best_ratio, RoutedAction(
        action=rule.action,
        argument=rule.template.format(argument),
        via=f"похоже ({best_ratio:.2f})",
    )
```

- [ ] **Step 5: Дописать via в history.add**

В `johnny/history.py` заменить функцию `add`:

```python
def add(text: str, via: str = "") -> None:
    """Дописать одну строку в чистую историю распознаваний (со временем).

    via — как сработало: «точно» / «похоже (0.74)» / «claude» / «не понял».
    Нужен, чтобы потом подкручивать пороги по фактам, а не на глаз.
    """
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    shown = text if text.strip() else "(пусто)"
    line = f"{stamp}  {shown}" + (f"   [{via}]\n" if via else "\n")
    with open(_HISTORY_FILE, "a", encoding="utf-8") as f:
        f.write(line)
```

- [ ] **Step 6: Возвращать способ из handle_command**

В `johnny/app.py` заменить функцию `handle_command` целиком:

```python
def handle_command(text: str, config, speaker) -> str:
    """Выполнить команду. Возвращает способ совпадения — для истории."""
    if not text.strip():
        speaker.say("Не расслышал")
        return "пусто"
    try:
        routed = route(text, config.commands)
        if routed is not None:
            _respond(speaker, execute(routed, config.apps, config.channels))
            return routed.via
        claude = interpret(text, config.commands)
        if claude is None:
            speaker.say("Не понял команду")
            return "claude недоступен"
        if claude.routed is not None:
            _respond(speaker, execute(claude.routed, config.apps, config.channels), claude.reply)
        else:
            speaker.say(claude.reply or "Готово")
        return "claude"
    except Exception:
        logger.exception("Ошибка при выполнении команды")
        speaker.say("Не смог выполнить команду")
        return "ошибка"
```

- [ ] **Step 7: Передать способ в историю**

В `johnny/controller.py` заменить строки 42-46 метода `run_one_cycle`:

```python
        text = recognizer.listen_command()
        self.last_command = text
        history.add(text)
        logger.info("Распознано: %r", text)
        handle_command(text, self.config, self.speaker)
```

на:

```python
        text = recognizer.listen_command()
        self.last_command = text
        logger.info("Распознано: %r", text)
        via = handle_command(text, self.config, self.speaker)
        history.add(text, via)
```

- [ ] **Step 8: Прогнать весь набор**

Run: `.\.venv\Scripts\python.exe -m pytest -q`
Expected: PASS, 143 passed

- [ ] **Step 9: Коммит**

```bash
git add johnny/router.py johnny/history.py johnny/app.py johnny/controller.py tests/test_history.py tests/test_router.py
git commit -m "feat: журнал способа срабатывания в истории команд"
```

---

### Task 8: Живая проверка и слияние

**Files:**
- Modify: нет (только проверка и слияние)

**Interfaces:**
- Consumes: всё выше.
- Produces: ветка влита в `master`.

- [ ] **Step 1: Прогнать весь набор начисто**

Run: `.\.venv\Scripts\python.exe -m pytest -q`
Expected: PASS, 143 passed

- [ ] **Step 2: Проверить, что Джони стартует**

Run: `.\.venv\Scripts\python.exe -c "from johnny.config import load_config; from johnny.recognizer import build_vocabulary; c=load_config('config'); v=build_vocabulary(c.apps,c.channels,c.commands); print(len(v.split()), 'слов в словаре:'); print(v)"`
Expected: не больше 60 слов, в начале имена игр и каналов, дальше слова команд.

- [ ] **Step 3: Живой прогон голосом**

Перезапустить Джони (Выход в трее → ярлык «Джони») и проверить голосом:
- «Джони» → «громкость 5» — должно ставить 50%.
- «Джони» → «громкость пять» — то же самое.
- «Джони» → «включи компьютер» — НЕ должно выключать (уйдёт к Claude или «не понял»).
- Заглянуть в `history.log`: у строк появились пометки `[точно]` / `[похоже (0.74)]` / `[claude]`.

- [ ] **Step 4: Влить в master**

```bash
git checkout master
git merge --no-ff feature/recognition-hardening -m "Merge feature/recognition-hardening"
```

- [ ] **Step 5: Обновить память проекта**

Дописать в `C:\Users\Admin\.claude\projects\D--assistent\memory\johnny-voice-assistant.md` итерацию: шаблонный fuzzy (порог 0.72), защита опасных команд, числительные словами, фильтр эха промпта, словарь ключевых слов с бюджетом 60 слов, Claude-корректор, пометки способа в истории. Указать итоговое число тестов.
