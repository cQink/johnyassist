# Плагины Johnny

Johnny расширяется через **плагины** — отдельные Python-модули в каталоге `plugins/`, которые регистрируют новые действия в общем реестре команд.

## Архитектура слоёв

```
Фраза пользователя
       │
       ▼
  router.py          — маршрутизация (commands.yaml → RoutedAction)
       │
       ▼
  app.py             — оркестрация, ответы, цепочки
       │
       ▼
  actions/           — исполнение через ActionRegistry
       ▲
       │
  plugins/           — динамическое расширение (load / unload)
```

Встроенные обработчики (`browser`, `system`, `discord_action`, …) живут в `johnny/actions/` и регистрируются при импорте. Плагины подключаются отдельно через `PluginManager`.

## Быстрый старт

### 1. Создайте файл плагина

`plugins/my_plugin.py`:

```python
from johnny.actions.registry import ActionResult
from johnny.plugins import JohnnyPlugin, PluginManifest


class MyPlugin(JohnnyPlugin):
    @property
    def manifest(self) -> PluginManifest:
        return PluginManifest(
            name="my_plugin",
            version="1.0.0",
            description="Мой первый плагин",
            capabilities=frozenset({"actions"}),
            actions=frozenset({"greet"}),
        )

    def on_load(self, registry) -> None:
        @registry.register("greet")
        def greet(argument: str, ctx: dict) -> ActionResult:
            name = argument.strip() or "друг"
            return ActionResult(True, f"Привет, {name}!")

    def on_unload(self, registry) -> None:
        for action in self.manifest.actions:
            registry.unregister(action)


plugin = MyPlugin()
```

Модуль **обязан** экспортировать переменную `plugin` (экземпляр `JohnnyPlugin`) или функцию `create_plugin()`.

### 2. Добавьте команду в конфиг

`config/commands.yaml`:

```yaml
"привет *":
  action: greet
  template: "*"
```

### 3. Перезапустите Johnny

При старте `app.run()` вызывает `actions.load_plugins()`, который сканирует `plugins/*.py` и загружает все найденные плагины.

## API

### PluginManifest

| Поле | Описание |
|------|----------|
| `name` | Уникальный идентификатор плагина |
| `version` | Semver-строка |
| `description` | Краткое описание |
| `capabilities` | Набор возможностей: `actions`, `connector`, `tts`, … |
| `actions` | Имена действий, которые регистрирует плагин |

### JohnnyPlugin

| Метод | Когда вызывается |
|-------|------------------|
| `manifest` | Свойство — метаданные плагина |
| `on_load(registry)` | Регистрация обработчиков через `@registry.register("name")` |
| `on_unload(registry)` | Снятие обработчиков через `registry.unregister("name")` |

### PluginManager

```python
from johnny.actions import registry, load_plugins
from johnny.plugins import PluginManager

# Автоматически при старте:
mgr = load_plugins()  # сканирует plugins/

# Или вручную:
mgr = PluginManager(registry)
mgr.register(MyPlugin())
mgr.load("my_plugin")
mgr.unload("my_plugin")
mgr.loaded_plugins()   # ['my_plugin']
mgr.manifests()      # список PluginManifest
```

## Пример: example_echo и example_plugin

В репозитории уже есть `plugins/example_echo.py` — действие `echo`, повторяющее аргумент команды.

Добавлен пример `plugins/example_plugin.py` с действием `example_action`, который возвращает `example_plugin: <текст>`.

Для проверки `example_echo` добавьте в `commands.yaml`:

```yaml
"эхо *":
  action: echo
  template: "*"
```

Для проверки `example_plugin` добавьте в `commands.yaml`:

```yaml
"пример *":
  action: example_action
  template: "*"
```

Команда «Джони, пример тест» → ответ «example_plugin: тест».

## Контракт обработчика

Сигнатура совпадает со встроенными действиями:

```python
def handler(argument: str, ctx: dict) -> ActionResult:
    ...
```

Контекст `ctx` содержит `apps`, `channels`, `new_tab`, `people` — те же kwargs, что передаёт `actions.execute()`.

## Тестирование

```bash
pytest tests/test_plugins.py -v
```

Тесты используют изолированный `ActionRegistry`, чтобы не влиять на глобальный реестр встроенных действий.

## Ограничения и рекомендации

- Имена действий должны быть уникальны; повторная регистрация перезаписывает обработчик с warning в лог.
- `on_unload` обязан снять все действия из `manifest.actions`, иначе останутся «висячие» обработчики.
- Файлы, начинающиеся с `_`, в `plugins/` игнорируются.
- Плагины с ошибками импорта логируются и пропускаются — Johnny продолжит работу.
- Разрушительные действия (`shutdown`, `restart`, …) по-прежнему требуют точного совпадения фразы (см. `router.py`).

## Связанные файлы

- `johnny/plugins.py` — API и PluginManager
- `johnny/actions/registry.py` — ActionRegistry
- `johnny/actions/__init__.py` — `load_plugins()`
- `tests/test_plugins.py` — интеграционные тесты
