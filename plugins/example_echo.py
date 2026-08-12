"""Пример плагина Johnny — действие ``echo``.

Повторяет аргумент команды голосом. Для подключения добавьте в
``config/commands.yaml`` правило с action ``echo`` (см. docs/plugins.md).
"""

from johnny.actions.registry import ActionResult
from johnny.plugins import JohnnyPlugin, PluginManifest


class EchoPlugin(JohnnyPlugin):
    @property
    def manifest(self) -> PluginManifest:
        return PluginManifest(
            name="example_echo",
            version="1.0.0",
            description="Пример: повторяет аргумент команды",
            capabilities=frozenset({"actions"}),
            actions=frozenset({"echo"}),
        )

    def on_load(self, registry) -> None:
        @registry.register("echo")
        def echo(argument: str, ctx: dict) -> ActionResult:
            text = argument.strip() or "тишина"
            return ActionResult(True, text)

    def on_unload(self, registry) -> None:
        for action in self.manifest.actions:
            registry.unregister(action)


plugin = EchoPlugin()
