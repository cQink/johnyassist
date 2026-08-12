"""Пакет actions — фасад.

Импортирует все модули с обработчиками (они регистрируют себя при импорте)
и экспортирует единственную функцию ``execute`` и ``duck_pulse``.
"""

from pathlib import Path

from .registry import ActionResult, registry  # noqa: F401

# Импорт модулей-обработчиков: каждый при загрузке вызывает
# @registry.register(...), наполняя реестр.
from . import browser          # noqa: F401
from . import connector_action  # noqa: F401
from . import discord_action   # noqa: F401
from . import git_action       # noqa: F401
from . import keyboard_action  # noqa: F401
from . import memory_action    # noqa: F401
from . import steam            # noqa: F401
from . import system           # noqa: F401
from . import windows          # noqa: F401  (зависит от browser — грузится после)

# duck_pulse нужен controller.py напрямую.
from .system import duck_pulse  # noqa: F401

_DEFAULT_PLUGINS_DIR = Path(__file__).resolve().parent.parent.parent / "plugins"
_plugin_manager = None


def load_plugins(plugins_dir=None):
    """Обнаружить и загрузить плагины из каталога ``plugins/``.

    Вызывается при старте приложения. Безопасно вызывать повторно —
    уже загруженные плагины пропускаются.
    """
    global _plugin_manager
    from ..plugins import PluginManager

    if _plugin_manager is None:
        _plugin_manager = PluginManager(registry)
    path = Path(plugins_dir) if plugins_dir is not None else _DEFAULT_PLUGINS_DIR
    _plugin_manager.discover_and_load(path)
    return _plugin_manager


def plugin_manager():
    """Текущий PluginManager или None до первого load_plugins()."""
    return _plugin_manager


def execute(routed, apps, channels=None, new_tab=True, people=None, config=None):
    """Единый диспетчер: принимает RoutedAction, отдаёт ActionResult.

    config — целиком, и только для коннекторов: им нужны и ключи из secrets, и
    настройки согласия. Необязательный: без него коннекторы честно откажут
    («не настроено»), а все прежние действия работают как раньше — старые
    вызовы execute() из тестов и плагинов остаются валидными.
    """
    return registry.execute(
        routed.action,
        routed.argument,
        apps=apps,
        channels=channels,
        new_tab=new_tab,
        people=people,
        config=config,
    )
