"""Пример плагина Johnny: действие ``example_action``.

Этот плагин демонстрирует минимальный контракт:
- экспортируется объект ``plugin``
- реализуется ``JohnnyPlugin``
- регистрируется действие через ``ActionRegistry``
"""

from johnny.actions.registry import ActionResult
from johnny.plugins import JohnnyPlugin, PluginManifest


class ExamplePlugin(JohnnyPlugin):
    @property
    def manifest(self) -> PluginManifest:
        return PluginManifest(
            name="example_plugin",
            version="0.1.0",
            description="Минимальный пример плагина для Johnny.",
            capabilities=frozenset({"actions"}),
            actions=frozenset({"example_action"}),
        )

    def on_load(self, registry) -> None:
        @registry.register("example_action")
        def example_action(argument: str, ctx: dict) -> ActionResult:
            text = argument.strip() or "пусто"
            return ActionResult(True, f"example_plugin: {text}")

    def on_unload(self, registry) -> None:
        for action in self.manifest.actions:
            registry.unregister(action)


plugin = ExamplePlugin()
