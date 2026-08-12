"""Клиент локального (или LAN/VPS) TTS-сервиса: NeMo, XTTS - любого, кто умеет
контракт POST /synthesize -> audio/mpeg (см. docs/voice_and_autonomy_roadmap.md).

Зачем отдельно от fish: своя GPU (латентность) и, в локальном режиме,
приватность. Приватность зависит от РАЗМЕЩЕНИЯ, и врать себе тут нельзя:
localhost и LAN - текст не покидает твою сеть; Google Cloud или любой VPS -
текст уходит на чужое железо, пусть и в твой проект. Поэтому облачный адрес
требует токена (см. token ниже), а роадмап требует явного согласия на
отправку приватных данных в облачный endpoint.

Своя цепочка деградации - сервис может быть просто не запущен (или ещё
не проснулся после scale-to-zero), и это НОРМАЛЬНОЕ состояние, а не поломка:
Джони обязан заговорить голосом fish/edge, не заметив разницы.

Кеш, атомарная запись, cooldown и проверка тела на mp3 - общие с fish
(tts_cache.py): дублировать их здесь значило бы чинить каждый баг дважды.
"""

from pathlib import Path

from . import sounds, tts_cache
from .http_client import get, post

# Таймаут короче, чем у fish (10 с): сервис свой, и если он думает над
# короткой фразой дольше пяти секунд, ждать смысла нет - запасной голос
# ответит быстрее, чем человек успеет удивиться тишине.
#
# Облачный NeMo (Cloud Run со scale-to-zero) на холодном старте поднимает
# модель десятки секунд, и ждать этого голосом НЕЛЬЗЯ - Джони замолчит
# посреди разговора. Правильный путь: держать таймаут коротким, отдать фразу
# запасному голосу, а прогрев вынести из голосового пути (warm_up ниже +
# min-instances на стороне облака).
_TIMEOUT = 5.0

_CACHE_DIR = Path(__file__).resolve().parent.parent / "models" / "tts-cache"


def synthesize(
    text: str,
    url: str,
    voice_id: str,
    style: str = "",
    timeout: float = _TIMEOUT,
    token: str = "",
) -> bytes:
    """mp3 от TTS-сервиса. Бросает исключение при любой неудаче.

    503 по контракту означает «модель ещё грузится / занята» - для нас это
    такая же неудача, как и любая другая: молча уходим по цепочке дальше.

    token - Bearer для облачного размещения. Пустой - заголовка нет вовсе
    (localhost в нём не нуждается, а лишний Authorization в логах прокси
    ни к чему).
    """
    payload = {"text": text, "voice_id": voice_id}
    if style:
        payload["style"] = style
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    response = post(url, headers, payload, timeout)
    if response.status_code != 200:
        raise RuntimeError(f"tts-сервис HTTP {response.status_code}: {response.text[:120]}")
    data = response.content
    if not tts_cache.looks_like_mp3(data):
        # Заглушка или прокси перед сервисом запросто отдаёт 200 с HTML/JSON.
        # Тело важнее статуса - иначе мусор осядет в кеше навсегда.
        raise RuntimeError(f"tts-сервис отдал не похожее на mp3 тело: {data[:40]!r}")
    return data


def make_local_tts(
    url: str,
    voice_id: str,
    fallback,
    style: str = "",
    cache_dir=_CACHE_DIR,
    play=sounds.play_file,
    timeout: float = _TIMEOUT,
    token: str = "",
):
    """say() голосом TTS-сервиса; при любой неудаче - fallback."""
    return tts_cache.make_cached_tts(
        lambda text: synthesize(text, url, voice_id, style, timeout, token),
        provider="tts-local",
        # style - в ключе кеша: одна и та же фраза в calm и confident звучит
        # по-разному, а под общим именем второй вариант молча не синтезировался
        # бы вовсе (см. tts_cache.cache_path про смену голоса).
        #
        # token в ключ НЕ идёт: он меняется при ротации, а звучание - нет.
        # Иначе каждая ротация ключа молча обнуляла бы весь кеш. Заодно
        # секрет не попадает в имена файлов на диске.
        voice_key=f"{url}|{voice_id}|{style}",
        fallback=fallback,
        cache_dir=cache_dir,
        play=play,
    )


def warm_up(url: str, timeout: float = 60.0, token: str = "") -> bool:
    """Разбудить облачный сервис заранее, ВНЕ голосового пути.

    Cloud Run со scale-to-zero держит холодный старт в десятки секунд: первая
    же фраза Джони при обычном таймауте в 5 с гарантированно уедет на запасной
    голос. Дёрнуть /health при старте Джони дешевле, чем услышать это вживую.

    Возвращает True, только если сервис ответил и у него поднялась модель.
    Никогда не бросает и никогда не блокирует голос: вызывать в фоне.
    """
    health = url.rsplit("/synthesize", 1)[0] + "/health"
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        response = get(health, headers, timeout)
        return response.status_code == 200 and bool(response.json().get("backend"))
    except Exception:
        # Молча: нет сети, нет сервиса, отвалился DNS - всё это штатно,
        # Джони просто заговорит голосом fish/edge.
        return False
