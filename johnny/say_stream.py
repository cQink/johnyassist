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


class _FillerText(str):
    """Строка-филлер: та же фраза, но помеченная для play_loop.

    Ведёт себя как обычная str (конкатенация, .strip(), " ".join — всё
    работает без изменений), но isinstance-проверка внизу отличает её от
    фраз ответа. Нужна ровно для одного решения: сбой ПРОИГРЫВАНИЯ филлера
    не должен ни попасть текстом в остаток, ни защёлкнуть общую деградацию
    — филлер лишь слово-заглушка перед ответом, а не часть самого ответа.
    """


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
    # Фразы, до которых синтез не добрался из-за отказа Fish (или которые
    # засинтезировались, но не смогли проиграться), договорит запасной голос
    # ОДНИМ куском, а не пофразно — иначе голос менялся бы туда-обратно на
    # границах фраз, и это звучит как поломка.
    leftover: list[str] = []
    # Общий на оба потока: как только СИНТЕЗ или ПРОИГРЫВАНИЕ хоть раз
    # отказали, дальше нет смысла ни платить Fish за новые фразы, ни пытаться
    # играть уже готовые вперемешку с ещё не готовыми — human слышал бы
    # «Два. Три. ... Раз.» вместо «Раз. Два. Три.». threading.Event потому что
    # его читают и пишут оба потока, а флаг — не составное значение, гонки
    # на нём безопасны.
    degraded = threading.Event()

    def synth_loop():
        while True:
            text = phrases.get()
            if text is None:
                audio.put(None)
                return
            if _stopped(cancel):
                continue
            if degraded.is_set():
                # Играть всё равно не будут — Fish платить не за что.
                # Текст всё же передаём дальше по очереди (без файла), чтобы
                # ЕДИНСТВЕННЫЙ поток, play_loop, решал судьбу leftover: так
                # порядок фраз в остатке гарантированно не перемешается.
                audio.put((text, None))
                continue
            try:
                prepared = voice.prepare(text)
            except Exception:
                # Контракт Voice обещает Prepared(path=None) при неудаче
                # синтеза, но сетевой сбой (обрыв, таймаут Fish) — обычный
                # СПОСОБ этой неудачи, а не аномалия сверх контракта.
                # Приравниваем исключение к уже существующему пути отказа:
                # без этого поток синтеза здесь же и умирает, конец очереди
                # в `audio` никогда не уходит, и player.join() (а с ним и
                # весь consume()) висит навсегда.
                prepared = None
            if prepared is None or prepared.path is None:
                degraded.set()
                audio.put((text, None))
                continue
            # Текст едет вместе с файлом: если файл не проиграется, договорить
            # эту фразу запасным голосом можно, только зная её текст.
            audio.put((text, prepared))

    def play_loop():
        # Своя, ЛОКАЛЬНАЯ для этого потока деградация — специально не через
        # общий `degraded`. Тот факт, что синтез где-то дальше по потоку уже
        # отказал (и выставил `degraded` для экономии на Fish), не значит, что
        # фраза, которая доехала досюда УЖЕ готовой, вдруг не должна играть:
        # `degraded` из play_loop читать нельзя — очередь `audio` асинхронна,
        # и к моменту, когда мы дошли до готового файла, синтез мог уйти
        # вперёд и успеть выставить флаг из-за СЛЕДУЮЩЕЙ фразы. Порядок и
        # решение о каждой фразе — только по локальному состоянию, строго по
        # очереди её обработки этим потоком.
        play_broken = False

        def cleanup(prepared):
            if prepared is not None and getattr(prepared, "temporary", False):
                try:
                    Path(prepared.path).unlink(missing_ok=True)
                except OSError:
                    pass

        while True:
            item = audio.get()
            if item is None:
                return
            text, prepared = item
            is_filler = isinstance(text, _FillerText)
            if _stopped(cancel):
                # Файл уже засинтезирован (Fish за него заплачен), но
                # проигрывать его не будем — «стоп» отменяет и это тоже. Не
                # убрать файл здесь значит оставить mp3 в TEMP навсегда.
                cleanup(prepared)
                continue
            if prepared is None or play_broken:
                # Либо синтез этой фразы не удался, либо раньше по потоку
                # (в этом же потоке, значит без гонки) отказало само
                # проигрывание: остаток договаривает запасной голос одним
                # куском, а не вразнобой.
                leftover.append(text)
                cleanup(prepared)
                continue
            try:
                voice.play(prepared.path)
            except Exception:
                if is_filler:
                    # Филлер едет по той же очереди, что и фразы ответа, но
                    # он не часть ответа, а слово-заглушка перед ним. Сбой
                    # ЕГО проигрывания (а филлеры почти всегда играют из
                    # кеша — битый файл кеша это как раз он) не должен ни
                    # добавить текст филлера в остаток (иначе запасной
                    # голос произнесёт «Секунду» вместе с ответом), ни
                    # защёлкнуть деградацию (иначе весь ответ вслед за
                    # филлером уедет на запасной голос без своей вины).
                    pass
                else:
                    # Проигрывание отвалилось — эту фразу и остаток договорит
                    # запасной голос, как и при отказе синтеза. `degraded.set()`
                    # не влияет на решения ЭТОГО потока (см. play_broken выше),
                    # только сообщает synth_loop, что дальше готовить фразы,
                    # которые всё равно уйдут в leftover, незачем.
                    leftover.append(text)
                    play_broken = True
                    degraded.set()
                if getattr(prepared, "from_cache", False):
                    # Как и в tts_cache._play_prepared: файл из кеша сам
                    # оказался битым. Если его не убрать, филлер (кеш —
                    # почти всегда он) молчит этим же файлом на КАЖДОМ
                    # ответе до ручной чистки кеша.
                    try:
                        Path(prepared.path).unlink(missing_ok=True)
                    except OSError:
                        pass
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
                            phrases.put(_FillerText(filler))
                if command_branch:
                    # Командная ветка: копим молча до конца, озвучивать нечего.
                    continue
                while True:
                    phrase, buffer = cut(buffer, first_limit, sentences)
                    if not phrase:
                        break
                    if looks_like_command(phrase):
                        # Решение «разговор» принято по НАЧАЛУ буфера — оно
                        # могло опередить границу предложения (см. DECIDE_AFTER
                        # выше) и не увидеть JSON, который приходит позже.
                        # Проверяем КАЖДУЮ фразу отдельно: встретив JSON,
                        # защёлкиваем командную ветку и дальше молчим до конца
                        # потока, как будто решение с самого начала было верным.
                        command_branch = True
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

        if not command_branch and buffer.strip() and looks_like_command(buffer.strip()):
            # Тот же сторож, что и внутри while-цикла: остаток без единой
            # точки внутри (JSON без хвостового текста после него) никогда не
            # проходит через cut() как отдельная фраза и добрался бы сюда
            # непроверенным.
            command_branch = True

        if not command_branch and buffer.strip() and not _stopped(cancel):
            phrases.put(buffer.strip())
            spoken = True
    finally:
        # Сигнал завершения обязан уйти всегда, а не только когда всё выше
        # прошло без исключений: иначе оба потока (synth и play) висят
        # навсегда на queue.get(), а синтез продолжает тратить деньги на Fish
        # уже после того, как consume() вышла с исключением.
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
