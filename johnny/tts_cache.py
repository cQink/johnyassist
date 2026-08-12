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


def make_cached_tts(synthesize, *, provider: str, voice_key: str, fallback, cache_dir, play=None):
    """say() поверх synthesize(text) -> bytes; при любой неудаче — fallback.

    provider — ключ для cooldown и лога (свой у каждого сервиса: отказ
    локального NeMo не должен глушить попытки к fish и наоборот).
    """
    play = sounds.play_file if play is None else play

    def say(text: str) -> None:
        if not text:
            return
        cached = cache_path(cache_dir, text, voice_key) if len(text) <= MAX_CACHED_CHARS else None
        if cached is not None and cached.exists():
            try:
                play(str(cached))
                return
            except Exception:
                warn_once(f"{provider}-cache", "Файл из кеша не проигрался — синтезирую заново")
                # Иначе, если пересинтез ниже тоже не удастся (например, сети
                # нет), битый файл остался бы под тем же именем и проигрывался
                # бы (безуспешно) на каждом следующем вызове — вечно.
                cached.unlink(missing_ok=True)

        if in_cooldown(provider):
            # Недавно уже не достучались до сервиса: не ждём заново полный
            # таймаут на эту же фразу, сразу отдаём голос запасному варианту.
            fallback(text)
            return

        try:
            data = synthesize(text)
        except Exception as error:
            mark_failure(provider)
            warn_once(provider, f"{provider} недоступен ({error}) — озвучиваю запасным голосом")
            fallback(text)
            return

        path = cached or Path(tempfile.gettempdir()) / (
            f"johnny_{provider}_{os.getpid()}_{random.randint(0, 1_000_000)}.mp3"
        )
        try:
            store(path, data)
            if cached is not None:
                trim(cache_dir)
            play(str(path))
        except Exception as error:
            warn_once(
                f"{provider}-play", f"Ошибка при проигрывании ({error}) — озвучиваю запасным голосом"
            )
            fallback(text)
        finally:
            if cached is None:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass

    return say
