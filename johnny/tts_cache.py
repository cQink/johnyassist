"""Общий слой для TTS-провайдеров, отдающих mp3 по сети (fish.audio, локальный
NeMo/XTTS-сервис): проверка тела на mp3, кеш коротких служебных фраз, атомарная
запись, пауза после отказа и переход на запасной голос.

Провайдеру остаётся только synthesize(text) -> bytes — вся возня с диском,
битым кешем и временными файлами живёт здесь, в одном месте на всех. Копия
этой логики во втором провайдере разъехалась бы с первой на первом же баге:
здесь собраны выводы из живых сбоев (см. комментарии ниже), а не общий код
ради общего кода.
"""

import os
import random
import tempfile
from dataclasses import dataclass
from pathlib import Path

from . import sounds
from .http_client import in_cooldown, mark_failure, warn_once

# Кешируем только служебные реплики Джони («Не понял команду», «Не расслышал»):
# они звучат сотнями раз и в 40 символов укладываются все. Ответы модели длиннее
# и дословно не повторяются — кешировать их значит копить мусор.
MAX_CACHED_CHARS = 40
MAX_CACHE_FILES = 100

# Настоящий mp3 весит явно больше — это просто щит от заглушки-страницы.
MIN_MP3_BYTES = 64


def looks_like_mp3(data: bytes) -> bool:
    """Провайдер иногда отдаёт 200 с телом, которое НЕ mp3 (страница Cloudflare,
    JSON про исчерпанный тариф, traceback FastAPI). Статус-коду в такой момент
    верить нельзя — мусор, попавший в кеш под видом mp3, будет проигрываться
    вечно."""
    if len(data) < MIN_MP3_BYTES:
        return False
    if data[:3] == b"ID3":
        return True
    return data[0] == 0xFF and (data[1] & 0xE0) == 0xE0  # синхрослово mp3-кадра


def cache_path(cache_dir, text: str, voice_key: str) -> Path:
    """Путь в кеше. voice_key — всё, от чего зависит ЗВУЧАНИЕ (id модели, стиль).

    Он в хеше намеренно: переобучили голос и поменяли id в настройках — старые
    файлы кеша обязаны стать другим именем, иначе служебные фразы годами
    звучат прежним голосом молча, до ручной чистки models/tts-cache/.
    """
    import hashlib

    digest = hashlib.sha1(f"{voice_key}:{text}".encode("utf-8")).hexdigest()
    return Path(cache_dir) / f"{digest}.mp3"


def store(path: Path, data: bytes) -> None:
    """Запись через временный файл: оборванный ответ сети не должен остаться
    в кеше под правильным именем и звучать обрезком до конца времён."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".part")
    temp.write_bytes(data)
    os.replace(temp, path)


def trim(cache_dir, limit: int = MAX_CACHE_FILES) -> None:
    """Оставить не больше limit файлов, удаляя самые старые.

    Заодно подчищает осиротевшие .part: если процесс убили между записью
    временного файла и os.replace в store, такой огрызок не удалится сам
    никогда — только здесь, при следующей уборке кеша.
    """
    for orphan in Path(cache_dir).glob("*.part"):
        orphan.unlink(missing_ok=True)
    files = sorted(Path(cache_dir).glob("*.mp3"), key=lambda p: p.stat().st_mtime)
    for old in files[: max(0, len(files) - limit)]:
        old.unlink(missing_ok=True)


@dataclass
class Prepared:
    """Результат синтеза: файл, готовый к проигрыванию.

    path=None — синтез не удался, и решать, что делать (запасной голос),
    будет зовущий: у конвейера и у обычного say разные правильные ответы.
    temporary=True — файл временный, удалить после проигрывания.
    """

    path: str | None
    temporary: bool = False


def make_cached_prepare(synthesize, *, provider: str, voice_key: str, cache_dir):
    """prepare(text) -> Prepared: синтез в файл, БЕЗ проигрывания.

    Разделение синтеза и проигрывания нужно конвейеру стриминга: следующая
    фраза синтезируется, пока играет предыдущая. Слитые вместе (см.
    make_cached_tts, который построен на этой же функции), они делают паузу
    между фразами равной времени синтеза.
    """

    def prepare(text: str) -> Prepared:
        if not text:
            return Prepared(None)
        cached = cache_path(cache_dir, text, voice_key) if len(text) <= MAX_CACHED_CHARS else None
        if cached is not None and cached.exists():
            return Prepared(str(cached), temporary=False)

        if in_cooldown(provider):
            return Prepared(None)

        try:
            data = synthesize(text)
        except Exception as error:
            mark_failure(provider)
            warn_once(provider, f"{provider} недоступен ({error}) — озвучиваю запасным голосом")
            return Prepared(None)

        if not looks_like_mp3(data):
            # Провайдер ответил 200 с телом, которое не mp3 (страница
            # Cloudflare, JSON про тариф). В кеш это класть нельзя.
            mark_failure(provider)
            warn_once(provider, f"{provider} отдал не mp3 — озвучиваю запасным голосом")
            return Prepared(None)

        path = cached or Path(tempfile.gettempdir()) / (
            f"johnny_{provider}_{os.getpid()}_{random.randint(0, 1_000_000)}.mp3"
        )
        try:
            store(path, data)
        except Exception as error:
            warn_once(f"{provider}-store", f"Не смог сохранить синтез ({error})")
            return Prepared(None)
        if cached is not None:
            trim(cache_dir)
        return Prepared(str(path), temporary=cached is None)

    return prepare


def _play_prepared(prepared: Prepared, *, play) -> Exception | None:
    """Проиграть один Prepared. None — успех, иначе — пойманное исключение.

    Общий блок для двух точек вызова play() в say() (первая попытка и
    единственный пересинтез после сбоя кеш-файла), чтобы не дублировать
    play/удаление/уборку целиком под копирку.

    При неудаче кеш-файл (temporary=False) удаляется здесь же: иначе, если
    и пересинтез следом не удастся (например, нет сети), битый файл остался
    бы под тем же именем и проигрывался бы (безуспешно) вечно. Временный
    файл удаляется в любом случае — и при успехе, и при неудаче, как раньше.

    Варнинг в лог и решение "пересинтезировать или сразу fallback" — уже на
    совести say(): у двух случаев (кеш-хит vs всё остальное) разный текст
    лога и разное продолжение.
    """
    try:
        play(prepared.path)
        return None
    except Exception as error:
        if not prepared.temporary:
            Path(prepared.path).unlink(missing_ok=True)
        return error
    finally:
        if prepared.temporary:
            try:
                Path(prepared.path).unlink(missing_ok=True)
            except OSError:
                pass


def make_cached_tts(synthesize, *, provider: str, voice_key: str, fallback, cache_dir, play=None):
    """say() поверх synthesize(text) -> bytes; при любой неудаче — fallback.

    provider — ключ для cooldown и лога (свой у каждого сервиса: отказ
    локального NeMo не должен глушить попытки к fish и наоборот).

    Синтез живёт в make_cached_prepare — тот же код обслуживает конвейер
    стриминга, где файл готовится заранее, а играется позже.
    """
    play = sounds.play_file if play is None else play
    prepare = make_cached_prepare(
        synthesize, provider=provider, voice_key=voice_key, cache_dir=cache_dir
    )

    def say(text: str) -> None:
        if not text:
            return

        # Кеш-хит определяем ДО prepare(): если файл уже лежал на диске,
        # сбой его проигрывания — повод пересинтезировать фразу заново
        # (человек услышит её тем же голосом), а не сразу уходить на
        # запасной. Для свежего файла (кеш-мисс или temporary) такой
        # разницы нет — пересинтез только что уже случился.
        cached = cache_path(cache_dir, text, voice_key) if len(text) <= MAX_CACHED_CHARS else None
        was_cache_hit = cached is not None and cached.exists()

        prepared = prepare(text)
        if prepared.path is None:
            fallback(text)
            return

        error = _play_prepared(prepared, play=play)
        if error is None:
            return

        if was_cache_hit:
            # Файл из кеша не проигрался. Раньше человек в этот момент слышал
            # фразу СВОИМ голосом: битый файл уже удалён (см. _play_prepared),
            # и фраза синтезируется заново — ровно один раз (без цикла), после
            # чего играется. Смена голоса на запасной из-за случайно битого
            # файла в кеше была бы слышна и неожиданна.
            warn_once(f"{provider}-cache", "Файл из кеша не проигрался — синтезирую заново")
            retried = prepare(text)
            if retried.path is not None:
                retry_error = _play_prepared(retried, play=play)
                if retry_error is None:
                    return
                error = retry_error

        warn_once(
            f"{provider}-play", f"Ошибка при проигрывании ({error}) — озвучиваю запасным голосом"
        )
        fallback(text)

    return say
