"""Диск коннекторов: кеш ответов с TTL и счётчики квоты, живущие МЕЖДУ запусками.

Почему на диске, а не в памяти: у внешних сервисов квота считается за сутки на
аккаунт (Azure Vision, Face++, любой OSINT-API). Счётчик в памяти обнуляется при
каждом перезапуске Джони, а Джони за вечер правок перезапускается десятки раз —
суточная квота сгорела бы за час, и молча. Поэтому счёт идёт в state.json.

Кеш здесь хранит JSON, а не mp3 (см. johnny/tts_cache.py про звук), но выводы
те же и повторены сознательно: запись через .part, потому что оборванный ответ
не должен остаться под правильным именем и годами выдаваться за результат.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

# Кеш держим щедро: разбор картинки или профиля за сутки не меняется, а каждый
# повторный запрос стоит денег или квоты.
DEFAULT_TTL_SECONDS = 86400.0
MAX_CACHE_FILES = 500


def cache_key(name: str, params: dict) -> str:
    """Имя файла кеша. sort_keys — обязательно: без него один и тот же запрос
    с иначе упорядоченными параметрами создаёт второй файл и второй платный
    вызов."""
    payload = json.dumps(params, sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(f"{name}:{payload}".encode("utf-8")).hexdigest()


def _path(cache_dir, name: str, params: dict) -> Path:
    return Path(cache_dir) / f"{cache_key(name, params)}.json"


def read(cache_dir, name: str, params: dict, ttl: float = DEFAULT_TTL_SECONDS) -> dict | None:
    """Свежий ответ из кеша или None. Битый файл удаляем: иначе он вечно
    выглядит как «кеш есть» и блокирует живой запрос."""
    path = _path(cache_dir, name, params)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        path.unlink(missing_ok=True)
        return None
    # time.time(), а не monotonic: TTL переживает перезапуск, monotonic обнуляется.
    if ttl > 0 and (time.time() - float(raw.get("stored_at", 0))) > ttl:
        return None
    data = raw.get("data")
    return data if isinstance(data, dict) else None


def write(cache_dir, name: str, params: dict, data: dict) -> None:
    path = _path(cache_dir, name, params)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".part")
    temp.write_text(
        json.dumps({"stored_at": time.time(), "data": data}, ensure_ascii=False),
        encoding="utf-8",
    )
    os.replace(temp, path)


def trim(cache_dir, limit: int = MAX_CACHE_FILES) -> None:
    """Оставить не больше limit файлов. Заодно убрать осиротевшие .part —
    их некому удалить, если процесс убили между записью и os.replace."""
    directory = Path(cache_dir)
    if not directory.exists():
        return
    for orphan in directory.glob("*.part"):
        orphan.unlink(missing_ok=True)
    files = sorted(directory.glob("*.json"), key=lambda p: p.stat().st_mtime)
    for old in files[: max(0, len(files) - limit)]:
        old.unlink(missing_ok=True)


# --- Квота и интервал -------------------------------------------------------


def _load_state(state_path) -> dict:
    try:
        raw = json.loads(Path(state_path).read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, ValueError):
        # Битый или отсутствующий state — не повод падать: хуже потерять счёт
        # квоты, чем уронить голосового ассистента на служебном файле.
        return {}


def _save_state(state_path, state: dict) -> None:
    path = Path(state_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".part")
    temp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    os.replace(temp, path)


def _today() -> str:
    return time.strftime("%Y-%m-%d", time.localtime())


def usage(state_path, name: str) -> tuple[int, float]:
    """(сколько вызовов сделано сегодня, когда был последний) для коннектора name."""
    entry = _load_state(state_path).get(name) or {}
    if entry.get("day") != _today():
        return 0, float(entry.get("last", 0.0))  # новые сутки — счёт с нуля
    return int(entry.get("count", 0)), float(entry.get("last", 0.0))


def bump(state_path, name: str) -> None:
    """Отметить состоявшийся вызов. Только НАСТОЯЩИЙ вызов сервиса: ответ из
    кеша квоту не тратит, иначе кеш терял бы смысл."""
    state = _load_state(state_path)
    count, _ = usage(state_path, name)
    state[name] = {"day": _today(), "count": count + 1, "last": time.time()}
    _save_state(state_path, state)
