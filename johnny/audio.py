import math
import queue
from collections import deque

import numpy as np

from . import activity

SAMPLE_RATE = 16000
BLOCK_FRAMES = 4000  # 0.25 c

# Сколько звука держим «назад во времени». Хватает, чтобы в снимок попало
# само слово «Джони» вместе с задержкой Vosk на его распознавание.
#
# ЗАМЕР 2026-08-03 (`scratchpad`, синтез edge-tts «Джони, открой ютуб» через
# ОБЕ модели Vosk блок за блоком): маленькая модель уверенно включает имя в
# partial-результат уже через ~1.0с звука, а ПОЛНАЯ (vosk-model-ru-0.42,
# поставлена в тот же день ради другой проблемы) — только через ~2.5с, то
# есть почти вся короткая фраза уже отзвучала. При старом значении 1.5с
# (6 блоков) кольцевой буфер к этому моменту УЖЕ ВЫТЕСНЯЛ блоки с самим
# словом «Джони» — Whisper потом просто не слышал имя в записи, потому что
# его там больше не было физически. Это была настоящая причина серии багов
# «то не выполняет слитную команду, то переспрашивает» в тот же день — не
# тайминги решений (started/found), которые чинились раньше, а нехватка
# буфера при более медленной (хоть и точной) полной модели. 4.0с — запас
# почти вдвое сверх замеренных 2.5с, плюс место на паузу перед именем.
_PREROLL_SECONDS = 4.0

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


# Порог тишины по RMS блока. Настроен на блоке 0.25с и живом микрофоне
# пользователя — не менять вместе с размером блока.
#
# Замер 2026-07-27 (Yeti Classic, три фразы «Джони, сделай громче» с обычного
# расстояния): речь 0.0012–0.0145, фон между фразами не выше 0.00065. Прежнее
# значение 0.01 лежало ВНУТРИ речи — две фразы из трёх целиком считались
# тишиной, поэтому слитная фраза через раз уезжала в ветку «позвал и ждёт», и
# звук-подтверждения звучал человеку в середину слова. Досталось оно от
# старого рекордера, где решало только, когда обрывать запись; в этой роли
# завышенный порог всего лишь обрезал начало команды («сделай громче» →
# «и громче»), поэтому и не всплывало.
#
# 0.001 — между двумя мирами: втрое выше фона и вдвое ниже самого тихого
# блока речи. Выше 0.002 брать нельзя (проверено на записи: тихая фраза снова
# теряется), заметно ниже — начнёт цепляться фон.
_SILENCE_RMS = 0.001


def to_float32(raw: bytes) -> np.ndarray:
    """int16-байты с микрофона → float32 [-1, 1], как ждёт Whisper."""
    return np.frombuffer(raw, dtype="int16").astype("float32") / 32768.0


def rms(raw: bytes) -> float:
    """Громкость блока. Пустой блок — ноль.

    Отдельно от is_silent, потому что то же самое число нужно кружку в панели
    (johnny/activity.py): считать RMS второй раз на каждый блок незачем.
    """
    samples = to_float32(raw)
    if samples.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(samples**2)))


def is_silent(raw: bytes) -> bool:
    """Блок тише порога? Пустой блок считаем тишиной (rms вернёт 0.0)."""
    return rms(raw) < _SILENCE_RMS


def record_until_silence(
    mic,
    max_seconds: float = 25.0,
    silence_seconds: float = 1.2,
    start_timeout: float = 6.0,
) -> tuple[bytes, bool]:
    """Записать одну реплику: от начала речи до тишины.

    Возвращает (звук, заговорил_ли). Второй элемент — то, на чём держится
    весь слитный режим: речь пошла сразу после имени → человек говорит одной
    фразой; тишина дольше start_timeout → он позвал и ждёт подтверждения.

    Арифметика блоков целочисленная и намеренно не округляется вверх: она
    настроена на живом голосе, ceil удлинил бы хвост после команды.

    ЖИВОЙ БАГ (2026-08-05): «Джони, ввод <длинный текст>» обрывался
    посреди диктовки — старый потолок max_seconds=8.0 резал запись через 8с
    НЕЗАВИСИМО от того, договорил человек или нет (обычные короткие команды
    заканчиваются сами через silence_seconds задолго до потолка — он тут
    только страховка от зависшей записи, а не осмысленный лимит длины
    команды). 25с даёт запас под полноценную диктовку, оставаясь конечным.
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
        loudness = rms(block)
        silent = loudness < _SILENCE_RMS
        # Кружок в панели пульсирует под этот уровень. Здесь, а не в отдельном
        # потоке: блоки всё равно проходят через это место каждые 0.25с, а
        # второй читатель микрофона невозможен — поток один.
        activity.push_level(loudness)

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
