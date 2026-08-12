"""Слой плагинов Johnny — регистрация действий и коннекторов.

Встроенные модули в ``johnny/actions/`` регистрируют обработчики при импорте.
Плагины — отдельные модули в каталоге ``plugins/``, которые подключаются
через ``PluginManager`` с явными ``load`` / ``unload`` и манифестом.

Слои:
  router (johnny/router.py)  → маршрутизация фразы в RoutedAction
  app (johnny/app.py)        → оркестрация, ответы
  actions (johnny/actions/)  → исполнение через ActionRegistry
  plugins (этот модуль)      → динамическое расширение реестра
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .actions.registry import ActionRegistry

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PluginManifest:
    """Описание плагина для discovery и документации."""

    name: str
    version: str
    description: str = ""
    capabilities: frozenset[str] = field(default_factory=frozenset)
    actions: frozenset[str] = field(default_factory=frozenset)


class JohnnyPlugin(ABC):
    """Базовый класс плагина.

    on_load регистрирует обработчики в переданном реестре;
    on_unload обязан их снять (через registry.unregister).
    """

    @property
    @abstractmethod
    def manifest(self) -> PluginManifest:
        ...

    @abstractmethod
    def on_load(self, registry: ActionRegistry) -> None:
        ...

    @abstractmethod
    def on_unload(self, registry: ActionRegistry) -> None:
        ...


class PluginManager:
    """Управление жизненным циклом плагинов."""

    def __init__(self, registry: ActionRegistry) -> None:
        self._registry = registry
        self._plugins: dict[str, JohnnyPlugin] = {}
        self._loaded: set[str] = set()

    @property
    def registry(self) -> ActionRegistry:
        return self._registry

    def register(self, plugin: JohnnyPlugin) -> None:
        """Зарегистрировать плагин без немедленной загрузки."""
        name = plugin.manifest.name
        if name in self._plugins:
            raise ValueError(f"Плагин {name!r} уже зарегистрирован")
        self._plugins[name] = plugin

    def load(self, name: str) -> None:
        """Загрузить плагин по имени."""
        if name in self._loaded:
            return
        plugin = self._plugins.get(name)
        if plugin is None:
            raise KeyError(f"Плагин {name!r} не найден")
        plugin.on_load(self._registry)
        self._loaded.add(name)
        logger.info(
            "Плагин %s v%s загружен (actions: %s)",
            name,
            plugin.manifest.version,
            ", ".join(sorted(plugin.manifest.actions)) or "—",
        )

    def unload(self, name: str) -> None:
        """Выгрузить плагин и снять его обработчики."""
        if name not in self._loaded:
            return
        plugin = self._plugins[name]
        plugin.on_unload(self._registry)
        self._loaded.discard(name)
        logger.info("Плагин %s выгружен", name)

    def load_all(self) -> None:
        """Загрузить все зарегистрированные плагины."""
        for name in sorted(self._plugins):
            self.load(name)

    def unload_all(self) -> None:
        """Выгрузить все загруженные плагины."""
        for name in sorted(self._loaded):
            self.unload(name)

    def loaded_plugins(self) -> list[str]:
        return sorted(self._loaded)

    def manifests(self) -> list[PluginManifest]:
        return [self._plugins[n].manifest for n in sorted(self._plugins)]

    def discover(self, plugins_dir: Path) -> int:
        """Импортировать ``*.py`` из каталога и зарегистрировать плагины.

        Модуль должен экспортировать объект ``plugin`` (экземпляр JohnnyPlugin)
        или функцию ``create_plugin() -> JohnnyPlugin``.

        Возвращает число зарегистрированных плагинов.
        """
        plugins_dir = Path(plugins_dir)
        if not plugins_dir.is_dir():
            return 0

        count = 0
        for path in sorted(plugins_dir.glob("*.py")):
            if path.name.startswith("_"):
                continue
            plugin = _import_plugin_module(path)
            if plugin is None:
                continue
            self.register(plugin)
            count += 1
        return count

    def discover_and_load(self, plugins_dir: Path) -> int:
        """Discovery + load_all. Возвращает число загруженных плагинов."""
        found = self.discover(plugins_dir)
        self.load_all()
        return found


def _import_plugin_module(path: Path) -> JohnnyPlugin | None:
    """Импортировать файл плагина и извлечь экземпляр JohnnyPlugin."""
    module_name = f"johnny_plugin_{path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        logger.warning("Не удалось создать spec для %s", path)
        return None

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        logger.exception("Ошибка импорта плагина %s", path)
        sys.modules.pop(module_name, None)
        return None

    if hasattr(module, "plugin") and isinstance(module.plugin, JohnnyPlugin):
        return module.plugin
    if hasattr(module, "create_plugin"):
        plugin = module.create_plugin()
        if isinstance(plugin, JohnnyPlugin):
            return plugin
        logger.warning("%s: create_plugin() не вернул JohnnyPlugin", path)
        return None

    logger.debug("%s: нет атрибута plugin или create_plugin — пропуск", path)
    sys.modules.pop(module_name, None)
    return None
