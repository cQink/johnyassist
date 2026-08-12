"""Тесты примера плагина Johnny."""

from pathlib import Path

from johnny.actions.registry import ActionRegistry, ActionResult
from johnny.plugins import PluginManager


def test_example_plugin_discovery_and_execution():
    registry = ActionRegistry()
    manager = PluginManager(registry)
    plugins_dir = Path(__file__).resolve().parent.parent / "plugins"

    count = manager.discover(plugins_dir)
    assert count >= 1
    assert any(manifest.name == "example_plugin" for manifest in manager.manifests())

    manager.load("example_plugin")
    assert "example_action" in registry
    result = registry.execute("example_action", "тест")
    assert result == ActionResult(True, "example_plugin: тест")

    manager.unload("example_plugin")
    assert "example_action" not in registry
    result = registry.execute("example_action", "тест")
    assert result.ok is False
