## Refactor for extensibility

Цель: Сделать кодовую базу модульной и безопасной, чтобы можно было быстро добавлять, тестировать и отключать новые интеграции и действия через стандартный плагин-интерфейс.

Критерии успеха:
- Чётко разделены слои: транспорт/вход → маршрутизация → исполнение → адаптеры/плагины → выход.
- Реализован `PluginManager` в `johnny/plugins.py`, поддерживающий загрузку/выгрузку плагинов, health checks и изоляцию ошибок.
- Пример плагина с тестами (`tests/test_plugin_example.py`) и шаблоном `plugins/example_plugin.py`.
- Документация процесса и примеры в `docs/plugins.md`.

Доставляемые артефакты:
- `johnny/plugins.py` — менеджер плагинов и API для регистрации
- `johnny/plugins/` — директория с примером плагина и README
- `tests/test_plugin_example.py` — интеграционный тест
- `docs/plugins.md` — руководство для разработчиков плагинов

Пошаговый план (технический):

1) Анализ текущей архитектуры (0.5 дня)
- Просмотреть `johnny/app.py`, `johnny/chain.py`, `johnny/actions/registry.py`, `johnny/router.py`.
- Составить список точек интеграции, где плагины могут регистрировать handlers.

2) Спроектировать API плагина (0.5 дня)
- Требуемые методы: `setup(manager)`, `handle(event)`, `teardown()`.
- Метаданные манифеста (JSON/YAML): `name`, `version`, `capabilities`, `entry_point`.

Пример `PluginBase` (в `johnny/plugins.py`):

```python
from typing import Any, Dict

class PluginBase:
	name: str

	def __init__(self, manifest: Dict[str, Any]):
		self.manifest = manifest

	def setup(self, manager):
		"""Called at load time. Register handlers via manager.register_handler(...)"""
		raise NotImplementedError

	def handle(self, event: Dict[str, Any]):
		"""Optional direct handler entrypoint for events."""
		raise NotImplementedError

	def teardown(self):
		"""Called at unload time to free resources."""
		raise NotImplementedError
```

3) Реализовать `PluginManager` (1 день)
- API: `load(path_or_manifest)`, `unload(name)`, `register_handler(name, fn, priority=50)`, `dispatch(event)`.
- Логика: try/except вокруг вызовов плагинов, таймауты, health checks.
- Пример: использовать `importlib` для загрузки локальных плагинов из `johnny/plugins/`.

Пример загрузчика (sketch):

```python
import importlib.util
import pathlib

class PluginManager:
	def __init__(self):
		self.plugins = {}
		self.handlers = []

	def load_from_path(self, path: str):
		spec = importlib.util.spec_from_file_location('plugin', path)
		mod = importlib.util.module_from_spec(spec)
		spec.loader.exec_module(mod)
		plugin = mod.Plugin()  # convention: Plugin class
		plugin.setup(self)
		self.plugins[plugin.manifest['name']] = plugin

	def register_handler(self, name, fn, priority=50):
		self.handlers.append((priority, name, fn))
		self.handlers.sort()

	def dispatch(self, event):
		for _, name, fn in list(self.handlers):
			try:
				fn(event)
			except Exception:
				# log and continue
				pass
```

4) Пример плагина (шаблон `johnny/plugins/example_plugin.py`) (0.5 дня)

```python
from johnny.plugins import PluginBase

class Plugin(PluginBase):
	manifest = {'name': 'example', 'version': '0.1', 'capabilities': ['echo']}

	def setup(self, manager):
		manager.register_handler('example_echo', self.on_event)

	def on_event(self, event):
		if event.get('type') == 'message':
			print('example plugin received', event.get('text'))

	def teardown(self):
		pass
```

5) Интеграционные тесты (pytest) (0.5 дня)
- `tests/test_plugin_example.py` — тест загрузки плагина, регистрация handler, dispatch event и assert side-effect.

Пример теста:

```python
def test_example_plugin_load(tmp_path, monkeypatch):
	from johnny.plugins import PluginManager
	pm = PluginManager()
	path = str(tmp_path / 'example_plugin.py')
	path_obj = tmp_path / 'example_plugin.py'
	path_obj.write_text(EXAMPLE_PLUGIN_SOURCE)
	pm.load_from_path(path)
	assert 'example' in pm.plugins
	# dispatch a message event and ensure handler runs (capture stdout or set flag)

```

6) Миграция и backward-compat (1 день)
- Внедрять поэтапно: сначала PluginManager как дополнительный путь регистрации, затем постепенно переводить `actions.registry` на использование менеджера.
- Оставлять совместимые адаптеры для старого API.

7) Документация и onboarding (0.5 дня)
- `docs/plugins.md` с инструкцией: как написать плагин, манифест, публикация, безопасность.

8) Rollout и мониторинг (0.5 дня)
- Метрики: количество загруженных плагинов, ошибок/паник по плагинам, timeouts.

Команды для разработки и тестирования:

```bash
# запустить unit tests
pytest tests/test_plugin_example.py -q

# lint
flake8 johnny/plugins.py johnny/plugins/*.py
```

Риски и примечания:
- Безопасность: плагины должны запускаться с правами ограниченного окружения или внутри контейнеров для доверенных сторон.
- Версионирование API: придерживаться семантического версионирования для манифестов.

Оценка: 3-5 рабочих дней общей работы (можно разбить на два спринта)

