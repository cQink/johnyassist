# Команда одной фразой (слитный вызов) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Дать Джони понимать «Джони, громкость пять» одной слитной фразой, сохранив звук-подтверждение для случая, когда после имени была пауза.

**Architecture:** Микрофон открывается один раз и живёт всё время работы; Vosk (вейкворд) и Whisper (команда) читают блоки из общего буфера, поэтому звук между именем и командой не теряется. Кольцевой пре-ролл 1.5с даёт доступ к самому имени — оно попадает в расшифровку Whisper и работает якорем: по нему отрезается префикс и отсекаются ложные срабатывания Vosk.

**Tech Stack:** Python 3.14, sounddevice (`RawInputStream`, int16 16кГц), vosk, faster-whisper, numpy, pytest.

**Спека:** `docs/superpowers/specs/2026-07-27-one-phrase-command-design.md`

## Global Constraints

- Запуск тестов всегда: `.\.venv\Scripts\python.exe -m pytest tests/ -q` из `D:\assistent`. Базовая линия до начала работы — **221 passed**.
- Все комментарии, docstring'и, сообщения коммитов и реплики Джони — **по-русски**, как во всём проекте.
- Тесты не должны требовать живого микрофона, звуковой карты или загрузки моделей: всё через фейки.
- Частота дискретизации 16000 Гц, блок 4000 фреймов (0.25с), формат int16 моно — эти значения зафиксированы, менять нельзя (на 0.25с настроен RMS-порог тишины).
- Порог паузы — ровно `0.75` секунды (пользователь выбрал 0.7, округление вверх до сетки блоков; 0.75·16000/4000 = 3.0 блока ровно, поэтому целочисленная арифметика существующего рекордера даёт точное значение без изменений).
- RMS-порог тишины остаётся `0.01`, арифметика блоков в `record_until_silence` остаётся целочисленной (`int()`, не `ceil`) — она уже настроена, любое изменение сдвинуло бы длину хвоста после команды.
- Опасные действия (`shutdown`/`restart`/`sleep`) по-прежнему проходят только точное совпадение через `router.is_unsafe_action` — ни один шаг плана не имеет права ослабить эту проверку.

---

## File Structure

| Файл | Ответственность |
|---|---|
| `johnny/audio.py` | **новый.** Владение микрофоном, буфер блоков с пре-роллом, запись до тишины. Ничего не знает ни про Vosk, ни про Whisper. |
| `johnny/listener.py` | Вейкворд через Vosk + чистое отрезание имени из текста. Свой аудиопоток больше не держит. |
| `johnny/recognizer.py` | Только «аудио → текст» (Whisper, подсказка, фильтр эха). Запись больше не его дело. |
| `johnny/controller.py` | Оркестрация цикла: две ветки (слитно / с паузой), восстановление, история. |
| `johnny/app.py` | `handle_command` с политикой (`speak_failures`, `use_brain`) и результатом `Outcome`; `run()` через контроллер. |
| `johnny/tray.py` | Проводка: создаёт микрофон, отдаёт в контроллер, закрывает на выходе. |
| `tests/test_audio.py` | **новый.** Буфер, пре-ролл, таймаут, запись до тишины. |
| `tests/test_wake_strip.py` | **новый.** Отрезание имени. |

---

### Task 1: Буфер блоков и владение микрофоном

**Files:**
- Create: `johnny/audio.py`
- Test: `tests/test_audio.py`

**Interfaces:**
- Consumes: ничего.
- Produces:
  - `johnny.audio.SAMPLE_RATE = 16000`, `BLOCK_FRAMES = 4000`
  - `class MicrophoneError(RuntimeError)`
  - `class BlockBuffer` — `put(raw: bytes) -> None`, `read_block(timeout: float = 2.0) -> bytes`, `preroll() -> bytes`, `flush() -> None`
  - `class Microphone(BlockBuffer)` — контекстный менеджер, `close() -> None`

Разделение на два класса намеренное: вся логика (очередь, кольцо, таймаут) живёт в `BlockBuffer` и полностью тестируется без железа, а `Microphone` — тонкая обёртка над `sounddevice`, которую тестировать нечем и не нужно. Тесты последующих задач используют `BlockBuffer` как фейковый микрофон — отдельный фейк-класс не понадобится.

- [ ] **Step 1: Написать падающие тесты буфера**

Создать `tests/test_audio.py`:

```python
import pytest

from johnny.audio import BLOCK_FRAMES, BlockBuffer, MicrophoneError


def _block(value: int = 0) -> bytes:
    """Блок нужного размера, заполненный одним значением int16."""
    return value.to_bytes(2, "little", signed=True) * BLOCK_FRAMES


def test_read_block_returns_what_was_put():
    buf = BlockBuffer()
    buf.put(_block(1))
    assert buf.read_block() == _block(1)


def test_read_block_raises_on_timeout():
    # Микрофон отвалился: callback перестал срабатывать. Без таймаута чтение
    # висело бы вечно, и защита контроллера (счётчик сбоев подряд) никогда
    # бы не сработала.
    buf = BlockBuffer()
    with pytest.raises(MicrophoneError):
        buf.read_block(timeout=0.01)


def test_preroll_keeps_only_recently_read_blocks():
    # Кольцо на 1.5с при блоке 0.25с — это 6 блоков. Седьмой вытесняет первый.
    buf = BlockBuffer(preroll_seconds=1.5)
    for i in range(1, 8):
        buf.put(_block(i))
        buf.read_block()
    assert buf.preroll() == b"".join(_block(i) for i in range(2, 8))


def test_preroll_holds_only_read_blocks():
    # В пре-ролл попадает то, что уже прочитано (Vosk это прослушал), а не
    # то, что ещё лежит в очереди: иначе снимок «назад во времени» захватил
    # бы будущее и то же самое аудио попало бы в запись дважды.
    buf = BlockBuffer()
    buf.put(_block(1))
    buf.put(_block(2))
    buf.read_block()
    assert buf.preroll() == _block(1)


def test_preroll_does_not_consume():
    buf = BlockBuffer()
    buf.put(_block(1))
    buf.read_block()
    assert buf.preroll() == _block(1)
    assert buf.preroll() == _block(1)


def test_flush_clears_queue_and_preroll():
    # Флаш нужен после собственного звука Джони: иначе он запишет свой «пик»
    # или услышит в ответе TTS собственное имя и разбудит сам себя.
    buf = BlockBuffer()
    buf.put(_block(1))
    buf.read_block()
    buf.put(_block(2))
    buf.flush()
    assert buf.preroll() == b""
    with pytest.raises(MicrophoneError):
        buf.read_block(timeout=0.01)


def test_overflow_drops_oldest_block():
    # Пока Джони выполняет команду, никто не читает очередь. Потолок не даёт
    # ей расти в памяти бесконечно, а выбрасывается САМОЕ СТАРОЕ — свежий
    # звук всегда важнее протухшего.
    buf = BlockBuffer(max_blocks=2)
    buf.put(_block(1))
    buf.put(_block(2))
    buf.put(_block(3))
    assert buf.read_block() == _block(2)
    assert buf.read_block() == _block(3)
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_audio.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'johnny.audio'`

- [ ] **Step 3: Написать `johnny/audio.py`**

```python
import math
import queue
from collections import deque

SAMPLE_RATE = 16000
BLOCK_FRAMES = 4000  # 0.25 c

# Сколько звука держим «назад во времени». Хватает, чтобы в снимок попало
# само слово «Джони» вместе с задержкой Vosk на его распознавание: имя
# звучит ~0.5с, блок 0.25с, остальное — запас на паузу перед именем.
_PREROLL_SECONDS = 1.5

# Потолок очереди: 200 блоков = 50с звука. Очередь наполняется, пока Джони
# занят выполнением команды и никто её не читает; без потолка долгая команда
# (или зависший обработчик) растила бы её в памяти неограниченно.
_MAX_BLOCKS = 200

# Сколько ждём блок, прежде чем считать микрофон мёртвым. С постоянно
# открытым потоком «микрофон отключили» больше не выглядит как исключение в
# конструкторе: callback просто перестаёт срабатывать. Без таймаута чтение
# висело бы вечно, и защита AssistantController.run (пауза + стоп после 5
# сбоев подряд) никогда бы не получила управление.
_READ_TIMEOUT = 2.0


class MicrophoneError(RuntimeError):
    """Микрофон не отдаёт звук (отключён, занят, индекс устройства протух)."""


class BlockBuffer:
    """Очередь блоков звука с кольцом последних прочитанных.

    Отделён от Microphone намеренно: здесь вся логика и ноль зависимостей от
    звукового железа, поэтому она полностью проверяется тестами, а в тестах
    других модулей этот же класс работает фейковым микрофоном.
    """

    def __init__(
        self,
        preroll_seconds: float = _PREROLL_SECONDS,
        max_blocks: int = _MAX_BLOCKS,
    ):
        self._queue: queue.Queue = queue.Queue(maxsize=max_blocks)
        ring_size = math.ceil(preroll_seconds * SAMPLE_RATE / BLOCK_FRAMES)
        self._ring: deque = deque(maxlen=ring_size)

    def put(self, raw: bytes) -> None:
        """Положить блок (вызывается из callback звукового потока)."""
        try:
            self._queue.put_nowait(raw)
        except queue.Full:
            # Выбрасываем самое старое, а не новое: протухший звук ценности
            # не имеет, а свежий — это то, что человек говорит прямо сейчас.
            try:
                self._queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self._queue.put_nowait(raw)
            except queue.Full:
                pass

    def read_block(self, timeout: float = _READ_TIMEOUT) -> bytes:
        """Следующий блок. Прочитанное попадает в кольцо пре-ролла."""
        try:
            raw = self._queue.get(timeout=timeout)
        except queue.Empty:
            raise MicrophoneError(f"нет звука с микрофона {timeout} с")
        self._ring.append(raw)
        return raw

    def preroll(self) -> bytes:
        """Снимок последних прочитанных блоков. Не очищает кольцо."""
        return b"".join(self._ring)

    def flush(self) -> None:
        """Забыть всё: и накопленное в очереди, и кольцо."""
        with self._queue.mutex:
            self._queue.queue.clear()
        self._ring.clear()


class Microphone(BlockBuffer):
    """Единственный владелец устройства: поток открыт всё время работы.

    Раньше поток открывали дважды на каждую команду (Vosk, затем Whisper), и
    между ними терялось всё сказанное — из-за этого «Джони, громкость пять»
    одной фразой было невозможно.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._stream = None

    def _callback(self, indata, frames, time, status) -> None:
        self.put(bytes(indata))

    def open(self) -> "Microphone":
        import sounddevice as sd

        self._stream = sd.RawInputStream(
            samplerate=SAMPLE_RATE,
            blocksize=BLOCK_FRAMES,
            dtype="int16",
            channels=1,
            callback=self._callback,
        )
        self._stream.start()
        return self

    def close(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def __enter__(self) -> "Microphone":
        return self.open()

    def __exit__(self, *exc) -> None:
        self.close()
```

- [ ] **Step 4: Убедиться, что тесты проходят**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_audio.py -q`
Expected: PASS, 7 passed

- [ ] **Step 5: Коммит**

```bash
git add johnny/audio.py tests/test_audio.py
git commit -m "feat: общий буфер блоков микрофона с кольцевым пре-роллом"
```

---

### Task 2: Запись до тишины поверх общего буфера

**Files:**
- Modify: `johnny/audio.py`
- Test: `tests/test_audio.py`

**Interfaces:**
- Consumes: `BlockBuffer`, `MicrophoneError`, `BLOCK_FRAMES`, `SAMPLE_RATE` из Task 1.
- Produces:
  - `to_float32(raw: bytes) -> np.ndarray`
  - `is_silent(raw: bytes) -> bool`
  - `record_until_silence(mic, max_seconds: float = 8.0, silence_seconds: float = 1.2, start_timeout: float = 6.0) -> tuple[bytes, bool]` — возвращает `(звук, заговорил_ли)`

Логика переезжает из `Recognizer._record_until_silence` (`johnny/recognizer.py:131-164`) практически как есть. Единственное содержательное дополнение — второй элемент кортежа: флаг «речь вообще началась». Именно по нему контроллер отличает слитную фразу от паузы. Целочисленную арифметику блоков **не трогать**: она настроена, `int(1.2*16000/4000) = 4` блока хвостовой тишины — это текущее живое поведение.

- [ ] **Step 1: Написать падающие тесты**

Дописать в конец `tests/test_audio.py`:

```python
import numpy as np

from johnny.audio import is_silent, record_until_silence, to_float32

_LOUD = 8000  # заметно выше RMS-порога 0.01 (это ~328 в int16)


def _fill(buf, *blocks):
    for value in blocks:
        buf.put(_block(value))
    return buf


def test_to_float32_scales_int16_to_unit_range():
    assert to_float32(_block(0)).max() == 0.0
    assert abs(to_float32(_block(32767)).max() - 1.0) < 0.001


def test_is_silent_distinguishes_silence_from_speech():
    assert is_silent(_block(0)) is True
    assert is_silent(_block(_LOUD)) is False


def test_no_speech_at_all_reports_not_started():
    # Ты сказал «Джони» и замолчал — контроллер по этому флагу поймёт, что
    # пора играть звук-подтверждение.
    buf = _fill(BlockBuffer(), 0, 0, 0, 0)
    raw, started = record_until_silence(buf, start_timeout=1.0)
    assert started is False
    assert raw == b""


def test_leading_silence_is_not_recorded():
    buf = _fill(BlockBuffer(), 0, 0, _LOUD, 0, 0, 0, 0, 0)
    raw, started = record_until_silence(buf, silence_seconds=1.0, start_timeout=2.0)
    assert started is True
    # Записано начиная с речи: сам громкий блок + хвост тишины до отсечки.
    assert raw.startswith(_block(_LOUD))


def test_recording_stops_after_silence_following_speech():
    # silence_seconds=1.0 → int(1.0*16000/4000) = 4 тихих блока подряд.
    buf = _fill(BlockBuffer(), _LOUD, 0, 0, 0, 0, _LOUD, _LOUD)
    raw, started = record_until_silence(buf, silence_seconds=1.0, start_timeout=2.0)
    assert started is True
    assert len(raw) == 5 * BLOCK_FRAMES * 2  # речь + 4 тихих, дальше не читали


def test_recording_respects_max_seconds():
    buf = _fill(BlockBuffer(), *([_LOUD] * 10))
    raw, _ = record_until_silence(buf, max_seconds=1.0, start_timeout=2.0)
    assert len(raw) == 4 * BLOCK_FRAMES * 2  # 1.0с = 4 блока


def test_silence_counter_resets_on_speech():
    # Пауза внутри фразы («Джони… эээ… громкость пять») не должна обрывать
    # запись: счётчик тишины обязан сбрасываться на каждом громком блоке.
    buf = _fill(BlockBuffer(), _LOUD, 0, 0, _LOUD, 0, 0, 0, 0, _LOUD)
    raw, _ = record_until_silence(buf, silence_seconds=1.0, start_timeout=2.0)
    assert len(raw) == 8 * BLOCK_FRAMES * 2


def test_dead_microphone_propagates_error():
    # Пустой буфер = микрофон молчит совсем. Ошибка должна дойти до
    # контроллера, а не превратиться в «тишину» (иначе Джони делал бы вид,
    # что всё хорошо, при отключённом микрофоне).
    with pytest.raises(MicrophoneError):
        record_until_silence(BlockBuffer(), start_timeout=1.0)
```

Обрати внимание на `test_dead_microphone_propagates_error`: `read_block` использует свой таймаут по умолчанию (2с), поэтому тест длится около двух секунд. Это единственный медленный тест в наборе, так и должно быть.

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_audio.py -q`
Expected: FAIL — `ImportError: cannot import name 'is_silent' from 'johnny.audio'`

- [ ] **Step 3: Дописать функции в `johnny/audio.py`**

В начало файла, к остальным импортам:

```python
import numpy as np
```

В конец файла:

```python
# Порог тишины по RMS блока. Значение настроено на блоке 0.25с и живом
# микрофоне пользователя — не менять вместе с размером блока.
_SILENCE_RMS = 0.01


def to_float32(raw: bytes) -> np.ndarray:
    """int16-байты с микрофона → float32 [-1, 1], как ждёт Whisper."""
    return np.frombuffer(raw, dtype="int16").astype("float32") / 32768.0


def is_silent(raw: bytes) -> bool:
    """Блок тише порога? Пустой блок считаем тишиной."""
    samples = to_float32(raw)
    if samples.size == 0:
        return True
    return bool(np.sqrt(np.mean(samples**2)) < _SILENCE_RMS)


def record_until_silence(
    mic,
    max_seconds: float = 8.0,
    silence_seconds: float = 1.2,
    start_timeout: float = 6.0,
) -> tuple[bytes, bool]:
    """Записать одну реплику: от начала речи до тишины.

    Возвращает (звук, заговорил_ли). Второй элемент — то, на чём держится
    весь слитный режим: речь пошла сразу после имени → человек говорит одной
    фразой; тишина дольше start_timeout → он позвал и ждёт подтверждения.

    Арифметика блоков целочисленная и намеренно не округляется вверх: она
    настроена на живом голосе, ceil удлинил бы хвост после команды.
    """
    frames: list[bytes] = []
    silent_blocks = 0
    started = False
    waited_blocks = 0
    needed_silent = int(silence_seconds * SAMPLE_RATE / BLOCK_FRAMES)
    max_blocks = int(max_seconds * SAMPLE_RATE / BLOCK_FRAMES)
    start_timeout_blocks = int(start_timeout * SAMPLE_RATE / BLOCK_FRAMES)

    while len(frames) < max_blocks:
        block = mic.read_block()
        silent = is_silent(block)

        if not started:
            # Ждём НАЧАЛА речи — тишину в запись не пишем.
            if silent:
                waited_blocks += 1
                if waited_blocks >= start_timeout_blocks:
                    break  # так и не заговорили
                continue
            started = True

        frames.append(block)
        if silent:
            silent_blocks += 1
            if silent_blocks >= needed_silent:  # тишина ПОСЛЕ речи — конец
                break
        else:
            silent_blocks = 0

    return b"".join(frames), started
```

- [ ] **Step 4: Убедиться, что тесты проходят**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_audio.py -q`
Expected: PASS, 15 passed

- [ ] **Step 5: Коммит**

```bash
git add johnny/audio.py tests/test_audio.py
git commit -m "feat: запись до тишины поверх общего микрофона, с флагом «заговорил»"
```

---

### Task 3: Отрезание имени и переезд Listener на общий микрофон

**Files:**
- Modify: `johnny/listener.py`
- Test: `tests/test_wake_strip.py` (создать)

**Interfaces:**
- Consumes: `BlockBuffer.read_block()` из Task 1 (в тестах — как фейковый микрофон).
- Produces:
  - `strip_wake_word(text: str, variants: list[str]) -> tuple[str, bool]`
  - `Listener.wake_words: list[str]` (публичный атрибут вместо приватного `_wake_words`)
  - `Listener.wait_for_wake_word(mic) -> None` (новый обязательный аргумент)

Два ключевых изменения в `Listener`, помимо сигнатуры: он больше **не открывает свой поток** и больше **не чистит хвост очереди** (нынешние строки 41–42) — этот хвост и есть тот звук, за которым мы пришли. Сбрасывается только внутреннее состояние Vosk.

- [ ] **Step 1: Написать падающие тесты отрезания имени**

Создать `tests/test_wake_strip.py`:

```python
from johnny.listener import strip_wake_word

VARIANTS = ["джони", "джонни", "джани"]


def test_strips_name_from_start():
    assert strip_wake_word("джони громкость пять", VARIANTS) == ("громкость пять", True)


def test_strips_misheard_variants():
    for said in ("джонни громкость пять", "джани громкость пять"):
        assert strip_wake_word(said, VARIANTS) == ("громкость пять", True)


def test_strips_punctuation_and_case():
    assert strip_wake_word("Джони, громкость пять", VARIANTS) == ("громкость пять", True)


def test_strips_near_miss_of_whisper():
    # Whisper пишет имя как попало; нечёткое сравнение (0.75) обязано ловить
    # то, чего нет в списке вариантов дословно.
    assert strip_wake_word("джоник громкость пять", VARIANTS) == ("громкость пять", True)


def test_cuts_up_to_last_occurrence_in_head():
    # Пре-ролл 1.5с иногда захватывает хвост предыдущей речи. Резать надо до
    # ПОСЛЕДНЕГО имени, иначе в команду попадёт мусор перед ним.
    assert strip_wake_word("ага джони громкость", VARIANTS) == ("громкость", True)


def test_ignores_match_beyond_head():
    # Дальше третьего слова не смотрим: случайное созвучие в конце длинной
    # фразы срезало бы всю команду целиком.
    said = "открой канал джонни на твиче"
    assert strip_wake_word(said, VARIANTS) == (said, False)


def test_reports_not_found_when_name_absent():
    # Vosk услышал имя, Whisper — нет. Флаг False понижает доверие: модель
    # к такой фразе уже не подключаем.
    assert strip_wake_word("да я говорю ему", VARIANTS) == ("да я говорю ему", False)


def test_yo_is_normalized():
    assert strip_wake_word("джёни громкость", VARIANTS) == ("громкость", True)


def test_name_alone_gives_empty_command():
    # Позвал слитно, а команды не сказал — текст пустой, но имя найдено:
    # контроллер по этому сочетанию уйдёт в восстановление (звук + ожидание).
    assert strip_wake_word("джони", VARIANTS) == ("", True)


def test_empty_text():
    assert strip_wake_word("", VARIANTS) == ("", False)
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_wake_strip.py -q`
Expected: FAIL — `ImportError: cannot import name 'strip_wake_word' from 'johnny.listener'`

- [ ] **Step 3: Переписать `johnny/listener.py` целиком**

```python
import difflib
import json
import re

from vosk import KaldiRecognizer, Model

# Сколько первых слов расшифровки просматриваем в поисках имени. Дальше не
# смотрим: случайное созвучие в конце длинной фразы («открой канал джонни на
# твиче») срезало бы всю команду целиком.
_HEAD_WORDS = 3

# Порог нечёткого сравнения с вариантами имени. Ниже 0.72 (порога роутера),
# потому что здесь цена ошибки мала: лишнее срезанное слово роутер переживёт,
# а вот НЕ узнать имя — значит потерять якорь и понизить доверие зря.
_NAME_RATIO = 0.75


def _words(text: str) -> list[str]:
    """Слова в нижнем регистре, ё→е, без пунктуации — как в recognizer."""
    return re.findall(r"[a-zа-я0-9]+", text.lower().replace("ё", "е"))


def strip_wake_word(text: str, variants: list[str]) -> tuple[str, bool]:
    """Убрать имя из начала слитной фразы.

    Возвращает (текст без имени, найдено ли имя). Второй элемент — тот самый
    якорь: Vosk (small) услышал имя, а Whisper (medium) на той же записи его
    не видит — значит Vosk скорее всего ошибся, и доверие к фразе надо
    понизить. Выбрасывать такое молча нельзя: Whisper мог проглотить быстрое
    тихое «Джони», а потерять настоящую команду хуже, чем выполнить лишнюю.

    Текст возвращается нормализованным (слова через пробел, нижний регистр,
    ё→е). Роутер всё равно нормализует вход, поэтому потери нет, зато история
    и логи выглядят одинаково для обеих веток.
    """
    said = _words(text)
    known = [w.lower().replace("ё", "е") for w in variants]
    cut = -1
    for index, word in enumerate(said[:_HEAD_WORDS]):
        for variant in known:
            if difflib.SequenceMatcher(None, word, variant).ratio() >= _NAME_RATIO:
                cut = index  # именно последнее совпадение в голове фразы
                break
    if cut < 0:
        return " ".join(said), False
    return " ".join(said[cut + 1 :]), True


class Listener:
    """Ловит слово-активатор через Vosk (офлайн, без ключей и регистрации)."""

    def __init__(self, wake_word: str, model_path: str):
        # wake_word может содержать несколько вариантов через "|"
        # (Vosk слышит «джони» как «джонни»/«джани» — принимаем все).
        self.wake_words = [w.strip() for w in wake_word.lower().split("|") if w.strip()]
        self._model = Model(model_path)
        self._recognizer = KaldiRecognizer(self._model, 16000)

    def wait_for_wake_word(self, mic) -> None:
        """Читать блоки из ОБЩЕГО микрофона, пока не прозвучит имя.

        Свой поток больше не открывается и хвост очереди больше НЕ чистится:
        именно этот хвост — начало команды, сказанной слитно с именем. Раньше
        он выбрасывался, и слитный вызов был невозможен физически.
        """
        while True:
            data = mic.read_block()
            if self._recognizer.AcceptWaveform(data):
                text = json.loads(self._recognizer.Result()).get("text", "")
            else:
                text = json.loads(self._recognizer.PartialResult()).get("partial", "")
            lowered = text.lower()
            if any(word in lowered for word in self.wake_words):
                self._recognizer.Reset()  # состояние Vosk — да, звук — нет
                return

    def close(self) -> None:
        pass
```

- [ ] **Step 4: Убедиться, что тесты проходят**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_wake_strip.py -q`
Expected: PASS, 10 passed

- [ ] **Step 5: Прогнать весь набор**

Run: `.\.venv\Scripts\python.exe -m pytest tests/ -q`
Expected: PASS. Ни один существующий тест `Listener` не трогает (тестов на него нет — он требует модель Vosk), поэтому падений быть не должно.

- [ ] **Step 6: Коммит**

```bash
git add johnny/listener.py tests/test_wake_strip.py
git commit -m "feat: отрезание имени из слитной фразы, Listener на общем микрофоне"
```

---

### Task 4: Recognizer — только «аудио → текст», имя в подсказке

**Files:**
- Modify: `johnny/recognizer.py`
- Modify: `tests/test_recognizer_echo.py:54`

**Interfaces:**
- Consumes: `johnny.audio.to_float32` (вызывает контроллер, не сам Recognizer).
- Produces: `Recognizer.transcribe(audio: np.ndarray) -> str` вместо `listen_command()`.

Две правки в одном шаге, потому что они связаны: чтобы флаг «имя найдено» из Task 3 был надёжным, Whisper должен уверенно писать «Джони», а для этого имя обязано быть в подсказке.

- [ ] **Step 1: Убрать запись из recognizer.py**

В `johnny/recognizer.py` удалить целиком метод `_record_until_silence` (строки 131–164) и заменить `listen_command` на:

```python
    def transcribe(self, audio) -> str:
        """Аудио (float32, 16 кГц) → текст. Запись — не наше дело."""
        segments, _ = self._model.transcribe(
            audio,
            language="ru",
            vad_filter=True,
            initial_prompt=self._prompt,
            beam_size=5,
            # Команды независимы — не тянем контекст прошлой фразы (убирает
            # «залипания»/галлюцинации Whisper на коротких/тихих записях).
            condition_on_previous_text=False,
            # Мягче отбрасываем «нет речи» и даём температуре откатиться —
            # меньше пустых/выдуманных результатов на шумных фрагментах.
            no_speech_threshold=0.5,
            temperature=[0.0, 0.2, 0.4, 0.6],
        )
        text = " ".join(seg.text.strip() for seg in segments).strip()
        if is_prompt_echo(text, self._prompt):
            # Whisper продолжил подсказку вместо расшифровки — считаем, что не
            # расслышали, иначе Джони выполнит собственный словарь как команду.
            logger.warning("Отброшено эхо подсказки: %r", text)
            return ""
        return text
```

Также удалить теперь неиспользуемые импорты `sounddevice as sd` и константы `_SAMPLE_RATE`/`_BLOCK` (они переехали в `audio.py` как `SAMPLE_RATE`/`BLOCK_FRAMES`). `numpy` остаётся — он нужен в аннотациях и не мешает.

- [ ] **Step 2: Добавить имя в шапку подсказки**

В `johnny/recognizer.py` заменить `_BASE_PROMPT`:

```python
# Короткая шапка + словарь имён. Целых командных фраз здесь СОЗНАТЕЛЬНО нет:
# initial_prompt обрезается на 224 токенах, а фразы вытеснили бы имена
# собственные — именно на них Whisper ошибается чаще всего.
# «Джони» в шапке не для красоты: в слитном режиме имя попадает в запись, и
# по нему strip_wake_word решает, доверять ли срабатыванию Vosk. Не подскажешь
# имя — Whisper напишет его как попало, и защита от ложных срабатываний
# начнёт отваливаться на верных командах.
_BASE_PROMPT = "Русские голосовые команды. Джони."
```

- [ ] **Step 3: Починить тест, который завязан на длину шапки**

`is_prompt_echo` берёт длину шапки из `len(_words(_BASE_PROMPT))`. Шапка выросла с 3 слов до 4, поэтому в `tests/test_recognizer_echo.py:54` трёхсловная альтернативная шапка перестанет совпадать. Заменить в `test_header_is_derived_from_own_prompt_argument`:

```python
    other_prompt = "Совсем другая шапка тут. алиас1 алиас2 алиас3 алиас4"
    assert is_prompt_echo("Совсем другая шапка тут", other_prompt) is True
```

Комментарий в тесте выше по коду тоже поправить: «тоже 3 слова» → «тоже 4 слова, как и у `_BASE_PROMPT`».

- [ ] **Step 4: Измерить бюджет токенов реальным токенизатором**

Имя в шапке съедает токены, а лимит Whisper — 224. Замерить точно, а не на глаз:

```bash
.\.venv\Scripts\python.exe -c "from huggingface_hub import hf_hub_download; from tokenizers import Tokenizer; from johnny.config import load_config; from johnny.recognizer import build_vocabulary, _BASE_PROMPT; c=load_config('config'); v=build_vocabulary(c.apps,c.channels,c.commands); p=_BASE_PROMPT+' '+v; t=Tokenizer.from_file(hf_hub_download('Systran/faster-whisper-medium','tokenizer.json')); print('слов словаря:', len(v.split()), 'токенов всего:', len(t.encode(p).ids))"
```

Expected: печатает два числа. Если «токенов всего» > 224 — уменьшать `_MAX_VOCAB_WORDS` (сейчас 90) на единицу и повторять запуск, пока не станет ≤ 224. Итоговое число вписать в константу и обновить обоснование в комментарии над ней, указав новые замеренные цифры вместо «90 слов = 220 токенов».

- [ ] **Step 5: Прогнать тесты**

Run: `.\.venv\Scripts\python.exe -m pytest tests/ -q`
Expected: PASS. `test_vocabulary_respects_word_budget` и соседи читают `_MAX_VOCAB_WORDS` из модуля, поэтому переживут любое новое значение.

- [ ] **Step 6: Коммит**

```bash
git add johnny/recognizer.py tests/test_recognizer_echo.py
git commit -m "refactor: Recognizer только расшифровывает; имя в подсказке Whisper"
```

---

### Task 5: `handle_command` — политика ответа и результат

**Files:**
- Modify: `johnny/app.py:21-46`
- Test: `tests/test_app.py`

**Interfaces:**
- Consumes: ничего нового.
- Produces:
  - `@dataclass class Outcome: via: str; handled: bool = True`
  - `handle_command(text, config, speaker, *, speak_failures: bool = True, use_brain: bool = True) -> Outcome`

Значения по умолчанию воспроизводят сегодняшнее поведение — существующие вызовы и все восемь тестов `test_app.py` остаются валидными (они проверяют реплики, а не возвращаемое значение).

Граница `speak_failures` проведена намеренно: он глушит **только** семейство «не понял» («Не расслышал», «Не понял команду»). Сбои выполнения («Не знаю такой программы», «Не смог выполнить команду») озвучиваются всегда — команда была понята, и молчать в ответ на неё нельзя.

- [ ] **Step 1: Написать падающие тесты**

Дописать в конец `tests/test_app.py`:

```python
def test_outcome_reports_handled_on_success(monkeypatch):
    import johnny.app as app
    from johnny.actions import ActionResult

    monkeypatch.setattr(app, "execute", lambda routed, apps, channels: ActionResult(True, "Запускаю дота"))
    outcome = handle_command("запусти дота", _config(), SpySpeaker())
    assert outcome.handled is True
    assert outcome.via == "точно"


def test_silent_mode_swallows_not_understood(monkeypatch):
    # Слитный режим: фраза ни во что не сошлась. Джони обязан промолчать —
    # случайное «Джони» в войсчате не должно вызывать реплик вслух.
    import johnny.app as app

    monkeypatch.setattr(app, "interpret", lambda text, commands, providers: None)
    sp = SpySpeaker()
    outcome = handle_command("да я говорю ему", _config(), sp, speak_failures=False)
    assert sp.said == []
    assert outcome.handled is False


def test_silent_mode_still_speaks_execution_failures(monkeypatch):
    # А вот сбой ВЫПОЛНЕНИЯ озвучивается всегда: команду поняли, значит
    # человек ждёт ответа.
    import johnny.app as app

    def boom(routed, apps, channels):
        raise RuntimeError("bad path")

    monkeypatch.setattr(app, "execute", boom)
    sp = SpySpeaker()
    outcome = handle_command("запусти дота", _config(), sp, speak_failures=False)
    assert sp.said == ["Не смог выполнить команду"]
    assert outcome.handled is True


def test_use_brain_false_skips_model(monkeypatch):
    # Имя не подтвердилось Whisper'ом — модель-корректор не зовём вовсе:
    # её работа превратить невнятицу в ближайшую команду, и на случайной
    # болтовне она это честно сделает.
    import johnny.app as app

    called = {}
    monkeypatch.setattr(app, "interpret", lambda *a, **k: called.setdefault("yes", True))
    sp = SpySpeaker()
    outcome = handle_command("что-то непонятное", _config(), sp, use_brain=False)
    assert called == {}
    assert outcome.handled is False
    assert outcome.via == "мимо"


def test_empty_text_is_not_handled():
    sp = SpySpeaker()
    outcome = handle_command("   ", _config(), sp)
    assert sp.said == ["Не расслышал"]
    assert outcome.handled is False
    assert outcome.via == "пусто"
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_app.py -q`
Expected: FAIL — `AttributeError: 'str' object has no attribute 'handled'`

- [ ] **Step 3: Переписать `handle_command`**

В `johnny/app.py` добавить импорт `from dataclasses import dataclass` и заменить `handle_command` (строки 21–46):

```python
@dataclass
class Outcome:
    """Итог обработки фразы.

    via — как сработало, для истории. handled — сделал ли Джони хоть что-то
    осмысленное. False ровно для семейства «не понял»: по нему контроллер
    решает, уходить ли в восстановление (сыграть звук и ждать команду).
    Сбой ВЫПОЛНЕНИЯ понятой команды — это handled=True: восстанавливаться там
    незачем, человеку уже сказали, что пошло не так.
    """

    via: str
    handled: bool = True


def handle_command(
    text: str,
    config,
    speaker,
    *,
    speak_failures: bool = True,
    use_brain: bool = True,
) -> Outcome:
    """Выполнить команду.

    speak_failures=False глушит только «не понял»/«не расслышал» — так ведёт
    себя слитный режим, где срабатывание могло быть ложным. use_brain=False
    отключает модель-корректор: её не спрашивают, когда Whisper не подтвердил
    имя в записи.
    """

    def not_understood(message: str, via: str) -> Outcome:
        if speak_failures:
            speaker.say(message)
        return Outcome(via, handled=False)

    if not text.strip():
        return not_understood("Не расслышал", "пусто")
    try:
        routed = route(text, config.commands)
        if routed is not None:
            _respond(speaker, execute(routed, config.apps, config.channels))
            return Outcome(routed.via)
        if not use_brain:
            return not_understood("Не понял команду", "мимо")
        answer = interpret(text, config.commands, make_providers(config))
        if answer is None:
            return not_understood("Не понял команду", "модель недоступна")
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

- [ ] **Step 4: Убедиться, что тесты проходят**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_app.py -q`
Expected: PASS, 13 passed

- [ ] **Step 5: Прогнать весь набор**

Run: `.\.venv\Scripts\python.exe -m pytest tests/ -q`
Expected: FAIL — `tests/test_controller.py` падает, потому что `controller.run_one_cycle` пишет в историю `via`, который стал объектом `Outcome`. Это чинится следующей задачей, здесь так и должно быть. Коммит всё равно делаем: задача самодостаточна, а контроллер переписывается целиком в Task 6.

- [ ] **Step 6: Коммит**

```bash
git add johnny/app.py tests/test_app.py
git commit -m "feat: handle_command возвращает Outcome и умеет молчать при провале"
```

---

### Task 6: Контроллер — две ветки и восстановление

**Files:**
- Modify: `johnny/controller.py`
- Test: `tests/test_controller.py`

**Interfaces:**
- Consumes: `audio.record_until_silence`, `audio.to_float32` (Task 2); `listener.strip_wake_word`, `Listener.wake_words`, `Listener.wait_for_wake_word(mic)` (Task 3); `Recognizer.transcribe(audio)` (Task 4); `app.handle_command(...) -> Outcome` (Task 5).
- Produces: `AssistantController.run_one_cycle(listener, recognizer, mic)`, `AssistantController.run(listener, recognizer, mic)`.

- [ ] **Step 1: Переписать фейки и добавить тесты**

Заменить верх `tests/test_controller.py` (строки 1–36) на:

```python
from johnny.audio import BLOCK_FRAMES, BlockBuffer
from johnny.config import CommandRule, Config, Settings
from johnny.controller import AssistantController

_LOUD = 8000


class FakeMic(BlockBuffer):
    """Микрофон по сценарию: список блоков (0 — тишина, _LOUD — речь), а
    после его конца — вечная тишина.

    Бесконечный хвост принципиален: контроллер вызывает flush() после
    звука-подтверждения, и микрофон на конечной очереди после этого «умирал»
    бы с MicrophoneError. В жизни звук после флаша продолжает идти, фейк
    обязан вести себя так же. По той же причине flush() не трогает сценарий —
    человек говорит команду уже ПОСЛЕ того, как буфер сброшен.
    """

    def __init__(self, script=()):
        super().__init__()
        self._script = list(script)

    def read_block(self, timeout=None) -> bytes:
        value = self._script.pop(0) if self._script else 0
        raw = value.to_bytes(2, "little", signed=True) * BLOCK_FRAMES
        self._ring.append(raw)
        return raw


def silent_mic():
    """Позвал и замолчал, потом сказал команду → ветка с паузой.

    Три тихих блока — ровно окно _CONTINUE_WINDOW (0.75с), после них
    контроллер решает, что была пауза.
    """
    return FakeMic([0] * 3 + [_LOUD] * 3 + [0] * 8)


def talking_mic():
    """Речь пошла сразу после имени → слитная ветка."""
    return FakeMic([_LOUD] * 3 + [0] * 8)


class FakeListener:
    wake_words = ["джони"]

    def wait_for_wake_word(self, mic):
        return None


class FakeRecognizer:
    def __init__(self, *texts):
        self.texts = list(texts)
        self.calls = 0

    def transcribe(self, audio):
        self.calls += 1
        return self.texts.pop(0) if self.texts else ""


class SpySpeaker:
    mode = "off"

    def __init__(self):
        self.acks = 0
        self.said = []

    def acknowledge(self):
        self.acks += 1

    def say(self, text):
        self.said.append(text)


def _config():
    return Config(
        apps={"дота": "steam://rungameid/570"},
        commands=[CommandRule("запусти *", "launch_app", "{0}")],
        settings=Settings("джони", "models/vosk", "off", "medium", "cuda"),
    )
```

Дальше в существующих тестах заменить все вызовы `ctrl.run_one_cycle(FakeListener(), FakeRecognizer("запусти дота"))` на `ctrl.run_one_cycle(FakeListener(), FakeRecognizer("запусти дота"), silent_mic())`, а `ctrl.run(FakeListener(), FakeRecognizer("запусти дота"))` — на `ctrl.run(FakeListener(), FakeRecognizer("запусти дота"), silent_mic())`. Подменённые в тестах `run_one_cycle`/`cycle` принимают третий аргумент: `def boom(listener, recognizer, mic):`.

В тестах, которые ходят через ветку с паузой (`silent_mic`), `handle_command` монкипатчится и должен теперь возвращать `Outcome`. Заменить, например, в `test_active_cycle_handles_command`:

```python
    monkeypatch.setattr(
        c, "handle_command",
        lambda text, cfg, sp, **kw: (handled.setdefault("text", text), c.Outcome("точно"))[1],
    )
```

и аналогично в остальных (в `test_paused_cycle_skips_handling` возвращаемое значение не важно, но сигнатура с `**kw` нужна).

Дописать новые тесты в конец файла:

```python
def test_joined_phrase_does_not_acknowledge(monkeypatch):
    # Главное поведение: сказал слитно — звука-подтверждения быть не должно.
    import johnny.controller as c

    monkeypatch.setattr(c.history, "add", lambda text, via=None: None)
    monkeypatch.setattr(c, "handle_command", lambda text, cfg, sp, **kw: c.Outcome("точно"))
    sp = SpySpeaker()
    ctrl = AssistantController(_config(), sp)
    ctrl.run_one_cycle(FakeListener(), FakeRecognizer("джони запусти дота"), talking_mic())
    assert sp.acks == 0


def test_joined_phrase_strips_name_before_routing(monkeypatch):
    import johnny.controller as c

    seen = {}
    monkeypatch.setattr(c.history, "add", lambda text, via=None: None)
    monkeypatch.setattr(
        c, "handle_command",
        lambda text, cfg, sp, **kw: (seen.setdefault("text", text), c.Outcome("точно"))[1],
    )
    ctrl = AssistantController(_config(), SpySpeaker())
    ctrl.run_one_cycle(FakeListener(), FakeRecognizer("джони запусти дота"), talking_mic())
    assert seen["text"] == "запусти дота"


def test_pause_branch_acknowledges(monkeypatch):
    # Позвал и замолчал — звук обязан прозвучать: это способ убедиться, что
    # Джони слышит.
    import johnny.controller as c

    monkeypatch.setattr(c.history, "add", lambda text, via=None: None)
    monkeypatch.setattr(c, "handle_command", lambda text, cfg, sp, **kw: c.Outcome("точно"))
    sp = SpySpeaker()
    ctrl = AssistantController(_config(), sp)
    ctrl.run_one_cycle(FakeListener(), FakeRecognizer("запусти дота"), silent_mic())
    assert sp.acks == 1


def test_joined_without_name_is_silent_and_logged(monkeypatch):
    # Имени в расшифровке нет → похоже на ложное срабатывание Vosk. Джони
    # молчит, но строка в истории остаётся — чтобы потом видеть, как часто
    # он ловит своё имя зря.
    import johnny.controller as c

    rows = []
    monkeypatch.setattr(c.history, "add", lambda text, via=None: rows.append((text, via)))
    monkeypatch.setattr(c, "handle_command", lambda text, cfg, sp, **kw: c.Outcome("мимо", handled=False))
    sp = SpySpeaker()
    ctrl = AssistantController(_config(), sp)
    ctrl.run_one_cycle(FakeListener(), FakeRecognizer("да я говорю ему"), talking_mic())
    assert sp.acks == 0
    assert sp.said == []
    assert rows == [("да я говорю ему", "слитно(без имени)/мимо")]


def test_joined_without_name_does_not_ask_model(monkeypatch):
    import johnny.controller as c

    flags = {}
    monkeypatch.setattr(c.history, "add", lambda text, via=None: None)
    monkeypatch.setattr(
        c, "handle_command",
        lambda text, cfg, sp, **kw: (flags.update(kw), c.Outcome("мимо", handled=False))[1],
    )
    ctrl = AssistantController(_config(), SpySpeaker())
    ctrl.run_one_cycle(FakeListener(), FakeRecognizer("да я говорю ему"), talking_mic())
    assert flags["use_brain"] is False
    assert flags["speak_failures"] is False


def test_joined_with_name_but_no_command_recovers(monkeypatch):
    # Имя подтверждено двумя движками — человек точно звал. Команда не
    # сошлась → падаем в обычную ветку: звук и ждём команду. Молчание здесь
    # ощущалось бы как «не услышал», хотя он услышал.
    import johnny.controller as c

    rows = []
    monkeypatch.setattr(c.history, "add", lambda text, via=None: rows.append((text, via)))
    monkeypatch.setattr(
        c, "handle_command",
        lambda text, cfg, sp, **kw: c.Outcome("точно") if text == "запусти дота"
        else c.Outcome("мимо", handled=False),
    )
    sp = SpySpeaker()
    ctrl = AssistantController(_config(), sp)
    # Слитная фраза, потом (после звука) человек говорит команду заново.
    mic = FakeMic([_LOUD] * 3 + [0] * 8 + [_LOUD] * 3 + [0] * 8)
    rec = FakeRecognizer("джони бубубу", "запусти дота")
    ctrl.run_one_cycle(FakeListener(), rec, mic)
    assert sp.acks == 1          # звук прозвучал
    assert rec.calls == 2        # записали и расшифровали заново
    assert rows == [("запусти дота", "точно")]  # одна строка, по итогу


def test_joined_success_is_marked_in_history(monkeypatch):
    import johnny.controller as c

    rows = []
    monkeypatch.setattr(c.history, "add", lambda text, via=None: rows.append((text, via)))
    monkeypatch.setattr(c, "handle_command", lambda text, cfg, sp, **kw: c.Outcome("точно"))
    ctrl = AssistantController(_config(), SpySpeaker())
    ctrl.run_one_cycle(FakeListener(), FakeRecognizer("джони запусти дота"), talking_mic())
    assert rows == [("запусти дота", "слитно/точно")]


def test_cycle_flushes_microphone_at_the_end(monkeypatch):
    # Без флаша Джони, произнеся вслух ответ со словом «Джони», разбудил бы
    # сам себя: поток теперь открыт всегда.
    import johnny.controller as c

    monkeypatch.setattr(c.history, "add", lambda text, via=None: None)
    monkeypatch.setattr(c, "handle_command", lambda text, cfg, sp, **kw: c.Outcome("точно"))
    ctrl = AssistantController(_config(), SpySpeaker())
    mic = talking_mic()
    ctrl.run_one_cycle(FakeListener(), FakeRecognizer("джони запусти дота"), mic)
    assert mic.preroll() == b""
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_controller.py -q`
Expected: FAIL — `TypeError: run_one_cycle() takes 3 positional arguments but 4 were given`

- [ ] **Step 3: Переписать цикл в `johnny/controller.py`**

Заменить импорты и класс (сохранив константы `_RETRY_PAUSE_SECONDS`, `_MAX_CONSECUTIVE_FAILURES` и их комментарии как есть):

```python
import logging
import threading
import time

from . import audio, history
from .app import Outcome, handle_command
from .listener import strip_wake_word

logger = logging.getLogger(__name__)

# Сколько тишины после имени считаем паузой. Пользователь выбрал 0.7с;
# 0.75 — то же самое, выровненное по сетке блоков 0.25с (ровно 3 блока).
# Округляем ВВЕРХ намеренно: 0.7 выбрано, чтобы Джони не перебивал звуком
# естественный зазор внутри фразы, и округление вниз сломало бы смысл.
_CONTINUE_WINDOW = 0.75
```

Методы `run_one_cycle` и `run` заменить на:

```python
    def run_one_cycle(self, listener, recognizer, mic) -> None:
        listener.wait_for_wake_word(mic)
        if self._paused.is_set() or self._stop.is_set():
            mic.flush()
            return
        preroll = mic.preroll()
        tail, started = audio.record_until_silence(mic, start_timeout=_CONTINUE_WINDOW)
        try:
            if started:
                self._joined_turn(listener, recognizer, mic, preroll + tail)
            else:
                self._summoned_turn(recognizer, mic)
        finally:
            # Свой же звук (ack, ответ TTS) не должен вернуться на вход:
            # поток открыт постоянно, и Джони способен разбудить сам себя.
            mic.flush()

    def _joined_turn(self, listener, recognizer, mic, raw) -> None:
        """Сказано слитно с именем: подтверждения не было и не будет."""
        text, found = strip_wake_word(
            recognizer.transcribe(audio.to_float32(raw)), listener.wake_words
        )
        self.last_command = text
        logger.info("Распознано слитно: %r (имя %s)", text, "есть" if found else "НЕТ")
        mark = "слитно" if found else "слитно(без имени)"
        try:
            outcome = handle_command(
                text, self.config, self.speaker, speak_failures=False, use_brain=found
            )
        except Exception:
            history.add(text, f"{mark}/ошибка")
            raise
        if outcome.handled:
            history.add(text, f"{mark}/{outcome.via}")
            return
        if not found:
            # Похоже на ложное срабатывание Vosk — молчим, но факт сохраняем.
            history.add(text, f"{mark}/мимо")
            return
        # Имя подтверждено, а команда не разобралась: человек точно звал,
        # поэтому не молчим, а ведём себя как при обычном вызове с паузой.
        self._summoned_turn(recognizer, mic)

    def _summoned_turn(self, recognizer, mic) -> None:
        """Позвал и ждёт: звук-подтверждение, потом команда."""
        self.speaker.acknowledge()
        mic.flush()  # иначе в запись попадёт собственный «пик»
        raw, started = audio.record_until_silence(mic)
        text = recognizer.transcribe(audio.to_float32(raw)) if started else ""
        self.last_command = text
        logger.info("Распознано: %r", text)
        outcome = Outcome("ошибка")
        try:
            outcome = handle_command(text, self.config, self.speaker)
        finally:
            # Строку истории пишем всегда: даже если обработка упала, факт
            # распознавания должен остаться — на нём настраиваются пороги.
            history.add(text, outcome.via)

    def run(self, listener, recognizer, mic) -> None:
        consecutive_failures = 0
        while not self._stop.is_set():
            try:
                self.run_one_cycle(listener, recognizer, mic)
                consecutive_failures = 0
            except Exception:
                # Сбой (например, TTS, либо пропавший микрофон) внутри одного
                # цикла не должен убивать поток прослушивания — трей-иконка
                # иначе выглядит живой, а Джони уже не слушает.
                logger.exception("Ошибка в цикле прослушивания")
                consecutive_failures += 1
                if consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
                    logger.error(
                        "Слишком много сбоев подряд (%d) — прослушивание остановлено",
                        consecutive_failures,
                    )
                    self.stop()
                    break
                time.sleep(_RETRY_PAUSE_SECONDS)
```

- [ ] **Step 4: Убедиться, что тесты проходят**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_controller.py -q`
Expected: PASS, 14 passed

- [ ] **Step 5: Прогнать весь набор**

Run: `.\.venv\Scripts\python.exe -m pytest tests/ -q`
Expected: PASS, все зелёные

- [ ] **Step 6: Коммит**

```bash
git add johnny/controller.py tests/test_controller.py
git commit -m "feat: слитная фраза и восстановление в цикле прослушивания"
```

---

### Task 7: Проводка — консольный вход и трей

**Files:**
- Modify: `johnny/app.py:49-73` (функция `run`)
- Modify: `johnny/tray.py:43-68`

**Interfaces:**
- Consumes: `audio.Microphone` (Task 1), `AssistantController.run(listener, recognizer, mic)` (Task 6).
- Produces: рабочее приложение.

`run()` переводится на контроллер не ради красоты: иначе двухрежимную логику пришлось бы написать дважды, и две копии неизбежно разъедутся. Заодно консольный вход получает обёртку try/except вокруг цикла — известная мелочь из списка «на будущее» в памяти проекта.

- [ ] **Step 1: Переписать `run()` в `johnny/app.py`**

Заменить функцию `run` целиком:

```python
def run(config_dir: str = "config") -> None:
    from .audio import Microphone
    from .controller import AssistantController
    from .listener import Listener
    from .recognizer import Recognizer, build_vocabulary

    config = load_config(config_dir)
    speaker = make_speaker(config.settings, config.secrets)
    vocabulary = build_vocabulary(config.apps, config.channels, config.commands)
    recognizer = Recognizer(
        config.settings.whisper_model, config.settings.whisper_device, vocabulary
    )
    listener = Listener(config.settings.wake_word, config.settings.vosk_model_path)
    controller = AssistantController(config, speaker)

    print("Джони готов. Скажи «Джони» и команду — можно одной фразой.")
    try:
        # Цикл живёт в контроллере: две ветки вызова (слитно / с паузой)
        # переписывать здесь второй раз нельзя, копии разъедутся.
        with Microphone() as mic:
            controller.run(listener, recognizer, mic)
    except KeyboardInterrupt:
        print("Выход.")
    finally:
        listener.close()
```

- [ ] **Step 2: Переписать создание микрофона в `johnny/tray.py`**

В `main()` после создания `listener` (строка 59) добавить:

```python
    from .audio import Microphone

    mic = Microphone().open()
```

Заменить функцию `loop`:

```python
    def loop() -> None:
        try:
            controller.run(listener, recognizer, mic)
        except Exception:
            logging.exception("Цикл прослушивания упал")
        finally:
            mic.close()
```

В `quit_app` микрофон закрывать не нужно: `controller.stop()` выводит цикл из `run`, и `finally` в `loop` отработает сам.

- [ ] **Step 3: Проверить, что всё импортируется и собирается**

Run: `.\.venv\Scripts\python.exe -c "import johnny.app, johnny.tray, johnny.controller, johnny.audio, johnny.listener; print('ok')"`
Expected: печатает `ok` (при живой звуковой подсистеме; микрофон здесь ещё не открывается)

- [ ] **Step 4: Прогнать весь набор**

Run: `.\.venv\Scripts\python.exe -m pytest tests/ -q`
Expected: PASS, все зелёные

- [ ] **Step 5: Коммит**

```bash
git add johnny/app.py johnny/tray.py
git commit -m "feat: единый микрофон в консольном входе и в трее"
```

- [ ] **Step 6: Живая проверка (делает пользователь, нужен настоящий микрофон)**

Перезапустить Джони: «Выход» в трее → ярлык «Джони» на рабочем столе. Затем проверить по пунктам:

1. **Слитно:** «Джони громкость пять» одной фразой. Ожидается: звука-подтверждения НЕТ, громкость становится 50%.
2. **С паузой:** «Джони» → подождать → звук → «громкость пять». Ожидается: как раньше.
3. **Восстановление:** «Джони» + невнятица слитно. Ожидается: звук прозвучал, Джони ждёт команду.
4. **Самопробуждение:** задать вопрос, в ответе на который прозвучит слово «Джони» (например «Джони, как тебя зовут»). Ожидается: после ответа он НЕ будит сам себя.
5. Открыть «История команд» в трее и убедиться, что пометки `слитно/…` проставляются.

Если пункт 1 не срабатывает (звук всё-таки играет), первый подозреваемый — RMS-порог тишины при твоём фоновом шуме: посмотреть `johnny.log`, там для каждой слитной фразы пишется `Распознано слитно: … (имя есть/НЕТ)`.

---

## Self-Review

**Покрытие спеки:**

| Требование спеки | Задача |
|---|---|
| Общий микрофонный поток, кольцевой пре-ролл 1.5с | Task 1 |
| `MicrophoneError` по таймауту чтения | Task 1 |
| Потолок очереди блоков | Task 1 |
| `record_until_silence` с флагом «заговорил» | Task 2 |
| `to_float32` | Task 2 |
| `strip_wake_word`, срез до последнего вхождения в первых трёх словах | Task 3 |
| Listener не чистит хвост очереди, читает из общего микрофона | Task 3 |
| Имя в `_BASE_PROMPT`, пересчёт бюджета токенов | Task 4 |
| `Recognizer.transcribe` вместо `listen_command` | Task 4 |
| `Outcome`, `speak_failures`, `use_brain` | Task 5 |
| Порог паузы 0.75с, две ветки цикла | Task 6 |
| Восстановление при найденном имени | Task 6 |
| Молчание + история при ненайденном имени | Task 6 |
| `flush` после ack и в конце цикла | Task 6 |
| Пометки `слитно/…` в истории | Task 6 |
| `run()` через контроллер, трей создаёт микрофон | Task 7 |
| Живая проверка (4 пункта спеки + история) | Task 7 |

**Заглушки:** не найдено — каждый шаг с кодом содержит код целиком.

**Согласованность типов:** `read_block() -> bytes` во всех задачах; `record_until_silence` возвращает `tuple[bytes, bool]` и так используется в Task 6; `transcribe` принимает результат `to_float32` (ndarray) везде; `Outcome(via, handled)` создаётся в Task 5 и читается в Task 6 через `c.Outcome`; `wake_words` — публичный список, объявлен в Task 3 и используется в Task 6 и в фейке `FakeListener`.

**Замеченное при проверке и учтённое:** фейковый микрофон в тестах контроллера не может быть конечной очередью — контроллер вызывает `flush()` после звука-подтверждения, и следующая запись упёрлась бы в `MicrophoneError` вместо ветки восстановления; поэтому `FakeMic` отдаёт вечную тишину после конца сценария. Изменение `_BASE_PROMPT` ломает `test_header_is_derived_from_own_prompt_argument` (он строит альтернативную шапку той же длины) — починка встроена в Task 4, Step 3. Task 5 намеренно оставляет набор красным до Task 6: контроллер переписывается целиком, чинить его дважды смысла нет.
