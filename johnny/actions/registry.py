"""Action Registry — центральный реестр обработчиков команд.

Каждый модуль (browser, system, discord_action, …) при импорте регистрирует
свои обработчики через ``@registry.register("action_name")``.  Фасад
``actions/__init__.py`` вызывает ``registry.execute(action, argument, **ctx)``
вместо гигантского if/elif.
"""

import logging
from dataclasses import dataclass
from typing import Callable, Dict, Any

logger = logging.getLogger(__name__)


@dataclass
class ActionResult:
    ok: bool
    message: str


# Сигнатура обработчика: (argument: str, ctx: dict) -> ActionResult
# ctx содержит apps, channels, new_tab, people и т.д.
ActionHandler = Callable[[str, Dict[str, Any]], ActionResult]


class ActionRegistry:
    """Реестр «имя действия → обработчик».

    register() — декоратор для объявления обработчика.
    execute()  — единый диспетчер, вызываемый из фасада.
    """

    def __init__(self) -> None:
        self._handlers: Dict[str, ActionHandler] = {}

    # -- публичный API --

    def register(self, action_name: str):
        """Декоратор: ``@registry.register("open_url")``."""
        def decorator(func: ActionHandler) -> ActionHandler:
            if action_name in self._handlers:
                logger.warning(
                    "Действие %r перерегистрировано: %s → %s",
                    action_name, self._handlers[action_name], func,
                )
            self._handlers[action_name] = func
            return func
        return decorator

    def execute(self, action_name: str, argument: str, **ctx) -> ActionResult:
        handler = self._handlers.get(action_name)
        if handler is None:
            return ActionResult(False, "Неизвестное действие")
        return handler(argument, ctx)

    def __contains__(self, action_name: str) -> bool:
        return action_name in self._handlers

    def registered_actions(self) -> list[str]:
        return list(self._handlers.keys())

    def unregister(self, action_name: str) -> bool:
        """Снять обработчик (для выгрузки плагинов)."""
        if action_name not in self._handlers:
            return False
        del self._handlers[action_name]
        return True


registry = ActionRegistry()
