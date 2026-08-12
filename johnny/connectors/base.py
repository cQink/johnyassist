"""Базовый коннектор: один и тот же путь запроса для всех внешних тулз.

Наследник описывает только СВОЁ: как проверить готовность (available) и как
сделать вызов (_run). Согласие, кеш, квота, cooldown и офлайн-отказ — здесь.

Порядок проверок в call() выбран сознательно, менять его нельзя не подумав:

  1. согласие      — до всего остального. Нет согласия на face/OSINT — сервис
                     не должен узнать даже о факте запроса.
  2. кеш           — ДО проверки готовности и cooldown. Именно это и есть
                     «офлайн-режим» из плана: сервис лёг, ключ отобрали,
                     интернета нет — а вчерашний ответ на тот же вопрос всё
                     ещё под рукой и его можно отдать.
  3. готовность    — нет ключа/бинаря/адреса: честный отказ с причиной.
  4. cooldown      — сервис только что отказал, не ждём таймаут повторно.
  5. квота         — суточный лимит и минимальный интервал между вызовами.
  6. вызов         — ошибка = mark_failure + warn_once + отказ, не исключение.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

from ..http_client import in_cooldown, mark_failure, warn_once
from . import store

# Кеш и счётчики — рядом с моделями, в уже игнорируемом git'ом каталоге.
DEFAULT_CACHE_DIR = Path("models") / "connector-cache"


@dataclass
class ConnectorResult:
    """Результат вызова. message — для голоса и лога, data — машинные детали.

    reason отделён от message намеренно: «нет ключа» и «квота на сегодня
    кончилась» звучат для человека одинаково буднично, но в логе их надо
    различать, иначе неделю ищешь несуществующую сетевую проблему.
    """

    ok: bool
    message: str
    data: dict = field(default_factory=dict)
    cached: bool = False
    reason: str = ""


class Connector(ABC):
    # Имя: ключ кеша, квоты, cooldown и строки в логе. У каждого своё, чтобы
    # отказ одного сервиса не глушил попытки к остальным.
    name: str = "connector"
    # True для всего, что касается людей (лица, OSINT-профили): без явно
    # выданного согласия такой коннектор не делает ни одного запроса.
    requires_consent: bool = False
    ttl_seconds: float = store.DEFAULT_TTL_SECONDS
    # 0 = без лимита. Ставить реальную цифру тарифа: смысл счётчика в том,
    # чтобы бесплатный тариф не кончился в первый же вечер отладки.
    daily_quota: int = 0
    min_interval_seconds: float = 0.0

    def __init__(self, *, cache_dir=None, state_path=None, consent: bool = False):
        self.cache_dir = Path(cache_dir) if cache_dir else DEFAULT_CACHE_DIR
        self.state_path = Path(state_path) if state_path else self.cache_dir / "state.json"
        self._consent = consent

    # --- переопределяют наследники ---

    @abstractmethod
    def available(self) -> tuple[bool, str]:
        """(готов ли, причина отказа). Причина попадёт человеку в ответ,
        поэтому она на русском и без внутренних деталей — ключей в ней быть
        не должно ни при каком раскладе."""

    @abstractmethod
    def _run(self, **params) -> dict:
        """Сделать настоящий вызов. Бросать исключение при неудаче — call()
        сам переведёт его в отказ, отметит cooldown и напишет в лог один раз."""

    def describe(self, data: dict) -> str:
        """Ответ голосом по данным вызова. По умолчанию — сырой факт успеха."""
        return "Готово"

    def cache_params(self, params: dict) -> dict:
        """Какие параметры образуют ключ кеша. По умолчанию — все.

        Переопределять там, где параметр не описывает запрос однозначно. Живой
        пример — путь к файлу: screenshot.png перезаписывается каждым новым
        снимком, и ключ по пути отдавал бы вчерашний ответ на сегодняшнюю
        картинку. Такой коннектор ключуется хешем содержимого, а сам путь в
        ключ не идёт (см. connectors/azure_vision.py и faceplusplus.py).
        """
        return params

    # --- общий путь ---

    def call(self, **params) -> ConnectorResult:
        if self.requires_consent and not self._consent:
            return ConnectorResult(
                False,
                "Этот запрос касается персональных данных — нужно явно разрешить его в настройках",
                reason="no-consent",
            )

        key_params = self.cache_params(params)
        cached = store.read(self.cache_dir, self.name, key_params, self.ttl_seconds)
        if cached is not None:
            return ConnectorResult(True, self.describe(cached), data=cached, cached=True)

        ready, why = self.available()
        if not ready:
            return ConnectorResult(False, why, reason="unavailable")

        if in_cooldown(self.name):
            return ConnectorResult(
                False, f"{self.name} только что не ответил, пробую позже", reason="cooldown"
            )

        used, last = store.usage(self.state_path, self.name)
        if self.daily_quota and used >= self.daily_quota:
            return ConnectorResult(
                False,
                f"Суточный лимит {self.name} исчерпан: {used} из {self.daily_quota}",
                reason="quota",
            )
        if self.min_interval_seconds and last:
            wait = self.min_interval_seconds - (time.time() - last)
            if wait > 0:
                # Ждём сами: сервисы с интервалом обычно отвечают 429, а он
                # тратит квоту и попадает под cooldown, глуша следующий вызов.
                time.sleep(min(wait, self.min_interval_seconds))

        try:
            data = self._run(**params)
        except Exception as error:
            mark_failure(self.name)
            warn_once(self.name, f"{self.name} недоступен ({error})")
            return ConnectorResult(
                False, f"{self.name} не ответил", reason="error", data={"error": str(error)}
            )
        finally:
            # bump в finally: неудачный запрос у большинства сервисов тоже
            # списывается с квоты, и не учесть его — значит её перерасходовать.
            store.bump(self.state_path, self.name)

        store.write(self.cache_dir, self.name, key_params, data)
        store.trim(self.cache_dir)
        return ConnectorResult(True, self.describe(data), data=data)
