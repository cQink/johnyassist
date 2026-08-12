"""Конвейер озвучки: текст Groq по кускам → фразы → mp3 → колонки.

Смысл модуля — совместить три ожидания, которые сегодня идут подряд: пока
модель договаривает ответ, первая фраза уже синтезируется, а пока она играет,
синтезируется вторая. Общее время ответа от этого почти не меняется — меняется
момент, когда Джони НАЧИНАЕТ говорить, а именно он и ощущается как «долго
молчит».

Потокового аудио здесь нет: MCI играет только готовые файлы (см. sounds.py), а
декодер mp3 в память — новая зависимость. Каждая фраза остаётся отдельным mp3.
Подробности решения — docs/superpowers/specs/2026-08-12-streaming-voice-design.md
"""

import queue
import random
import re
import threading
from dataclasses import dataclass
from pathlib import Path

from .brain_groq import StreamBroken  # noqa: F401 — переэкспорт

# Конец предложения вместе с закрывающими кавычками и скобками: «Да!» — фраза
# кончается после кавычки, а не после восклицательного знака.
_BOUNDARY = re.compile(r"[.!?…]+[\"»)\]]*")

# Сколько знаков ждём, прежде чем решить «разговор или команда». По первому
# символу решать нельзя: модель изредка предваряет JSON словами, и такой ответ
# выглядел бы разговорным.
DECIDE_AFTER = 40


def looks_like_command(head: str) -> bool:
    """Похоже ли начало потока на JSON-команду, а не на разговор.

    Решение принимается ОДИН РАЗ и не пересматривается: озвученного не вернуть,
    а метаться между ветками посреди ответа хуже, чем ошибиться один раз.
    """
    return "{" in head or "```" in head


def cut(buffer: str, limit: int, sentences: int = 1) -> tuple[str, str]:
    """Отрезать готовую к озвучке фразу. Возвращает (фраза, остаток).

    Пустая фраза = ещё рано, копим дальше. limit — потолок ожидания в знаках:
    без него текст без единой точки никогда не дошёл бы до синтеза.
    """
    ends = [match.end() for match in _BOUNDARY.finditer(buffer)]
    if ends:
        end = ends[min(max(1, sentences), len(ends)) - 1]
        return buffer[:end].strip(), buffer[end:].lstrip()
    if len(buffer) >= limit:
        # По границе слова: обрывок посреди слова слышен как заикание.
        space = buffer.rfind(" ", 0, limit)
        end = space if space > 0 else limit
        return buffer[:end].strip(), buffer[end:].lstrip()
    return "", buffer


class Fillers:
    """Короткие реплики, которые играют, пока готовится первая фраза.

    Именно они убирают паузу до первого слова: быстрее, чем за время синтеза,
    первую фразу не получить, а филлер после первого раза лежит в кеше и
    играет с диска мгновенно (все фразы короче tts_cache.MAX_CACHED_CHARS —
    это требование к списку, а не совпадение).
    """

    def __init__(self, phrases):
        self._phrases = [str(phrase).strip() for phrase in (phrases or []) if str(phrase).strip()]
        self._last = ""

    def pick(self) -> str:
        """Случайная фраза, но не та же, что в прошлый раз. "" = филлеров нет."""
        if not self._phrases:
            return ""
        choices = [phrase for phrase in self._phrases if phrase != self._last]
        # Список из одной фразы: повтор разрешён, иначе выбирать не из чего.
        chosen = random.choice(choices or self._phrases)
        self._last = chosen
        return chosen


# Сколько фраз может ждать своей очереди. Предел нужен не ради памяти: без него
# нарезчик убежит вперёд и насинтезирует (за деньги, у Fish) фразы, которые
# «стоп» отменит через секунду.
_QUEUE_LIMIT = 3

_BROKEN_PHRASE = "Связь оборвалась, договорить не могу"


@dataclass
class StreamResult:
    """Чем кончился конвейер.

    text — весь накопленный текст (он же уходит в разбор JSON, если ветка
    оказалась командной). spoken — звучал ли голос: зовущему нельзя произносить
    ответ ВТОРОЙ раз. broken — поток оборвался, а не закончился.
    """

    text: str
    spoken: bool
    broken: bool = False


@dataclass
class Voice:
    """Три функции, которыми конвейер пользуется, и больше он про голос ничего
    не знает: prepare(text) -> Prepared, play(path), fallback(text).

    Именно эта граница делает конвейер тестируемым без сети, без Fish и без
    звуковой карты.
    """

    prepare: object
    play: object
    fallback: object


def _stopped(cancel) -> bool:
    return cancel is not None and cancel.is_set()


def consume(chunks, voice, *, fillers, first_limit: int = 120, cancel=None) -> StreamResult:
    """Провести поток кусков текста через нарезку, синтез и проигрывание.

    Вызывающий поток работает нарезчиком, синтез и проигрывание идут своими
    потоками — иначе следующая фраза не готовится во время проигрывания
    предыдущей, а ради этого всё и затевалось.
    """
    phrases: queue.Queue = queue.Queue(maxsize=_QUEUE_LIMIT)
    audio: queue.Queue = queue.Queue(maxsize=_QUEUE_LIMIT)
    # Фразы, до которых синтез не добрался из-за отказа Fish: их договорит
    # запасной голос ОДНИМ куском, а не пофразно — иначе голос менялся бы
    # туда-обратно на границах фраз, и это звучит как поломка.
    leftover: list[str] = []
    played_anything = threading.Event()

    def synth_loop():
        degraded = False
        while True:
            text = phrases.get()
            if text is None:
                audio.put(None)
                return
            if _stopped(cancel):
                continue
            if degraded:
                leftover.append(text)
                continue
            prepared = voice.prepare(text)
            if prepared.path is None:
                degraded = True
                leftover.append(text)
                continue
            # Текст едет вместе с файлом: если файл не проиграется, договорить
            # эту фразу запасным голосом можно, только зная её текст.
            audio.put((text, prepared))

    def play_loop():
        def cleanup(prepared):
            if getattr(prepared, "temporary", False):
                try:
                    Path(prepared.path).unlink(missing_ok=True)
                except OSError:
                    pass

        while True:
            item = audio.get()
            if item is None:
                return
            text, prepared = item
            if _stopped(cancel):
                # Файл уже засинтезирован (Fish за него заплачен), но
                # проигрывать его не будем — «стоп» отменяет и это тоже. Не
                # убрать файл здесь значит оставить mp3 в TEMP навсегда.
                cleanup(prepared)
                continue
            try:
                voice.play(prepared.path)
                played_anything.set()
            except Exception:
                # Проигрывание отвалилось — эту фразу и остаток договорит
                # запасной голос, как и при отказе синтеза.
                leftover.append(text)
            finally:
                cleanup(prepared)

    synth = threading.Thread(target=synth_loop, name="say-stream-synth", daemon=True)
    player = threading.Thread(target=play_loop, name="say-stream-play", daemon=True)
    synth.start()
    player.start()

    collected: list[str] = []
    buffer = ""
    decided = False
    command_branch = False
    spoken = False
    broken = False
    sentences = 1

    try:
        for piece in chunks:
            if _stopped(cancel):
                break
            collected.append(piece)
            buffer += piece
            if not decided:
                # Решаем один раз: набралось DECIDE_AFTER знаков или пришла
                # первая законченная фраза — что раньше.
                phrase, _ = cut(buffer, first_limit)
                if len(buffer) < DECIDE_AFTER and not phrase:
                    continue
                decided = True
                command_branch = looks_like_command(buffer[:DECIDE_AFTER])
                if not command_branch:
                    filler = fillers.pick()
                    if filler:
                        phrases.put(filler)
            if command_branch:
                # Командная ветка: копим молча до конца, озвучивать нечего.
                continue
            while True:
                phrase, buffer = cut(buffer, first_limit, sentences)
                if not phrase:
                    break
                phrases.put(phrase)
                spoken = True
                # Дальше первой фразы спешить некуда — Джони уже говорит, а
                # каждый лишний вызов Fish это и деньги, и слышимая пауза.
                sentences = 2
    except StreamBroken:
        broken = True

    if not decided and buffer.strip():
        # Поток кончился, а решения так и не было: ответ оказался короче
        # DECIDE_AFTER и без единой точки. Ровно так выглядит короткий JSON
        # ({"command": "громкость 5"} — 26 знаков), поэтому решить ОБЯЗАНЫ и
        # здесь. Без этой ветки хвост уходил бы в озвучку, и Джони зачитывал
        # бы вслух JSON — то, против чего стоит сторож №1 в спеке.
        decided = True
        command_branch = looks_like_command(buffer[:DECIDE_AFTER])

    if not command_branch and buffer.strip() and not _stopped(cancel):
        phrases.put(buffer.strip())
        spoken = True

    phrases.put(None)
    synth.join()
    player.join()

    text = "".join(collected)
    if _stopped(cancel):
        return StreamResult(text=text, spoken=False, broken=broken)

    tail = " ".join(part for part in leftover if part).strip()
    if tail:
        voice.fallback(tail)
    if broken and spoken:
        voice.fallback(_BROKEN_PHRASE)
    return StreamResult(text=text, spoken=spoken, broken=broken)
