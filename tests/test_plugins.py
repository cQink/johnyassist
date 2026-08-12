"""Интеграционные тесты слоя плагинов."""

from pathlib import Path

import pytest

from johnny.actions.registry import ActionRegistry, ActionResult
from johnny.plugins import JohnnyPlugin, PluginManager, PluginManifest


class _UpperPlugin(JohnnyPlugin):
    """Тестовый плагин: действие upper — переводит аргумент в верхний регистр."""

    @property
    def manifest(self) -> PluginManifest:
        return PluginManifest(
            name="test_upper",
            version="0.1.0",
            description="Тестовый плагин",
            capabilities=frozenset({"actions"}),
            actions=frozenset({"upper"}),
        )

    def on_load(self, registry: ActionRegistry) -> None:
        @registry.register("upper")
        def upper(argument: str, ctx: dict) -> ActionResult:
            return ActionResult(True, argument.upper())

    def on_unload(self, registry: ActionRegistry) -> None:
        for action in self.manifest.actions:
            registry.unregister(action)


@pytest.fixture
def isolated_registry():
    return ActionRegistry()


@pytest.fixture
def manager(isolated_registry):
    return PluginManager(isolated_registry)


def test_plugin_load_execute_unload(manager, isolated_registry):
    """Полный цикл: register → load → execute → unload."""
    manager.register(_UpperPlugin())
    assert "upper" not in isolated_registry

    manager.load("test_upper")
    assert "upper" in isolated_registry

    result = isolated_registry.execute("upper", "привет")
    assert result == ActionResult(True, "ПРИВЕТ")

    manager.unload("test_upper")
    assert "upper" not in isolated_registry

    result = isolated_registry.execute("upper", "привет")
    assert result.ok is False


def test_load_all_and_loaded_plugins(manager):
    manager.register(_UpperPlugin())
    manager.load_all()
    assert manager.loaded_plugins() == ["test_upper"]

    manager.unload_all()
    assert manager.loaded_plugins() == []


def test_duplicate_register_raises(manager):
    manager.register(_UpperPlugin())
    with pytest.raises(ValueError, match="уже зарегистрирован"):
        manager.register(_UpperPlugin())


def test_load_unknown_raises(manager):
    with pytest.raises(KeyError, match="не найден"):
        manager.load("missing")


def test_registry_unregister(isolated_registry):
    reg = isolated_registry

    @reg.register("temp")
    def temp(argument, ctx):
        return ActionResult(True, "ok")

    assert "temp" in reg
    assert reg.unregister("temp") is True
    assert "temp" not in reg
    assert reg.unregister("temp") is False


def test_discover_example_echo_plugin():
    """Файл plugins/example_echo.py загружается через discovery."""
    reg = ActionRegistry()
    mgr = PluginManager(reg)
    plugins_dir = Path(__file__).resolve().parent.parent / "plugins"
    assert plugins_dir.is_dir()

    count = mgr.discover(plugins_dir)
    assert count >= 1
    assert any(m.name == "example_echo" for m in mgr.manifests())

    mgr.load("example_echo")
    assert "echo" in reg
    result = reg.execute("echo", "раз два")
    assert result == ActionResult(True, "раз два")

    mgr.unload("example_echo")
    assert "echo" not in reg


def test_manifests_sorted(manager):
    manager.register(_UpperPlugin())
    names = [m.name for m in manager.manifests()]
    assert names == ["test_upper"]
