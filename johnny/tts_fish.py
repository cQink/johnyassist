"""Голос Джарвиса через облако fish.audio (клон по reference_id).

Кеш, атомарная запись, проверка тела на mp3 и cooldown живут в общем
tts_cache.py — тем же слоем пользуется локальный TTS-сервис (tts_local.py).
Здесь остаётся только HTTP-запрос к fish и его особенности.
"""

from pathlib import Path

from . import sounds, tts_cache
from .http_client import post

_URL = "https://api.fish.audio/v1/tts"
_MODEL = "s2.1-pro-free"      # бесплатная модель клонирования голоса
_TIMEOUT = 10.0

_CACHE_DIR = Path(__file__).resolve().parent.parent / "models" / "tts-cache"

# Имена сохранены: на них ссылаются tests/test_tts_fish.py и старые вызовы.
_MAX_CACHED_CHARS = tts_cache.MAX_CACHED_CHARS
_MAX_CACHE_FILES = tts_cache.MAX_CACHE_FILES
_MIN_MP3_BYTES = tts_cache.MIN_MP3_BYTES
_looks_like_mp3 = tts_cache.looks_like_mp3
_cache_path = tts_cache.cache_path
_store = tts_cache.store
_trim = tts_cache.trim


def synthesize(text: str, api_key: str, model_id: str, timeout: float = _TIMEOUT) -> bytes:
    """mp3 с голосом Джарвиса. Бросает исключение при любой неудаче."""
    response = post(
        _URL,
        {"Authorization": f"Bearer {api_key}", "model": _MODEL},
        {"text": text, "reference_id": model_id, "format": "mp3"},
        timeout,
    )
    if response.status_code != 200:
        raise RuntimeError(f"fish HTTP {response.status_code}: {response.text[:120]}")
    data = response.content
    if not _looks_like_mp3(data):
        raise RuntimeError(f"fish отдал не похожее на mp3 тело: {data[:40]!r}")
    return data


def make_fish_tts(api_key: str, model_id: str, fallback, cache_dir=_CACHE_DIR, play=sounds.play_file):
    """say() голосом Джарвиса; при любой неудаче — fallback (edge-Дмитрий)."""
    return tts_cache.make_cached_tts(
        lambda text: synthesize(text, api_key, model_id),
        provider="fish",
        voice_key=model_id,
        fallback=fallback,
        cache_dir=cache_dir,
        play=play,
    )
