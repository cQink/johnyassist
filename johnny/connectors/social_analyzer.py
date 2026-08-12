"""social-analyzer: где ещё встречается этот ник — 900+ сайтов.

Отличие от Azure Vision и Face++: сервиса нет, есть пакет на этой же машине.
Это случай «optional local install» из .md — ключа не нужно, квоты у чужого API
тоже, зато пакета может просто не оказаться, и тогда отказ должен объяснять,
что поставить.

Работаем через run_as_object, а не через подпроцесс: он возвращает готовый dict
{"detected", "unknown", "failed"}. Подпроцесс пришлось бы парсить со stdout, где
JSON перемешан с логами пакета, — лишняя точка отказа на ровном месте.

Приватность: ник — не менее личное, чем лицо. Собрать профили человека по всем
сетям в одну сводку и есть деанонимизация, поэтому requires_consent = True и
журнал доступа, как у Face++.
"""

from __future__ import annotations

import importlib
import json
import time
from pathlib import Path

from .base import Connector

# Имя пакета с дефисом — не идентификатор Python, обычный import невозможен,
# поэтому только importlib. Это не костыль, это то, как пакет опубликован.
PACKAGE = "social-analyzer"

# По всем 900+ сайтам поиск идёт минутами. Для разговора нужны популярные, а
# остальное — отдельная сознательная команда с другим top.
DEFAULT_TOP = 50
KEEP_ITEMS = 15


class SocialAnalyzerConnector(Connector):
    name = "social-analyzer"
    requires_consent = True
    ttl_seconds = 7 * 86400.0
    # Своя машина, чужие лимиты не тратятся — ограничение только чтобы случайный
    # цикл не устроил сотню обходов по 900 сайтам.
    daily_quota = 50
    min_interval_seconds = 0.0

    def __init__(self, *, top: int = DEFAULT_TOP, log_path=None, daily_quota=None, **kwargs):
        super().__init__(**kwargs)
        self._top = int(top)
        self._log_path = Path(log_path) if log_path else self.cache_dir / "osint-access.log"
        if daily_quota is not None:
            self.daily_quota = int(daily_quota)

    def available(self) -> tuple[bool, str]:
        try:
            importlib.import_module(PACKAGE)
        except Exception:
            # Причина человеку — и сразу что делать: иначе «не работает» без
            # подсказки живёт месяцами.
            return False, "Пакет social-analyzer не установлен: pip install social-analyzer"
        return True, ""

    def cache_params(self, params: dict) -> dict:
        """Ник и глубина обхода. top в ключе обязателен: поиск по 50 сайтам и
        по 900 — разные ответы, и первый не должен подменять второй."""
        return {
            "username": str(params.get("username", "")).strip().lower(),
            "top": int(params.get("top", self._top)),
        }

    def _run(self, **params) -> dict:
        username = str(params.get("username", "")).strip()
        if not username:
            raise RuntimeError("не задан ник для поиска")

        module = importlib.import_module(PACKAGE)
        analyzer = module.SocialAnalyzer()
        raw = analyzer.run_as_object(
            username=username,
            # silent: пакет иначе печатает прогресс прямо в консоль Джони.
            silent=True,
            output="json",
            metadata=False,
            top=str(params.get("top", self._top)),
            # good: пакет считает так «уверенные» попадания. maybe/bad дают
            # длинный список догадок, а по нему делают выводы о человеке.
            filter="good",
            profiles="detected",
            mode="fast",
        )
        data = _compact(raw, username=username)
        self._log_access(username, data)
        return data

    def _log_access(self, username: str, data: dict) -> None:
        """Журнал OSINT-доступа: кого искали и сколько нашли. Как у Face++ —
        на вопрос «когда и по кому мы это делали» нужен ответ."""
        record = {
            "at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "username": username,
            "found": data.get("total", 0),
        }
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            with self._log_path.open("a", encoding="utf-8") as log:
                log.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def describe(self, data: dict) -> str:
        total = data.get("total", 0)
        if not total:
            return f"Профилей под ником {data.get('username', '')} не нашлось"
        sites = [m["site"] for m in data.get("matches", []) if m.get("site")][:5]
        answer = f"Нашёл профилей: {total}"
        if sites:
            answer += f" — {', '.join(sites)}"
        if data.get("unknown"):
            # Иначе «нашёл 3» звучит как исчерпывающий ответ, хотя часть сайтов
            # просто не ответила.
            answer += f". Ещё {data['unknown']} сайтов ответили неоднозначно"
        return answer


def _compact(raw: dict, *, username: str) -> dict:
    """Выжимка: сайт, ссылка, уверенность. Разбираем мягко — схема пакета
    менялась между версиями, а падать посреди разговора нельзя."""
    if not isinstance(raw, dict):
        raw = {}
    detected = raw.get("detected") or []
    if not isinstance(detected, list):
        detected = []

    matches = []
    for item in detected[:KEEP_ITEMS]:
        if not isinstance(item, dict):
            continue
        link = item.get("link") or ""
        matches.append(
            {
                "site": item.get("title") or _site_from(link),
                "link": link,
                "rate": _rate(item.get("rate")),
                "type": item.get("type") or "",
            }
        )
    matches.sort(key=lambda m: -m["rate"])
    return {
        "username": username,
        "total": len(matches),
        "unknown": len(raw.get("unknown") or []),
        "failed": len(raw.get("failed") or []),
        "matches": matches,
    }


def _rate(value) -> int:
    """rate приходит строкой вида "%75" — тянуть этот формат дальше значит
    сравнивать строки вместо чисел и однажды получить "%100" < "%9"."""
    try:
        return int(str(value).replace("%", "").strip() or 0)
    except ValueError:
        return 0


def _site_from(link: str) -> str:
    """Домен из ссылки: пакет не всегда заполняет title, а «нашёл профиль
    где-то» без имени сайта бесполезно произносить вслух."""
    without_scheme = link.split("://")[-1]
    return without_scheme.split("/")[0]
