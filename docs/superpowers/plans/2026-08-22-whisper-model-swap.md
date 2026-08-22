# Подмена модели Whisper на время игры — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Пока запущена игра, Джони держит модель Whisper `small` (648 МБ
видеопамяти) вместо `medium` (2138 МБ), а после выхода из игры возвращается.

**Architecture:** Подмену умеет сам `Recognizer` — новый метод `use_model()`
меняет модель внутри существующего объекта, поэтому снаружи не меняется ничего.
Отдельный модуль `johnny/game_watch.py` в фоновом потоке читает два признака
(ключ реестра Steam и свободную видеопамять) и зовёт `use_model`. От наложения
на работающую расшифровку защищает `threading.RLock`.

**Tech Stack:** Python 3.14, faster-whisper / ctranslate2, `winreg` (в составе
Python на Windows), `nvidia-smi`, pytest.

Спека: `docs/superpowers/specs/2026-08-22-whisper-model-swap-design.md`

## Global Constraints

- Комментарии и docstring — по-русски, как во всём проекте. Комментарий
  объясняет **почему**, а не пересказывает код.
- Тесты запускаются так: `D:/Python/python.exe -m pytest tests/ -q --ignore=tests/test_face_index_connector.py --ignore=tests/test_discord_actions.py`. Два файла исключены не нами: у них падает сбор из-за отсутствующего `cv2`.
- Базовый уровень до начала работ — **1107 passed**. Ни один существующий тест
  сломаться не должен.
- Ничего не коммитить с `--no-verify`.
- Порог «уйти» и порог «вернуться» обязаны различаться больше, чем на объём,
  который освобождает сама подмена (2138 − 648 = **1490 МБ**). Иначе Джони
  начнёт метаться: ушёл на `small`, свободной памяти сразу стало на 1.5 ГБ
  больше, порог возврата перейдён, вернулся на `medium` — и по кругу.
- Замеры, на которые опирается план (RTX 3070, 2026-08-22): `medium` 2138 МБ /
  0.56 с, `small` 648 МБ / 0.25 с, выгрузка возвращает 1984 МБ за 1.1 с,
  загрузка `small` 1.3 с.

---

## Структура файлов

| Файл | Ответственность |
|---|---|
| `johnny/recognizer.py` (правка) | Умеет менять свою модель на ходу, не отдавая её из-под работающей расшифровки |
| `johnny/game_watch.py` (новый) | Читает признаки «идёт игра» и решает, какую модель держать. Про звук и Whisper не знает |
| `johnny/config.py` (правка) | Три новые настройки |
| `config/settings.yaml` (правка) | Значения для этой машины |
| `johnny/tray.py`, `johnny/app.py` (правка) | Запускают и останавливают сторожа |

---

### Task 1: `Recognizer.use_model` — подмена модели с замком

**Files:**
- Modify: `johnny/recognizer.py` (класс `Recognizer`, строки 182–243 и `transcribe` со строки 245)
- Modify: `tests/test_recognizer_confidence.py:19-24`
- Modify: `tests/test_recognizer_repetition.py:18-23`
- Test: `tests/test_recognizer_swap.py` (создать)

**Interfaces:**
- Consumes: ничего от других задач.
- Produces: `Recognizer.use_model(model: str) -> bool` — True, если модель
  сменилась. `Recognizer.model_name: str` — имя текущей модели.

- [ ] **Step 1: Написать падающий тест**

Создать `tests/test_recognizer_swap.py`:

```python
"""Подмена модели Whisper на ходу: она же экономия видеопамяти во время игры."""
import threading

import johnny.recognizer as recognizer_module
from johnny.recognizer import Recognizer


class FakeWhisper:
    """Считает, сколько раз её создавали и с какими аргументами."""

    created: list[tuple[str, str, str]] = []

    def __init__(self, model, device, compute_type):
        FakeWhisper.created.append((model, device, compute_type))
        self.model = model

    def transcribe(self, audio, **kwargs):
        return iter(()), None


def _fresh(monkeypatch):
    FakeWhisper.created = []
    monkeypatch.setattr(recognizer_module, "WhisperModel", FakeWhisper)
    return Recognizer("medium", "cuda", "")


def test_use_model_меняет_модель(monkeypatch):
    recognizer = _fresh(monkeypatch)

    changed = recognizer.use_model("small")

    assert changed is True
    assert recognizer.model_name == "small"
    assert recognizer._model.model == "small"


def test_use_model_на_ту_же_модель_ничего_не_делает(monkeypatch):
    """Сторож опрашивает признак раз в несколько секунд и почти всегда видит
    то же самое. Перезагружать модель на каждый опрос — 2.4 секунды впустую и
    выброшенная из памяти рабочая модель."""
    recognizer = _fresh(monkeypatch)
    было = len(FakeWhisper.created)

    changed = recognizer.use_model("medium")

    assert changed is False
    assert len(FakeWhisper.created) == было


def test_старая_модель_отпускается_до_загрузки_новой(monkeypatch):
    """Смысл подмены — освободить видеопамять. Если обе модели полежат на карте
    одновременно, экономии не будет вовсе, будет перерасход.

    Проверяем порядок напрямую: в момент, когда создаётся новая модель, поле
    _model обязано быть уже пустым. Через __del__ и сборщик мусора это же
    свойство проверялось бы недетерминированно."""
    recognizer = _fresh(monkeypatch)
    состояние_при_загрузке = []

    class Наблюдаемая(FakeWhisper):
        def __init__(self, model, device, compute_type):
            состояние_при_загрузке.append(recognizer._model)
            super().__init__(model, device, compute_type)

    monkeypatch.setattr(recognizer_module, "WhisperModel", Наблюдаемая)
    recognizer.use_model("small")

    assert состояние_при_загрузке == [None]


def test_если_новая_модель_не_поднялась_имя_не_меняется(monkeypatch):
    """Игра успела забрать всю память. Соврать про то, какая модель стоит,
    нельзя: сторож сравнивает model_name с желаемым и на вранье начнёт
    дёргать подмену каждый опрос."""
    recognizer = _fresh(monkeypatch)

    class Отказ:
        def __init__(self, model, device, compute_type):
            raise RuntimeError("нет памяти")

    monkeypatch.setattr(recognizer_module, "WhisperModel", Отказ)
    changed = recognizer.use_model("small")

    assert changed is False
    assert recognizer.model_name == "medium"


def test_расшифровка_и_подмена_делят_один_замок(monkeypatch):
    """transcribe работает в потоке контроллера, use_model — в потоке сторожа.
    Выдернуть модель из-под работающей расшифровки значит проглотить команду."""
    recognizer = _fresh(monkeypatch)

    assert isinstance(recognizer._lock, type(threading.RLock()))
```

- [ ] **Step 2: Убедиться, что тест падает**

Запустить: `D:/Python/python.exe -m pytest tests/test_recognizer_swap.py -q`
Ожидается: FAIL, `AttributeError: 'Recognizer' object has no attribute 'use_model'`.

- [ ] **Step 3: Вынести лесенку загрузки в отдельный метод**

В `johnny/recognizer.py` добавить в начало файла к импортам:

```python
import gc
import threading
```

Заменить тело `Recognizer.__init__` (строки 182–243, от `def __init__` до конца
цикла с `preferred_compute_types`) на:

```python
class Recognizer:
    def __init__(self, model: str, device: str, vocabulary: str = ""):
        self._prompt = _BASE_PROMPT + (" " + vocabulary if vocabulary else "")
        # Худшая уверенность последней расшифровки. Пока только пишется в
        # историю: порог отсечения выбирается по накопленным данным, а не
        # угадывается заранее.
        self.last_confidence = 0.0
        # Замок общий у расшифровки и подмены модели: transcribe работает в
        # потоке контроллера, а use_model зовут из потока сторожа. RLock, а не
        # Lock, чтобы вложенный вызов внутри одного потока не встал намертво.
        self._lock = threading.RLock()
        self._device = device
        self.model_name = model
        self._model = None

        if WhisperModel is None:
            logger.warning("faster-whisper недоступен, распознавание будет отключено: %s", _WHISPER_IMPORT_ERROR)
            return

        self._model = self._load(model, device)

    def _load(self, model: str, device: str):
        """Поднять модель, спускаясь по лесенке до первого рабочего режима.

        Возвращает модель или None. Лесенка нужна потому, что доступность
        режима зависит от машины: на карте без float16 просьба о нём падает, а
        человек всё равно должен быть услышан — пусть и на процессоре.
        """
        if device == "cuda":
            preferred_compute_types = [
                ("cuda", "float16"),
                ("cuda", "float32"),
                ("cuda", "int8"),
                ("cpu", "int8"),
            ]
        else:
            preferred_compute_types = [
                ("cpu", "int8"),
                ("cpu", "float32"),
            ]

        last_error: Exception | None = None
        for target_device, compute_type in preferred_compute_types:
            try:
                loaded = WhisperModel(
                    model,
                    device=target_device,
                    compute_type=compute_type,
                )
            except Exception as exc:  # pragma: no cover - environment-dependent
                last_error = exc
                logger.warning(
                    "Не удалось инициализировать Whisper-модель %r с %s/%s: %s",
                    model,
                    target_device,
                    compute_type,
                    exc,
                )
                continue
            if target_device != device or compute_type != ("float16" if device == "cuda" else "int8"):
                logger.warning(
                    "Whisper-модель %r инициализирована в fallback-режиме %s/%s",
                    model,
                    target_device,
                    compute_type,
                )
            return loaded

        logger.warning("Whisper не смог загрузиться ни в одном режиме: %s", last_error)
        return None
```

- [ ] **Step 4: Добавить `use_model`**

Сразу после `_load`, перед свойством `available`:

```python
    def use_model(self, model: str) -> bool:
        """Сменить модель Whisper на ходу. True — сменили, False — оставили как было.

        Зачем: medium на видеокарте занимает 2138 МБ, small — 648 МБ (замер
        2026-08-22 на RTX 3070). На время игры разницу отдаём игре.

        Замок держим на всю подмену: она занимает около 2.4 секунды, и если
        выдернуть модель из-под работающего transcribe, команда пропадёт.
        """
        with self._lock:
            if model == self.model_name and self._model is not None:
                return False

            previous_name = self.model_name
            # Старую отпускаем ДО загрузки новой. Иначе на карте полежат обе,
            # и подмена, затеянная ради экономии памяти, её же и не даст.
            self._model = None
            gc.collect()

            loaded = self._load(model, self._device)
            if loaded is None:
                logger.warning(
                    "Модель %r не поднялась — возвращаюсь на %r", model, previous_name
                )
                self._model = self._load(previous_name, self._device)
                return False

            self._model = loaded
            self.model_name = model
            logger.info("Whisper переключён на модель %r", model)
            return True
```

- [ ] **Step 5: Взять замок в `transcribe`**

В `johnny/recognizer.py`, в методе `transcribe`, обернуть вызов модели. Заменить:

```python
        segments, _ = self._model.transcribe(
```

на:

```python
        with self._lock:
            segments, _ = self._model.transcribe(
```

и увеличить отступ у всех аргументов вызова и у строки `segments = list(segments)`
так, чтобы они попали внутрь `with`. Список `list(segments)` обязан быть внутри
замка: `transcribe` у faster-whisper возвращает **ленивый генератор**, и
настоящая работа происходит при переборе, а не при вызове.

- [ ] **Step 6: Починить два существующих теста**

Оба строят `Recognizer` в обход `__init__`, поэтому `_lock` у них не появится и
`transcribe` упадёт с `AttributeError`.

В `tests/test_recognizer_confidence.py` добавить `import threading` в начало и
в функцию `_recognizer` (строки 19–24) строку:

```python
def _recognizer(segments):
    recognizer = Recognizer.__new__(Recognizer)   # без загрузки настоящей модели
    recognizer._model = FakeModel(segments)
    recognizer._prompt = "Джони. Русские голосовые команды."
    recognizer.last_confidence = 0.0
    recognizer._lock = threading.RLock()          # transcribe берёт его на время работы
    return recognizer
```

В `tests/test_recognizer_repetition.py` — то же самое, сохранив её собственный
`_prompt`:

```python
def _recognizer(segments):
    recognizer = Recognizer.__new__(Recognizer)  # без загрузки настоящей модели
    recognizer._model = FakeModel(segments)
    recognizer._prompt = "Джони. Русские голосовые команды. введи"
    recognizer.last_confidence = 0.0
    recognizer._lock = threading.RLock()         # transcribe берёт его на время работы
    return recognizer
```

- [ ] **Step 7: Прогнать тесты**

Запустить: `D:/Python/python.exe -m pytest tests/ -q --ignore=tests/test_face_index_connector.py --ignore=tests/test_discord_actions.py`
Ожидается: PASS, **1112 passed** (1107 прежних + 5 новых).

- [ ] **Step 8: Коммит**

```bash
git add johnny/recognizer.py tests/test_recognizer_swap.py tests/test_recognizer_confidence.py tests/test_recognizer_repetition.py
git commit -m "Recognizer.use_model: подмена модели Whisper на ходу"
```

---

### Task 2: `game_watch` — признаки игры и решение о модели

**Files:**
- Create: `johnny/game_watch.py`
- Test: `tests/test_game_watch.py` (создать)

**Interfaces:**
- Consumes: ничего (`Recognizer` тут не участвует — модуль про признаки, не про Whisper).
- Produces:
  - `steam_game_running() -> bool`
  - `free_vram_mb() -> int | None`
  - `decide(current: str, *, normal_model: str, gaming_model: str, game_running: bool, free_mb: int | None, low_mb: int, high_mb: int) -> str`

- [ ] **Step 1: Написать падающий тест**

Создать `tests/test_game_watch.py`:

```python
"""Решение «какую модель Whisper держать» — без видеокарты и без Steam."""
from johnny.game_watch import decide

БАЗА = dict(normal_model="medium", gaming_model="small", low_mb=2500, high_mb=4500)


def test_игра_запущена_уходим_на_маленькую():
    assert decide("medium", game_running=True, free_mb=6000, **БАЗА) == "small"


def test_игры_нет_и_памяти_вдоволь_возвращаемся():
    assert decide("small", game_running=False, free_mb=6000, **БАЗА) == "medium"


def test_памяти_мало_уходим_даже_без_steam():
    """Игра мимо Steam: Epic, свой лаунчер, просто exe."""
    assert decide("medium", game_running=False, free_mb=1000, **БАЗА) == "small"


def test_между_порогами_ничего_не_трогаем():
    """Гистерезис. Без мёртвой зоны Джони метался бы туда-сюда, и каждое
    метание стоит 2.4 секунды."""
    assert decide("small", game_running=False, free_mb=3000, **БАЗА) == "small"
    assert decide("medium", game_running=False, free_mb=3000, **БАЗА) == "medium"


def test_мёртвая_зона_шире_того_что_освобождает_подмена():
    """Подмена сама меняет свободную память на 1490 МБ (2138 − 648). Если бы
    зазор между порогами был уже, уход на small тут же перевёл бы порог
    возврата, и Джони закольцевался бы."""
    assert БАЗА["high_mb"] - БАЗА["low_mb"] > 1490


def test_пустая_игровая_модель_выключает_переключение():
    """Значение по умолчанию: ничего не делаем никогда."""
    assert decide(
        "medium",
        normal_model="medium",
        gaming_model="",
        game_running=True,
        free_mb=100,
        low_mb=2500,
        high_mb=4500,
    ) == "medium"


def test_без_ответа_от_видеокарты_остаёмся_на_обычной():
    """nvidia-smi не отвечает — про игру ничего не известно. Молча уходить на
    маленькую модель было бы решением на пустом месте."""
    assert decide("medium", game_running=False, free_mb=None, **БАЗА) == "medium"
```

- [ ] **Step 2: Убедиться, что тест падает**

Запустить: `D:/Python/python.exe -m pytest tests/test_game_watch.py -q`
Ожидается: FAIL, `ModuleNotFoundError: No module named 'johnny.game_watch'`.

- [ ] **Step 3: Написать модуль**

Создать `johnny/game_watch.py`:

```python
"""Признак «идёт игра» и решение, какую модель Whisper держать.

Отдельный модуль именно потому, что решение проверяется без видеокарты и без
Steam: decide() — чистая функция, а два признака читаются двумя мелкими
функциями, которые в тестах подменяются.
"""
import logging
import subprocess

logger = logging.getLogger(__name__)

_STEAM_KEY = r"Software\Valve\Steam"
_NVIDIA_SMI_TIMEOUT = 5.0


def steam_game_running() -> bool:
    """True, если Steam запустил игру: RunningAppID — её номер, 0 — игры нет.

    Ключ означает «игра ЗАПУЩЕНА», а не «игра в фокусе», и это именно то, что
    нужно: свёрнутая игра видеопамять не отдаёт. Следи он за фокусом, Джони
    менял бы модель на каждый alt-tab по 2.4 секунды за раз.
    """
    try:
        import winreg
    except ImportError:  # не Windows — признака просто нет
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _STEAM_KEY) as key:
            value, _ = winreg.QueryValueEx(key, "RunningAppID")
    except OSError:
        # Steam не установлен, не запускался или ключа нет. Это не ошибка:
        # признак недоступен, и точка — жаловаться в лог не о чем.
        return False
    try:
        return int(value) != 0
    except (TypeError, ValueError):
        return False


def free_vram_mb() -> int | None:
    """Свободная видеопамять в мегабайтах. None — спросить не у кого.

    None и ноль — разные вещи, поэтому не int: на машине без nvidia-smi ноль
    означал бы «памяти нет совсем» и гнал бы Джони на маленькую модель вечно.
    """
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=_NVIDIA_SMI_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    lines = out.stdout.strip().splitlines()
    if not lines:
        return None
    try:
        return int(lines[0].strip())
    except ValueError:
        return None


def decide(
    current: str,
    *,
    normal_model: str,
    gaming_model: str,
    game_running: bool,
    free_mb: int | None,
    low_mb: int,
    high_mb: int,
) -> str:
    """Какую модель держать сейчас. Может вернуть ту же, что и была.

    Два порога вместо одного — это гистерезис, и он здесь не перестраховка.
    Сама подмена меняет свободную память на 1490 МБ (2138 у medium против 648
    у small). На одном пороге уход на small немедленно перевёл бы условие
    возврата, и Джони закольцевался бы, платя 2.4 секунды за круг. Поэтому
    зазор между low_mb и high_mb обязан быть шире 1490 МБ.
    """
    if not gaming_model:
        return normal_model  # выключено настройкой
    if game_running:
        return gaming_model
    if free_mb is None:
        # Спросить не у кого, и Steam молчит. Поводов уходить нет.
        return normal_model
    if free_mb < low_mb:
        return gaming_model
    if free_mb > high_mb:
        return normal_model
    return current  # мёртвая зона между порогами: не трогаем
```

- [ ] **Step 4: Прогнать тесты**

Запустить: `D:/Python/python.exe -m pytest tests/test_game_watch.py -q`
Ожидается: PASS, 7 passed.

- [ ] **Step 5: Коммит**

```bash
git add johnny/game_watch.py tests/test_game_watch.py
git commit -m "game_watch: признак игры и решение о модели Whisper"
```

---

### Task 3: Настройки

**Files:**
- Modify: `johnny/config.py:19-20` (поля) и `:128-129` (чтение)
- Modify: `config/settings.yaml` (после блока `whisper_device`, строка 95)
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: ничего.
- Produces: `Settings.whisper_model_gaming: str`, `Settings.gpu_guard_low_mb: int`,
  `Settings.gpu_guard_high_mb: int`.

- [ ] **Step 1: Написать падающий тест**

Дописать в конец `tests/test_config.py`:

```python
def test_игровая_модель_по_умолчанию_выключена():
    """Пусто = никогда не переключаться, как git_workdir и streaming_fillers.
    Переключение трогает видеопамять чужой машины — включать его молча нельзя."""
    settings = config.Settings(
        wake_word="джони", vosk_model_path="p", response_mode="voice",
        whisper_model="medium", whisper_device="cpu",
    )
    assert settings.whisper_model_gaming == ""


def test_игровая_модель_и_пороги_читаются_из_yaml(tmp_path):
    (tmp_path / "apps.yaml").write_text("{}", encoding="utf-8")
    (tmp_path / "commands.yaml").write_text("{}", encoding="utf-8")
    (tmp_path / "settings.yaml").write_text(
        "wake_word: джони\n"
        "vosk_model_path: p\n"
        "response_mode: voice\n"
        "whisper_model: medium\n"
        "whisper_device: cuda\n"
        "whisper_model_gaming: small\n"
        "gpu_guard_low_mb: 2500\n"
        "gpu_guard_high_mb: 4500\n",
        encoding="utf-8",
    )
    loaded = config.load_config(tmp_path)
    assert loaded.settings.whisper_model_gaming == "small"
    assert loaded.settings.gpu_guard_low_mb == 2500
    assert loaded.settings.gpu_guard_high_mb == 4500
```

Стиль скопирован с соседей в этом же файле — `test_streaming_is_off_by_default`
(строка 186) и `test_streaming_settings_are_read_from_yaml` (строка 197). Второй
стиль в одном файле заводить не нужно.

- [ ] **Step 2: Убедиться, что тест падает**

Запустить: `D:/Python/python.exe -m pytest tests/test_config.py -q -k игровая`
Ожидается: FAIL, `AttributeError: 'Settings' object has no attribute 'whisper_model_gaming'`.

- [ ] **Step 3: Добавить поля в `Settings`**

В `johnny/config.py` после строки `whisper_device: str` (строка 20) — эти поля
со значениями по умолчанию должны стоять среди прочих необязательных, не среди
обязательных без значения:

```python
    # Модель, на которую уходить, пока запущена игра. Пусто = не переключаться
    # никогда, и это правильное значение по умолчанию: подмена трогает
    # видеопамять, а сколько её и на что она нужна — знает только владелец
    # машины. Замер на RTX 3070 (2026-08-22): medium 2138 МБ, small 648 МБ.
    whisper_model_gaming: str = ""
    # Пороги свободной видеопамяти для запасного признака (игры мимо Steam).
    # Зазор между ними обязан быть шире 1490 МБ — столько освобождает сама
    # подмена, и на узком зазоре Джони закольцуется. Подробнее — game_watch.decide.
    gpu_guard_low_mb: int = 2500
    gpu_guard_high_mb: int = 4500
```

- [ ] **Step 4: Читать их из YAML**

В `johnny/config.py` рядом со строками 128–129 добавить:

```python
        whisper_model_gaming=str(s.get("whisper_model_gaming", "") or ""),
        gpu_guard_low_mb=int(s.get("gpu_guard_low_mb", 2500)),
        gpu_guard_high_mb=int(s.get("gpu_guard_high_mb", 4500)),
```

- [ ] **Step 5: Прописать значения для этой машины**

В `config/settings.yaml` сразу после блока комментариев к `whisper_device`
(после строки 95, перед разделом `# Внешние тулзы`):

```yaml
# Пока идёт игра, Джони уходит на маленькую модель и возвращает видеопамять.
# Пусто = никогда не переключаться. Замер 2026-08-22 на RTX 3070: medium
# занимает 2138 МБ и расшифровывает за 0.56 с, small — 648 МБ и 0.25 с. То
# есть во время игры Джони даже быстрее, просто менее точен на длинных фразах.
whisper_model_gaming: small
# Запасной признак — для игр мимо Steam (Epic, свой лаунчер, просто exe).
# Уходим, когда свободно меньше low; возвращаемся, когда больше high. Зазор
# между ними обязан быть шире 1490 МБ: столько освобождает сама подмена, и на
# узком зазоре уход немедленно перевёл бы условие возврата (game_watch.decide).
gpu_guard_low_mb: 2500
gpu_guard_high_mb: 4500
```

- [ ] **Step 6: Прогнать тесты**

Запустить: `D:/Python/python.exe -m pytest tests/ -q --ignore=tests/test_face_index_connector.py --ignore=tests/test_discord_actions.py`
Ожидается: PASS, **1121 passed** (1112 после Task 1 + 7 из Task 2 + 2 новых).

- [ ] **Step 7: Коммит**

```bash
git add johnny/config.py config/settings.yaml tests/test_config.py
git commit -m "Настройки игровой модели Whisper и порогов видеопамяти"
```

---

### Task 4: Сторож в потоке и подключение

**Files:**
- Modify: `johnny/game_watch.py` (дописать класс `Guard`)
- Modify: `tests/test_game_watch.py` (дописать тесты `Guard`)
- Modify: `johnny/tray.py:86-97` (после создания микрофона) и `:155-159` (`quit_app`)
- Modify: `johnny/app.py:337-339` (после создания `recognizer`) и `:352-358`

**Interfaces:**
- Consumes: `Recognizer.use_model`, `Recognizer.model_name` (Task 1);
  `decide`, `steam_game_running`, `free_vram_mb` (Task 2); три поля `Settings`
  (Task 3).
- Produces: `Guard(recognizer, settings, *, game_running=..., free_mb=...)`
  с методами `tick() -> str`, `start() -> None`, `stop() -> None`.

- [ ] **Step 1: Написать падающий тест**

Дописать в `tests/test_game_watch.py`:

```python
from johnny.game_watch import Guard


class ФальшивыйRecognizer:
    def __init__(self, model="medium"):
        self.model_name = model
        self.смены = []

    def use_model(self, model):
        self.смены.append(model)
        self.model_name = model
        return True


class ФальшивыеНастройки:
    whisper_model = "medium"
    whisper_model_gaming = "small"
    gpu_guard_low_mb = 2500
    gpu_guard_high_mb = 4500


def _сторож(recognizer, *, игра, память):
    return Guard(
        recognizer,
        ФальшивыеНастройки(),
        game_running=lambda: игра,
        free_mb=lambda: память,
    )


def test_запуск_игры_уводит_на_маленькую():
    recognizer = ФальшивыйRecognizer()

    _сторож(recognizer, игра=True, память=6000).tick()

    assert recognizer.смены == ["small"]


def test_повторный_опрос_не_дёргает_модель():
    """Сторож опрашивает раз в несколько секунд, а признак почти всегда тот же.
    Каждая лишняя подмена — 2.4 секунды и выброшенная рабочая модель."""
    recognizer = ФальшивыйRecognizer()
    guard = _сторож(recognizer, игра=True, память=6000)

    guard.tick()
    guard.tick()
    guard.tick()

    assert recognizer.смены == ["small"]


def test_выход_из_игры_возвращает_обычную():
    recognizer = ФальшивыйRecognizer("small")

    _сторож(recognizer, игра=False, память=6000).tick()

    assert recognizer.смены == ["medium"]


def test_сбой_признака_не_роняет_сторожа():
    """Сторож живёт в фоновом потоке. Упадёт — Джони останется на той модели,
    что была, и никто об этом не узнает до перезапуска."""
    recognizer = ФальшивыйRecognizer()

    def взрыв():
        raise OSError("реестр недоступен")

    guard = Guard(
        recognizer,
        ФальшивыеНастройки(),
        game_running=взрыв,
        free_mb=lambda: 6000,
    )

    assert guard.tick() == "medium"
    assert recognizer.смены == []
```

- [ ] **Step 2: Убедиться, что тест падает**

Запустить: `D:/Python/python.exe -m pytest tests/test_game_watch.py -q`
Ожидается: FAIL, `ImportError: cannot import name 'Guard' from 'johnny.game_watch'`.

- [ ] **Step 3: Дописать класс `Guard`**

Добавить в начало `johnny/game_watch.py` к импортам:

```python
import threading
```

И в конец файла:

```python
_POLL_SECONDS = 5.0


class Guard:
    """Фоновый сторож: следит за признаками и просит сменить модель.

    Про Whisper знает ровно одно — что у распознавателя есть use_model и
    model_name. Признаки берёт функциями-аргументами, поэтому проверяется без
    видеокарты и без Steam.
    """

    def __init__(self, recognizer, settings, *, game_running=steam_game_running,
                 free_mb=free_vram_mb, poll_seconds: float = _POLL_SECONDS):
        self._recognizer = recognizer
        self._settings = settings
        self._game_running = game_running
        self._free_mb = free_mb
        self._poll = poll_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def enabled(self) -> bool:
        return bool(getattr(self._settings, "whisper_model_gaming", ""))

    def tick(self) -> str:
        """Один опрос. Возвращает имя модели, которая должна стоять сейчас.

        Сбой признака не должен ронять поток: сторож фоновый, и его смерть
        осталась бы незамеченной до перезапуска Джони — а Whisper при этом
        навсегда застрял бы на той модели, что была в тот момент.
        """
        try:
            game = bool(self._game_running())
        except Exception:
            logger.warning("Не удалось прочитать признак игры", exc_info=True)
            game = False
        try:
            free = self._free_mb()
        except Exception:
            logger.warning("Не удалось прочитать свободную видеопамять", exc_info=True)
            free = None

        current = self._recognizer.model_name
        wanted = decide(
            current,
            normal_model=self._settings.whisper_model,
            gaming_model=self._settings.whisper_model_gaming,
            game_running=game,
            free_mb=free,
            low_mb=self._settings.gpu_guard_low_mb,
            high_mb=self._settings.gpu_guard_high_mb,
        )
        if wanted != current:
            self._recognizer.use_model(wanted)
        return wanted

    def start(self) -> None:
        """Запустить опрос в фоне. Выключенный настройкой сторож не стартует."""
        if not self.enabled or self._thread is not None:
            return

        def loop() -> None:
            while not self._stop.wait(self._poll):
                try:
                    self.tick()
                except Exception:
                    logger.exception("Сторож видеопамяти упал на опросе")

        self._thread = threading.Thread(target=loop, daemon=True)
        self._thread.start()
        logger.info(
            "Сторож видеопамяти запущен: во время игры модель %r",
            self._settings.whisper_model_gaming,
        )

    def stop(self) -> None:
        self._stop.set()
```

- [ ] **Step 4: Прогнать тесты сторожа**

Запустить: `D:/Python/python.exe -m pytest tests/test_game_watch.py -q`
Ожидается: PASS, 11 passed.

- [ ] **Step 5: Подключить в трее**

В `johnny/tray.py` добавить `game_watch` в импорт из пакета (строка 11):

```python
from . import autostart, game_watch, panel_tools, single_instance
```

После строки `mic = Microphone().open()` и её логирования (после строки 87),
перед `listener_available = ...`:

```python
    # Сторож видеопамяти: пока идёт игра, Whisper уходит на маленькую модель и
    # возвращает карте ~1.5 ГБ. Выключен, если whisper_model_gaming пуст.
    guard = game_watch.Guard(recognizer, config.settings)
    guard.start()
```

В функции `quit_app` (строка 155) первой строкой после `logging.info`:

```python
        guard.stop()
```

- [ ] **Step 6: Подключить в консольном запуске**

В `johnny/app.py` добавить импорт рядом с прочими импортами из пакета:

```python
from . import game_watch
```

После создания `recognizer` (строка 339) и до `listener = Listener(...)`:

```python
    # Тот же сторож, что и в трее: консольный запуск не должен вести себя иначе.
    guard = game_watch.Guard(recognizer, config.settings)
    guard.start()
```

Заменить блок `try:` … `except KeyboardInterrupt:` (строки 352–358) так, чтобы
сторож останавливался при любом выходе:

```python
    try:
        # Цикл живёт в контроллере: две ветки вызова (слитно / с паузой)
        # переписывать здесь второй раз нельзя, копии разъедутся.
        with Microphone() as mic:
            controller.run(listener, recognizer, mic)
    except KeyboardInterrupt:
        print("Выход.")
    finally:
        guard.stop()
```

- [ ] **Step 7: Прогнать весь набор**

Запустить: `D:/Python/python.exe -m pytest tests/ -q --ignore=tests/test_face_index_connector.py --ignore=tests/test_discord_actions.py`
Ожидается: PASS, **1125 passed** (1121 после Task 3 + 4 новых).

- [ ] **Step 8: Проверить вживую**

Запустить Джони: `D:/Python/pythonw.exe johnny_tray.pyw`, дождаться в
`johnny.log` строки `Джони запущен: Слушает` (около 3.5 минут — грузится vosk) и
строки `Сторож видеопамяти запущен`.

Затем запустить любую игру в Steam и проверить в `johnny.log` появление
`Whisper переключён на модель 'small'`. Выйти из игры — ожидается
`Whisper переключён на модель 'medium'`.

Замерить видеопамять до и во время игры:
`nvidia-smi --query-gpu=memory.free --format=csv`
Ожидается: во время игры Джони держит примерно на 1.5 ГБ меньше.

- [ ] **Step 9: Коммит**

```bash
git add johnny/game_watch.py tests/test_game_watch.py johnny/tray.py johnny/app.py
git commit -m "Сторож видеопамяти: подмена модели Whisper на время игры"
```
