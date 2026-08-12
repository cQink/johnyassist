# Помощь с кодом голосом (пункт 2: прокси промптов к внешним ИИ)

> **Для исполнителя:** выполнять по задачам, сверху вниз. Шаги отмечены
> чекбоксами. В проекте **нет git** (`Is a git repository: false`), поэтому
> вместо коммитов в конце каждой задачи — прогон всего набора тестов и отметка
> в `current_work_plan.md`. Это не послабление: набор в 1058 тестов и есть
> здешняя защита от регрессий.

**Цель:** выделил код в редакторе → сказал Джони, что с ним сделать → услышал
объяснение или получил переписанный код в буфере обмена, готовый к вставке.

**Архитектура:** мостом служит **буфер обмена**, а не сервер и не расширение.
Джони читает то, что человек скопировал, маскирует секреты, отправляет в уже
существующую цепочку моделей (`brain.make_providers`) и кладёт ответ обратно
в буфер либо произносит вслух.

**Стек:** `win32clipboard` (уже есть в pywin32), существующие `johnny/brain.py`
и реестр действий. Новых зависимостей нет.

---

## Почему НЕ так, как написано в исходной спеке

Спека `details/add_ai_prompt_proxy_support_in_editors.md` предписывает
FastAPI-прокси, расширение для VS Code и auth. Берём **требование**, а не
предписанный инструмент — тем же движением, каким в этом проекте уже заменили
MoonMonet Translator на argostranslate (Tauri-приложение без программного API →
взяли требование «локальная обработка»).

| Из спеки | Почему не делаем | Что вместо |
|---|---|---|
| Расширение для VS Code | **На машине владельца нет ни одной IDE** (проверено: ни VS Code, ни JetBrains). Расширение нельзя ни собрать, ни проверить. Обещать невыполнимое хуже, чем не иметь команды — тот же вывод, что по TinEye | Буфер обмена: работает в ЛЮБОМ редакторе, и в поле браузера тоже |
| FastAPI-прокси + auth | Решает сетевую задачу, которой нет: Джони и редактор — один компьютер и один пользователь. Auth охранял бы localhost от самого себя | Прямой вызов `make_providers()` внутри процесса |
| «безопасный endpoint» | — | Безопасность переезжает туда, где риск реален: маскирование секретов и явное согласие |

Требования спеки, которые ОСТАЮТСЯ и выполняются: приём контекста файла и
промпта, применение результата к файлу (вручную — вставкой), ограничение
размера, **маскирование секретов в промптах**.

## Ключевое решение: результат НЕ печатается сам

Ответ модели кладётся в буфер, вставляет человек (Ctrl+V). Причины:

1. Роадмап автономности §4: «Ввод текста, клавиатура — L1, подтверждение
   ВСЕГДА».
2. Печать поверх выделенного кода необратима: выделение заменяется, и если
   модель ответила мусором, исходник потерян.
3. `keyboard.type_text` шлёт по символу через SendInput. Ответ на 2000 знаков
   печатался бы заметно долго и перемешался бы с любым нажатием человека.

Вставка руками — это и есть «ручное применение», разрешённое спекой, и человек
видит результат до того, как он попадёт в файл.

## Global Constraints

- Питон 3.14, Windows. Комментарии и сообщения — по-русски, как во всём проекте.
- Отвечает Джони ГОЛОСОМ: длинные ответы вслух не читаются (см. задачу 4).
- Ни одной новой зависимости в `requirements.txt`.
- Пути: модуль логики → тонкий слой действия → фразы в `config/commands.yaml`.
- Тесты объясняют ПОЧЕМУ, а не пересказывают код (стиль всего `tests/`).
- Прогон: `"D:\Python\python.exe" -m pytest -q --ignore=tests/test_face_index_connector.py`
  (модуль лиц не собирается — на машине нет `cv2`, это было до нас).

---

## Task 1: Буфер обмена

**Files:**
- Create: `johnny/clipboard.py`
- Test: `tests/test_clipboard.py`

**Interfaces:**
- Produces: `read_text() -> str`, `write_text(text: str) -> None`,
  `ClipboardError(Exception)`

Буфер — общий ресурс ОС: его держат открытым другие программы (браузеры,
Office), и `OpenClipboard` штатно падает с «Access denied». Поэтому повторы,
а не одна попытка.

- [ ] **Шаг 1: тест**

```python
"""Буфер обмена: общий ресурс ОС, который постоянно занят кем-то ещё."""

import pytest

import johnny.clipboard as clipboard


class FakeWin32:
    """Подставной win32clipboard: считает попытки открыть буфер."""

    CF_UNICODETEXT = 13

    def __init__(self, text="", fail_times=0, error=Exception("Access denied")):
        self.text = text
        self.fail_times = fail_times
        self.error = error
        self.opens = 0
        self.written = None
        self.closed = 0

    def OpenClipboard(self):
        self.opens += 1
        if self.opens <= self.fail_times:
            raise self.error

    def CloseClipboard(self):
        self.closed += 1

    def GetClipboardData(self, fmt):
        return self.text

    def EmptyClipboard(self):
        pass

    def SetClipboardData(self, fmt, value):
        self.written = value

    def IsClipboardFormatAvailable(self, fmt):
        return True


def test_reads_text():
    fake = FakeWin32(text="print(1)")
    assert clipboard.read_text(api=fake) == "print(1)"


def test_retries_while_another_program_holds_the_clipboard():
    """Буфер держат открытым браузеры и Office. Одна попытка = случайные
    отказы там, где достаточно подождать 50 мс."""
    fake = FakeWin32(text="код", fail_times=2)
    assert clipboard.read_text(api=fake, attempts=5, pause=0.0) == "код"
    assert fake.opens == 3


def test_gives_up_with_a_clear_error():
    fake = FakeWin32(fail_times=99)
    with pytest.raises(clipboard.ClipboardError):
        clipboard.read_text(api=fake, attempts=3, pause=0.0)


def test_always_closes_the_clipboard_even_on_failure():
    """Незакрытый буфер вешает КАЖДОЙ программе Ctrl+C до перезапуска Джони."""
    fake = FakeWin32(text="код")

    def boom(fmt):
        raise RuntimeError("чтение упало")

    fake.GetClipboardData = boom
    with pytest.raises(clipboard.ClipboardError):
        clipboard.read_text(api=fake, attempts=1, pause=0.0)
    assert fake.closed == 1


def test_empty_clipboard_is_empty_string_not_an_error():
    """Пустой буфер — обычное состояние, а не сбой: человек мог забыть скопировать."""
    fake = FakeWin32(text="")
    fake.IsClipboardFormatAvailable = lambda fmt: False
    assert clipboard.read_text(api=fake) == ""


def test_writes_text():
    fake = FakeWin32()
    clipboard.write_text("готово", api=fake)
    assert fake.written == "готово"
```

- [ ] **Шаг 2: убедиться, что тесты падают**

Запуск: `"D:\Python\python.exe" -m pytest tests/test_clipboard.py -q`
Ожидание: `ModuleNotFoundError: No module named 'johnny.clipboard'`

- [ ] **Шаг 3: реализация**

```python
"""Буфер обмена — мост между редактором и Джони.

Именно буфер, а не расширение для редактора: расширение живёт в одном
редакторе, а Ctrl+C работает везде, включая поле в браузере. Подробности
решения — в docs/superpowers/plans/2026-08-09-ai-code-assist.md.

Буфер — ОБЩИЙ ресурс Windows, и это определяет весь код ниже: его держат
открытым другие программы, OpenClipboard штатно падает с «Access denied», а
незакрытый нами буфер ломает Ctrl+C всей системе до перезапуска Джони.
Отсюда повторы и безусловный close в finally.
"""

import time

_ATTEMPTS = 5
_PAUSE = 0.05


class ClipboardError(Exception):
    """Буфер не отдался за отведённые попытки."""


def _api():
    import win32clipboard

    return win32clipboard


def read_text(api=None, attempts: int = _ATTEMPTS, pause: float = _PAUSE) -> str:
    """Текст из буфера. Пустой буфер — пустая строка, а не исключение."""
    api = api or _api()
    last = None
    for attempt in range(attempts):
        try:
            api.OpenClipboard()
        except Exception as exc:
            last = exc
            time.sleep(pause)
            continue
        try:
            if not api.IsClipboardFormatAvailable(api.CF_UNICODETEXT):
                return ""
            return api.GetClipboardData(api.CF_UNICODETEXT) or ""
        except Exception as exc:
            raise ClipboardError(f"не смог прочитать буфер: {exc}") from exc
        finally:
            api.CloseClipboard()
    raise ClipboardError(f"буфер занят другой программой: {last}")


def write_text(text: str, api=None, attempts: int = _ATTEMPTS,
               pause: float = _PAUSE) -> None:
    """Положить текст в буфер, чтобы человек вставил его сам (Ctrl+V)."""
    api = api or _api()
    last = None
    for attempt in range(attempts):
        try:
            api.OpenClipboard()
        except Exception as exc:
            last = exc
            time.sleep(pause)
            continue
        try:
            api.EmptyClipboard()
            api.SetClipboardData(api.CF_UNICODETEXT, text)
            return
        except Exception as exc:
            raise ClipboardError(f"не смог записать в буфер: {exc}") from exc
        finally:
            api.CloseClipboard()
    raise ClipboardError(f"буфер занят другой программой: {last}")
```

- [ ] **Шаг 4: тесты зелёные**

Запуск: `"D:\Python\python.exe" -m pytest tests/test_clipboard.py -q`
Ожидание: `6 passed`

- [ ] **Шаг 5: живая проверка** (буфер нельзя проверить моком до конца)

```bash
"D:\Python\python.exe" -c "import sys; sys.path.insert(0,'.'); from johnny import clipboard; clipboard.write_text('проба Джони'); print(repr(clipboard.read_text()))"
```
Ожидание: `'проба Джони'`

---

## Task 2: Маскирование секретов и ограничение размера

**Files:**
- Create: `johnny/code_prompt.py`
- Test: `tests/test_code_prompt.py`

**Interfaces:**
- Produces: `mask_secrets(text: str) -> tuple[str, int]`,
  `trim(text: str, limit: int) -> tuple[str, bool]`

Это ядро приватности всей задачи: в буфере может лежать целый файл с ключами.
Требование спеки «проверка и маскирование секретов в промптах» реализуется
здесь и нигде больше.

- [ ] **Шаг 1: тест**

```python
"""Маскирование секретов: в буфере может лежать целый файл с ключами.

Перемаскировать безопасно (модель увидит ***), недомаскировать — нет: ключ
уедет на чужой сервер и станет скомпрометированным.
"""

import johnny.code_prompt as code_prompt


def test_masks_assignment_forms():
    text = 'api_key = "gsk_abcdefghijklmnop"'
    masked, count = code_prompt.mask_secrets(text)
    assert "gsk_abcdefghijklmnop" not in masked
    assert count == 1


def test_masks_known_token_prefixes_even_without_assignment():
    """Ключ может лежать голой строкой в списке или в комментарии."""
    text = "заголовки = ['sk-ABCDEFGHIJKLMNOPQRSTUV']"
    masked, count = code_prompt.mask_secrets(text)
    assert "sk-ABCDEFGHIJKLMNOPQRSTUV" not in masked
    assert count == 1


def test_masks_password_and_token_names():
    text = "password: 'ochen-sekretno'\ntoken = 'abcdef123456'"
    masked, count = code_prompt.mask_secrets(text)
    assert "ochen-sekretno" not in masked
    assert "abcdef123456" not in masked
    assert count == 2


def test_keeps_ordinary_code_intact():
    """Перемаскирование ломает смысл кода, и модель ответит не про то."""
    text = "def add(a, b):\n    return a + b\n"
    masked, count = code_prompt.mask_secrets(text)
    assert masked == text
    assert count == 0


def test_short_values_are_not_treated_as_secrets():
    """`key = 'id'` — это не ключ, а обычный код."""
    masked, count = code_prompt.mask_secrets("key = 'id'")
    assert count == 0


def test_count_is_reported_so_johnny_can_say_it_out_loud():
    """Человек обязан знать, что часть кода уехала замазанной: иначе он
    удивится ответу модели про ***."""
    _, count = code_prompt.mask_secrets(
        'api_key = "gsk_aaaaaaaaaaaaaaaa"\ntoken = "bbbbbbbbbbbbbbbb"'
    )
    assert count == 2


def test_trim_cuts_long_text_at_a_line_boundary():
    text = "\n".join(f"строка {i}" for i in range(500))
    cut, was_trimmed = code_prompt.trim(text, 100)
    assert was_trimmed is True
    assert len(cut) <= 100
    assert not cut.endswith("стро")   # не посреди строки


def test_trim_leaves_short_text_alone():
    cut, was_trimmed = code_prompt.trim("print(1)", 1000)
    assert cut == "print(1)"
    assert was_trimmed is False
```

- [ ] **Шаг 2: убедиться, что падает**

Запуск: `"D:\Python\python.exe" -m pytest tests/test_code_prompt.py -q`
Ожидание: `ModuleNotFoundError: No module named 'johnny.code_prompt'`

- [ ] **Шаг 3: реализация**

```python
"""Подготовка кода из буфера к отправке в модель.

ГЛАВНОЕ ЗДЕСЬ — МАСКИРОВАНИЕ. В буфере оказывается то, что человек выделил в
редакторе: запросто целый файл с ключами, паролями и чужими данными. Отправка
в облако необратима — уехавший ключ считается скомпрометированным. Поэтому
перемаскировать безопасно (модель увидит ***), а недомаскировать нет, и все
пороги ниже смещены в сторону «лучше замажем лишнее».

Количество замен возвращается наружу не для статистики: Джони обязан сказать
вслух, что часть кода уехала замазанной, иначе человек не поймёт, почему
модель рассуждает про ***.
"""

import re

# Присваивания вида api_key = "...", password: '...'. Значение от 6 знаков:
# `key = 'id'` — обычный код, а не секрет.
_ASSIGNMENT = re.compile(
    r"""(?ix)
    \b(
        api[_-]?key | apikey | secret | token | password | passwd | pwd |
        auth | credential | private[_-]?key | access[_-]?key
    )
    (\s*[:=]\s*)
    (["']?)
    ([^\s"',;]{6,})
    \3
    """
)

# Известные префиксы ключей: такой ключ узнаётся и без присваивания —
# в списке, в комментарии, в json.
_KNOWN_PREFIX = re.compile(
    r"\b(sk-|gsk_|ghp_|github_pat_|xox[baprs]-|AKIA|AIza)[A-Za-z0-9_\-]{8,}"
)

_MASK = "***"


def mask_secrets(text: str) -> tuple[str, int]:
    """Замазать похожее на ключи. Возвращает (текст, сколько замазано)."""
    count = 0

    def by_assignment(match):
        nonlocal count
        count += 1
        return f"{match.group(1)}{match.group(2)}{match.group(3)}{_MASK}{match.group(3)}"

    def by_prefix(match):
        nonlocal count
        count += 1
        return _MASK

    text = _ASSIGNMENT.sub(by_assignment, text)
    text = _KNOWN_PREFIX.sub(by_prefix, text)
    return text, count


def trim(text: str, limit: int) -> tuple[str, bool]:
    """Обрезать до limit знаков по границе строки.

    По границе, а не по символу: обрывок строки посреди выражения путает
    модель сильнее, чем честно укороченный фрагмент.
    """
    if len(text) <= limit:
        return text, False
    cut = text[:limit]
    newline = cut.rfind("\n")
    if newline > 0:
        cut = cut[:newline]
    return cut, True
```

- [ ] **Шаг 4: тесты зелёные**

Запуск: `"D:\Python\python.exe" -m pytest tests/test_code_prompt.py -q`
Ожидание: `8 passed`

---

## Task 3: Сборка промпта и разбор ответа

**Files:**
- Modify: `johnny/code_prompt.py` (дописать в конец)
- Test: `tests/test_code_prompt.py` (дописать в конец)

**Interfaces:**
- Consumes: `mask_secrets`, `trim` из задачи 2
- Produces: `build_prompt(instruction: str, code: str) -> str`,
  `extract_code(answer: str) -> str`,
  `prepare(code: str, limit: int) -> tuple[str, int, bool]`

- [ ] **Шаг 1: тест**

```python
def test_prompt_carries_both_instruction_and_code():
    prompt = code_prompt.build_prompt("объясни", "print(1)")
    assert "объясни" in prompt
    assert "print(1)" in prompt


def test_prompt_fences_the_code():
    """Без ограды модель путает код с инструкцией и начинает его выполнять
    как указание."""
    prompt = code_prompt.build_prompt("объясни", "print(1)")
    assert "```" in prompt


def test_extract_code_takes_what_is_inside_the_fence():
    """В буфер должен лечь КОД, а не «Вот ваш код:» с рассуждением —
    иначе вставка в редактор принесёт прозу."""
    answer = "Вот исправленный код:\n```python\ndef add(a, b):\n    return a + b\n```\nГотово."
    assert code_prompt.extract_code(answer) == "def add(a, b):\n    return a + b"


def test_extract_code_without_a_fence_returns_everything():
    assert code_prompt.extract_code("просто ответ") == "просто ответ"


def test_prepare_masks_and_trims_together():
    code = 'api_key = "gsk_aaaaaaaaaaaaaaaa"\n' + "\n".join(f"x{i}" for i in range(500))
    ready, masked, trimmed = code_prompt.prepare(code, 200)
    assert "gsk_aaaaaaaaaaaaaaaa" not in ready
    assert masked == 1
    assert trimmed is True
```

- [ ] **Шаг 2: убедиться, что падает**

Запуск: `"D:\Python\python.exe" -m pytest tests/test_code_prompt.py -q`
Ожидание: `AttributeError: module 'johnny.code_prompt' has no attribute 'build_prompt'`

- [ ] **Шаг 3: реализация** (дописать в `johnny/code_prompt.py`)

```python
# Ограда обязательна: без неё модель принимает код за продолжение инструкции
# и начинает выполнять написанное в нём как указание.
_TEMPLATE = (
    "Ты помогаешь программисту. Ниже фрагмент кода из его редактора.\n"
    "Задача: {instruction}\n\n"
    "Отвечай по-русски. Если просят изменить код — верни готовый код одним "
    "блоком в ``` ``` и без объяснений вокруг.\n\n"
    "```\n{code}\n```"
)

_FENCE = re.compile(r"```[a-zA-Z0-9_+-]*\n(.*?)```", re.DOTALL)


def build_prompt(instruction: str, code: str) -> str:
    """Промпт для модели: инструкция + огороженный код."""
    return _TEMPLATE.format(instruction=instruction.strip(), code=code)


def extract_code(answer: str) -> str:
    """Код из ответа модели. Нет ограды — возвращаем ответ целиком.

    Нужно потому, что результат кладётся в буфер для вставки в редактор:
    вставить «Вот исправленный код:» вместе с кодом значит сломать файл.
    """
    match = _FENCE.search(answer or "")
    return match.group(1).rstrip("\n") if match else (answer or "").strip()


def prepare(code: str, limit: int) -> tuple[str, int, bool]:
    """Код к отправке: обрезать, замазать секреты.

    Обрезаем ДО маскирования: маскировать выброшенный хвост незачем, а счётчик
    замазанного должен считать то, что реально уедет.
    """
    cut, trimmed = trim(code, limit)
    masked, count = mask_secrets(cut)
    return masked, count, trimmed
```

- [ ] **Шаг 4: тесты зелёные**

Запуск: `"D:\Python\python.exe" -m pytest tests/test_code_prompt.py -q`
Ожидание: `13 passed`

---

## Task 4: Действия, настройки и фразы

**Files:**
- Create: `johnny/actions/code_action.py`
- Modify: `johnny/actions/__init__.py` (добавить импорт)
- Modify: `johnny/config.py` (поля `code_assist_consent`, `code_assist_limit`)
- Modify: `config/settings.yaml` (значения с объяснением)
- Modify: `config/commands.yaml` (фразы, ПЕРЕД ловящими всё в конце файла)
- Test: `tests/test_code_action.py`

**Interfaces:**
- Consumes: `clipboard.read_text/write_text`, `code_prompt.prepare/build_prompt/extract_code`
- Produces: действия `explain_code`, `rewrite_code`

**Согласие отдельной строкой.** Groq и так получает каждую распознанную фразу,
но код из буфера — другое дело: это может быть целый проприетарный файл. Человек
копировал его для редактора, а не для облака. Поэтому `code_assist_consent`
отдельно, как `faceplusplus.consent` отдельно от общего.

- [ ] **Шаг 1: тест**

```python
"""Голосовая помощь с кодом: согласие, буфер, маршрутизация фраз."""

import types

import pytest
from pathlib import Path

import johnny.actions.code_action as code_action
from johnny.actions import execute
from johnny.config import load_config
from johnny.router import route, route_exact

_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def commands():
    return load_config(str(_ROOT / "config")).commands


def _config(consent=True, limit=8000):
    return types.SimpleNamespace(
        settings=types.SimpleNamespace(
            code_assist_consent=consent, code_assist_limit=limit
        ),
        secrets={}, commands=[],
    )


def test_without_consent_nothing_leaves_the_machine(monkeypatch):
    """Ключевая проверка приватности: без согласия буфер даже не читается."""
    touched = []
    monkeypatch.setattr(code_action.clipboard, "read_text",
                        lambda: touched.append("read") or "код")
    result = execute(
        types.SimpleNamespace(action="explain_code", argument="", via="тест"),
        {}, {}, config=_config(consent=False),
    )
    assert result.ok is False
    assert touched == []
    assert "code_assist_consent" in result.message


def test_empty_clipboard_is_explained_not_sent(monkeypatch):
    monkeypatch.setattr(code_action.clipboard, "read_text", lambda: "   ")
    sent = []
    monkeypatch.setattr(code_action, "_ask", lambda prompt, config: sent.append(prompt) or "")
    result = execute(
        types.SimpleNamespace(action="explain_code", argument="", via="тест"),
        {}, {}, config=_config(),
    )
    assert result.ok is False
    assert sent == []
    assert "скопируй" in result.message.lower()


def test_secrets_are_masked_before_the_prompt_is_built(monkeypatch):
    """Самое важное: ключ не должен попасть в промпт ни при каких условиях."""
    monkeypatch.setattr(code_action.clipboard, "read_text",
                        lambda: 'api_key = "gsk_aaaaaaaaaaaaaaaa"')
    sent = {}
    monkeypatch.setattr(code_action, "_ask",
                        lambda prompt, config: sent.setdefault("prompt", prompt) or "ответ")
    execute(
        types.SimpleNamespace(action="explain_code", argument="", via="тест"),
        {}, {}, config=_config(),
    )
    assert "gsk_aaaaaaaaaaaaaaaa" not in sent["prompt"]


def test_masking_is_announced_out_loud(monkeypatch):
    monkeypatch.setattr(code_action.clipboard, "read_text",
                        lambda: 'token = "abcdef123456"')
    monkeypatch.setattr(code_action, "_ask", lambda prompt, config: "ответ модели")
    result = execute(
        types.SimpleNamespace(action="explain_code", argument="", via="тест"),
        {}, {}, config=_config(),
    )
    assert result.ok is True
    assert "замаскирова" in result.message.lower()


def test_rewrite_puts_code_into_the_clipboard(monkeypatch):
    monkeypatch.setattr(code_action.clipboard, "read_text", lambda: "def f(): pass")
    monkeypatch.setattr(code_action, "_ask",
                        lambda prompt, config: "Готово:\n```python\ndef f():\n    return 1\n```")
    written = {}
    monkeypatch.setattr(code_action.clipboard, "write_text",
                        lambda text: written.setdefault("text", text))
    result = execute(
        types.SimpleNamespace(action="rewrite_code", argument="верни единицу", via="тест"),
        {}, {}, config=_config(),
    )
    assert result.ok is True
    assert written["text"] == "def f():\n    return 1"
    assert "вставь" in result.message.lower()


def test_rewrite_never_types_into_the_editor(monkeypatch):
    """Печать поверх выделенного кода необратима (роадмап §4: клавиатура — L1).
    Сторож: если кто-то захочет автопечать, он снимет этот тест осознанно."""
    import inspect

    assert "type_text" not in inspect.getsource(code_action)


def test_model_silence_is_reported(monkeypatch):
    monkeypatch.setattr(code_action.clipboard, "read_text", lambda: "код")
    monkeypatch.setattr(code_action, "_ask", lambda prompt, config: "")
    result = execute(
        types.SimpleNamespace(action="explain_code", argument="", via="тест"),
        {}, {}, config=_config(),
    )
    assert result.ok is False


@pytest.mark.parametrize("phrase,action", [
    ("объясни этот код", "explain_code"),
    ("что делает этот код", "explain_code"),
])
def test_explain_phrases_route(phrase, action, commands):
    routed = route_exact(phrase, commands, literal_only=True) or route(phrase, commands)
    assert routed is not None and routed.action == action


def test_rewrite_phrase_captures_the_instruction(commands):
    routed = route("перепиши этот код на английский", commands)
    assert routed.action == "rewrite_code"
    assert routed.argument == "на английский"
```

- [ ] **Шаг 2: убедиться, что падает**

Запуск: `"D:\Python\python.exe" -m pytest tests/test_code_action.py -q`
Ожидание: `ModuleNotFoundError: No module named 'johnny.actions.code_action'`

- [ ] **Шаг 3: реализация** — `johnny/actions/code_action.py`

```python
"""Голосовая помощь с кодом: объяснить и переписать выделенное.

Мост — буфер обмена: человек копирует код в редакторе (Ctrl+C), говорит, что
с ним сделать, и получает объяснение вслух либо переписанный код обратно
в буфер (вставляет сам, Ctrl+V).

Почему НЕ печатаем результат сами: печать поверх выделенного необратима, а
роадмап автономности §4 требует подтверждения на любой ввод с клавиатуры.
Вставка руками — это и есть подтверждение, и человек видит код заранее.
Сторож — tests/test_code_action.py::test_rewrite_never_types_into_the_editor.
"""

from .. import clipboard, code_prompt, git_tools
from ..brain import make_providers
from .registry import ActionResult, registry


def _ask(prompt: str, config) -> str:
    """Спросить цепочку моделей. Первый непустой ответ побеждает.

    Своей цепочки не заводим: `make_providers` уже решает, кто отвечает и
    в каком порядке (Groq → сильная модель → claude), и дублировать её
    значило бы разойтись с настройками расходов.
    """
    for name, provider in make_providers(config):
        try:
            answer = (provider(prompt) or "").strip()
        except Exception:
            continue
        if answer:
            return answer
    return ""


def _prepared(ctx: dict):
    """Общая часть обеих команд: согласие → буфер → маскирование.

    Возвращает (ошибка_или_None, готовый_код, замаскировано, обрезано).
    """
    settings = getattr(ctx.get("config"), "settings", None)
    if not getattr(settings, "code_assist_consent", False):
        return (ActionResult(False, "Отправка кода в облако выключена: "
                                    "включи code_assist_consent в settings.yaml"),
                "", 0, False)
    try:
        raw = clipboard.read_text()
    except clipboard.ClipboardError as exc:
        return ActionResult(False, f"Не смог прочитать буфер: {exc}"), "", 0, False
    if not raw.strip():
        return (ActionResult(False, "Буфер пуст — скопируй код и повтори"),
                "", 0, False)
    limit = getattr(settings, "code_assist_limit", 8000)
    ready, masked, trimmed = code_prompt.prepare(raw, limit)
    return None, ready, masked, trimmed


def _notes(masked: int, trimmed: bool) -> str:
    """Приписка про замазанное и обрезанное.

    Обязательна: без неё человек не поймёт, почему модель рассуждает про ***
    или не увидела конец файла.
    """
    parts = []
    if masked:
        # plural берём из git_tools, а не пишем второй раз: два набора правил
        # склонения разъедутся на первом же исправлении. Появится третий
        # потребитель — правило переедет в morph.py, где ему и место.
        parts.append(f"замаскировал {masked} "
                     f"{git_tools.plural(masked, 'секрет', 'секрета', 'секретов')}")
    if trimmed:
        parts.append("код длинный, отправил начало")
    return f" ({', '.join(parts)})" if parts else ""


@registry.register("explain_code")
def action_explain_code(argument: str, ctx: dict) -> ActionResult:
    """«Джони, объясни этот код» — объяснение вслух."""
    error, code, masked, trimmed = _prepared(ctx)
    if error is not None:
        return error
    instruction = argument.strip() or "объясни, что делает этот код, коротко"
    answer = _ask(code_prompt.build_prompt(instruction, code), ctx.get("config"))
    if not answer:
        return ActionResult(False, "Модель не ответила")
    return ActionResult(True, answer + _notes(masked, trimmed))


@registry.register("rewrite_code")
def action_rewrite_code(argument: str, ctx: dict) -> ActionResult:
    """«Джони, перепиши этот код <как>» — результат в буфер, вставляет человек."""
    error, code, masked, trimmed = _prepared(ctx)
    if error is not None:
        return error
    instruction = argument.strip() or "перепиши этот код чище, поведение сохрани"
    answer = _ask(code_prompt.build_prompt(instruction, code), ctx.get("config"))
    if not answer:
        return ActionResult(False, "Модель не ответила")
    try:
        clipboard.write_text(code_prompt.extract_code(answer))
    except clipboard.ClipboardError as exc:
        return ActionResult(False, f"Ответ есть, но буфер занят: {exc}")
    return ActionResult(True, "Готово, код в буфере — вставь куда нужно"
                              + _notes(masked, trimmed))
```

- [ ] **Шаг 4: подключить модуль** — в `johnny/actions/__init__.py`, рядом с
      остальными импортами, в алфавитном порядке (после `browser`):

```python
from . import code_action      # noqa: F401
```

- [ ] **Шаг 5: настройки** — в `johnny/config.py`, в `class Settings`, сразу
      после `git_workdir`:

```python
    # Помощь с кодом: Джони читает буфер обмена и шлёт код в облачную модель.
    # Выключено по умолчанию НЕ из осторожности вообще, а по существу: в буфере
    # оказывается то, что человек выделил в редакторе, — запросто целый файл
    # с ключами или чужими данными, скопированный для редактора, а не для
    # облака. Отдельной строкой от общего согласия, как faceplusplus.consent.
    code_assist_consent: bool = False
    # Сколько знаков кода уходит в модель. 8000 — примерно 200 строк: больше
    # и не нужно (вопрос обычно про кусок), и дороже по токенам.
    code_assist_limit: int = 8000
```

      и в `load_config`, в конструктор `Settings(...)`, рядом с остальными:

```python
        code_assist_consent=bool(s.get("code_assist_consent", False)),
        code_assist_limit=int(s.get("code_assist_limit", 8000)),
```

- [ ] **Шаг 6: значения** — в `config/settings.yaml`, после блока `git_workdir`:

```yaml
# Помощь с кодом голосом: «объясни этот код», «перепиши этот код …».
# Джони читает БУФЕР ОБМЕНА (то, что ты скопировал в редакторе) и отправляет
# его в облачную модель — ту же, что отвечает на команды (Groq → сильная).
# false = выключено, и это правильное значение по умолчанию: в буфере может
# оказаться целый файл с ключами. Секреты Джони замазывает перед отправкой
# (johnny/code_prompt.py) и говорит, сколько замазал, но полагаться только на
# это нельзя — согласие включаешь ты.
code_assist_consent: false
code_assist_limit: 8000        # знаков кода за раз (~200 строк)
```

- [ ] **Шаг 7: фразы** — в `config/commands.yaml`, **перед** комментарием
      `# ВАЖНО: стоят В САМОМ КОНЦЕ файла намеренно`:

```yaml
# --- Помощь с кодом (буфер обмена → модель) ---
# Работает только при code_assist_consent: true в settings.yaml.
# «перепиши этот код *» — ПЕРЕД «перепиши этот код», иначе жадный шаблон
# заберёт фразу и инструкция потеряется (та же ловушка, что у «закоммить *»).
"объясни этот код":
  action: explain_code
  template: ""
"что делает этот код":
  action: explain_code
  template: ""
"объясни код":
  action: explain_code
  template: ""
"перепиши этот код *":
  action: rewrite_code
  template: "{0}"
"перепиши код *":
  action: rewrite_code
  template: "{0}"
"исправь этот код":
  action: rewrite_code
  template: ""
```

- [ ] **Шаг 8: тесты зелёные**

Запуск: `"D:\Python\python.exe" -m pytest tests/test_code_action.py -q`
Ожидание: `10 passed`

- [ ] **Шаг 9: весь набор**

Запуск: `"D:\Python\python.exe" -m pytest -q --ignore=tests/test_face_index_connector.py`
Ожидание: `1058 + новые passed`, ни одного упавшего

---

## Task 5: Живая проверка и документация

**Files:**
- Modify: `docs/superpowers/plans/current_work_plan.md`
- Modify: `ПолныйСписок.md`
- Modify: `docs/superpowers/plans/details/add_ai_prompt_proxy_support_in_editors.md`

- [ ] **Шаг 1: живой прогон целиком** (моки не доказывают, что связка работает)

Временно включить согласие в `config/settings.yaml` (`code_assist_consent: true`),
скопировать в буфер любой кусок кода и выполнить:

```bash
"D:\Python\python.exe" - <<'PY'
import sys; sys.path.insert(0, '.')
from johnny.config import load_config
from johnny.app import handle_command
config = load_config("config")
class S:
    def say(self, t): print("   Джони:", t)
    def play_answer(self): return False
handle_command("объясни этот код", config, S(), use_brain=False)
PY
```

Ожидание: осмысленное объяснение по-русски про скопированный код.

- [ ] **Шаг 2: проверить маскирование вживую** — скопировать в буфер строку
      `api_key = "gsk_реальныйвидключа123456"` и повторить прогон. Ожидание:
      в ответе Джони есть «замаскировал 1 секрет», а модель рассуждает про `***`.

- [ ] **Шаг 3: вернуть согласие в `false`**, если владелец не решил иначе —
      значение по умолчанию принадлежит ему, а не исполнителю плана.

- [ ] **Шаг 4: записать в `current_work_plan.md`** — отметить пункт сделанным,
      перечислить: почему отказались от FastAPI и расширения VS Code (нет IDE
      на машине, сервер охранял бы localhost от себя), почему результат не
      печатается сам, что согласие отдельное, что маскирование обязательно.

- [ ] **Шаг 5: дописать фразы в `ПолныйСписок.md`** отдельным разделом
      «💻 Помощь с кодом», с оговоркой про буфер и согласие, и обновить счётчик
      фраз в шапке файла.

- [ ] **Шаг 6: пометить исходную спеку** `details/add_ai_prompt_proxy_support_in_editors.md`
      блоком в начале: что сделано вместо FastAPI/расширения и почему.

---

## Логирование (шаг 3 исходной спеки) — и почему НЕ содержимого

Спека просит логирование. Оно уже есть и трогать его не надо: `history.log`
пишет каждую команду с меткой пути разбора, и вызовы помощи с кодом попадут
туда сами, как все прочие.

**Содержимое кода в лог не пишем — ни исходное, ни ответ модели.** Иначе
маскирование теряет смысл: замазанный для облака ключ лёг бы открытым текстом
в файл на диске, который к тому же открывается кнопкой «Лог» в панели и
показывается на скриншотах. В логе остаётся факт вызова, а не то, что уехало.
Отдельный тест на это не нужен: логирование делает контроллер, а этот модуль
в него ничего не передаёт — достаточно не добавлять сюда `logger.info(code)`.

## Что этот план сознательно НЕ делает

- **Не печатает ответ в редактор.** Причины выше; изменить это можно только
  вместе с подтверждением, а подтверждения в проекте пока нет вовсе.
- **Не версионирует изменения** (шаг 3 исходной спеки). Версионирование кода
  уже есть и называется git — а голосовые git-команды сделаны пунктом 1.
  Второй механизм истории поверх него был бы дублем.
- **Не читает файл с диска по пути.** Буфер честнее: человек видит глазами,
  что именно отправляет. Чтение по пути стоит делать вместе с анализом
  проекта — это следующий пункт плана.
