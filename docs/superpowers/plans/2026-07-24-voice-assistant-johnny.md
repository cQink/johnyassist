# Голосовой ассистент «Джони» — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Собрать голосового ассистента для Windows, который по слову-активатору «Джони» распознаёт русскую речь и выполняет действия: запуск игр/программ, открытие сайтов с поиском, системные команды, свободные вопросы через Claude Code.

**Architecture:** Модульный Python-пакет. Слово-активатор ловит Porcupine, команду распознаёт faster-whisper на GPU. Текст идёт в Router — сопоставление с шаблонами из YAML; если совпадений нет, фраза уходит в `claude -p` (Pro-подписка). Действие выполняет модуль Actions, результат озвучивает Speaker. Логика (Config, Router, Actions, ClaudeBrain, Speaker) покрыта юнит-тестами; голосовые модули (Listener, Recognizer) проверяются вручную.

**Tech Stack:** Python 3.10+, faster-whisper, pvporcupine + pvrecorder, sounddevice, PyYAML, pyttsx3 (TTS), pycaw (громкость), pytest.

## Global Constraints

- Платформа: **Windows 11**. Пути и системные вызовы — под Windows.
- Python **3.10+** (используется синтаксис `dict[str, str]` и `X | None`).
- Весь пользовательский текст, конфиги и голосовые ответы — **на русском**.
- Всё работает **офлайн и бесплатно**, кроме опционального `claude -p` (Pro-подписка).
- **Никакого Chrome-расширения**: сайты открываются как URL в браузере по умолчанию.
- Слово-активатор: **«джони»** (`config/settings.yaml` → `wake_word`).
- Секреты (ключ Picovoice) — в `config/settings.yaml`, который в `.gitignore` не попадает, но `picovoice_access_key` в git коммитим пустым.
- TDD: сначала падающий тест, потом минимальная реализация. Частые коммиты.

## File Structure

```
D:\assistent\
  requirements.txt          # зависимости
  main.py                   # точка входа
  config/
    apps.yaml               # имя программы → цель запуска
    commands.yaml           # шаблон → {action, template}
    settings.yaml           # wake_word, ключи, режимы
  johnny/
    __init__.py
    config.py               # загрузка YAML → dataclass'ы
    router.py               # шаблон-матчинг → RoutedAction
    actions.py              # launch_app / open_url / system + execute
    claude_brain.py         # вызов `claude -p`, парсинг ответа
    speaker.py              # озвучка: voice | beep | off (сменный TTS)
    recognizer.py           # faster-whisper: запись+распознавание команды
    listener.py             # Porcupine: ожидание слова-активатора
    app.py                  # оркестратор: главный цикл
  tests/
    test_config.py
    test_router.py
    test_actions.py
    test_claude_brain.py
    test_speaker.py
```

**Порядок задач:** сначала чистая логика с юнит-тестами (Config → Router → Actions → ClaudeBrain → Speaker), затем голосовое железо (Recognizer → Listener), затем оркестратор.

---

### Task 1: Настройка окружения + модуль Config

**Files:**
- Create: `requirements.txt`
- Create: `johnny/__init__.py` (пустой)
- Create: `johnny/config.py`
- Create: `config/apps.yaml`, `config/commands.yaml`, `config/settings.yaml`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: —
- Produces:
  - `CommandRule(pattern: str, action: str, template: str)` — dataclass
  - `Settings(wake_word: str, picovoice_access_key: str, response_mode: str, whisper_model: str, whisper_device: str)` — dataclass
  - `Config(apps: dict[str, str], commands: list[CommandRule], settings: Settings)` — dataclass
  - `load_config(config_dir) -> Config` — читает три YAML; ключи `apps` приводятся к нижнему регистру

- [ ] **Step 1: Создать окружение и зависимости**

Создать `requirements.txt`:

```
pyyaml==6.*
faster-whisper==1.*
pvporcupine==3.*
pvrecorder==1.*
sounddevice==0.4.*
numpy==1.*
pyttsx3==2.*
pycaw==20240210
comtypes
pytest==8.*
```

Создать окружение и поставить то, что нужно для этой задачи:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install pyyaml pytest
```

(Тяжёлые голосовые пакеты ставятся в задачах 6–7, чтобы не блокировать логику.)

- [ ] **Step 2: Написать падающий тест**

`tests/test_config.py`:

```python
from pathlib import Path
from johnny.config import load_config, CommandRule, Settings


def _write_configs(tmp_path: Path) -> Path:
    (tmp_path / "apps.yaml").write_text(
        'Дота: "steam://rungameid/570"\n', encoding="utf-8"
    )
    (tmp_path / "commands.yaml").write_text(
        '"открой канал * на твиче":\n'
        '  action: open_url\n'
        '  template: "twitch.tv/{0}"\n',
        encoding="utf-8",
    )
    (tmp_path / "settings.yaml").write_text(
        'wake_word: "джони"\n'
        'picovoice_access_key: ""\n'
        'response_mode: voice\n'
        'whisper_model: medium\n'
        'whisper_device: cuda\n',
        encoding="utf-8",
    )
    return tmp_path


def test_load_config_reads_all_sections(tmp_path):
    cfg = load_config(_write_configs(tmp_path))
    # ключи apps приведены к нижнему регистру
    assert cfg.apps["дота"] == "steam://rungameid/570"
    assert cfg.commands[0] == CommandRule(
        pattern="открой канал * на твиче",
        action="open_url",
        template="twitch.tv/{0}",
    )
    assert cfg.settings == Settings(
        wake_word="джони",
        picovoice_access_key="",
        response_mode="voice",
        whisper_model="medium",
        whisper_device="cuda",
    )
```

- [ ] **Step 3: Запустить тест — убедиться, что падает**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'johnny'` / `ImportError`.

- [ ] **Step 4: Реализовать модуль**

`johnny/__init__.py` — пустой файл.

`johnny/config.py`:

```python
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass
class CommandRule:
    pattern: str
    action: str
    template: str


@dataclass
class Settings:
    wake_word: str
    picovoice_access_key: str
    response_mode: str
    whisper_model: str
    whisper_device: str


@dataclass
class Config:
    apps: dict[str, str]
    commands: list[CommandRule]
    settings: Settings


def _read_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def load_config(config_dir) -> Config:
    config_dir = Path(config_dir)

    apps_raw = _read_yaml(config_dir / "apps.yaml")
    apps = {str(name).lower(): str(target) for name, target in apps_raw.items()}

    commands_raw = _read_yaml(config_dir / "commands.yaml")
    commands = [
        CommandRule(pattern=pattern, action=body["action"], template=body["template"])
        for pattern, body in commands_raw.items()
    ]

    s = _read_yaml(config_dir / "settings.yaml")
    settings = Settings(
        wake_word=s.get("wake_word", "джони"),
        picovoice_access_key=s.get("picovoice_access_key", ""),
        response_mode=s.get("response_mode", "voice"),
        whisper_model=s.get("whisper_model", "medium"),
        whisper_device=s.get("whisper_device", "cuda"),
    )

    return Config(apps=apps, commands=commands, settings=settings)
```

- [ ] **Step 5: Запустить тест — убедиться, что проходит**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_config.py -v`
Expected: PASS.

- [ ] **Step 6: Создать реальные конфиги проекта**

`config/apps.yaml`:

```yaml
дота: "steam://rungameid/570"
апекс: "steam://rungameid/1172470"
дискорд: "C:/Users/Admin/AppData/Local/Discord/Update.exe --processStart Discord.exe"
```

`config/commands.yaml`:

```yaml
"открой канал * на твиче":
  action: open_url
  template: "twitch.tv/{0}"
"найди на ютубе *":
  action: open_url
  template: "youtube.com/results?search_query={0}"
"открой ютуб":
  action: open_url
  template: "youtube.com"
"открой твич":
  action: open_url
  template: "twitch.tv"
"запусти *":
  action: launch_app
  template: "{0}"
"сделай громче":
  action: system
  template: "volume_up"
"сделай тише":
  action: system
  template: "volume_down"
"выключи звук":
  action: system
  template: "mute"
"заблокируй компьютер":
  action: system
  template: "lock"
```

`config/settings.yaml`:

```yaml
wake_word: "джони"
picovoice_access_key: ""      # вставить бесплатный ключ с console.picovoice.ai
response_mode: voice          # voice | beep | off
whisper_model: medium         # small | medium | large-v3
whisper_device: cuda          # cuda | cpu
```

- [ ] **Step 7: Commit**

```bash
git add requirements.txt johnny/__init__.py johnny/config.py config/ tests/test_config.py
git commit -m "feat: add config loader and project config files"
```

---

### Task 2: Router — сопоставление с шаблонами

**Files:**
- Create: `johnny/router.py`
- Test: `tests/test_router.py`

**Interfaces:**
- Consumes: `CommandRule` из `johnny.config`
- Produces:
  - `RoutedAction(action: str, argument: str)` — dataclass
  - `route(text: str, commands: list[CommandRule]) -> RoutedAction | None` — `*` в шаблоне ловит одно или несколько слов; захваты подставляются в `template` через `.format()`; `None`, если ни один шаблон не подошёл

- [ ] **Step 1: Написать падающий тест**

`tests/test_router.py`:

```python
from johnny.config import CommandRule
from johnny.router import route, RoutedAction

COMMANDS = [
    CommandRule("открой канал * на твиче", "open_url", "twitch.tv/{0}"),
    CommandRule("найди на ютубе *", "open_url", "youtube.com/results?search_query={0}"),
    CommandRule("открой ютуб", "open_url", "youtube.com"),
    CommandRule("запусти *", "launch_app", "{0}"),
    CommandRule("сделай громче", "system", "volume_up"),
]


def test_pattern_with_capture_fills_template():
    assert route("открой канал 9impulse на твиче", COMMANDS) == RoutedAction(
        "open_url", "twitch.tv/9impulse"
    )


def test_capture_can_span_several_words():
    assert route("найди на ютубе как варить борщ", COMMANDS) == RoutedAction(
        "open_url", "youtube.com/results?search_query=как варить борщ"
    )


def test_pattern_without_capture():
    assert route("открой ютуб", COMMANDS) == RoutedAction("open_url", "youtube.com")


def test_launch_app_passes_name_as_argument():
    assert route("запусти дота", COMMANDS) == RoutedAction("launch_app", "дота")


def test_normalizes_case_and_trailing_punctuation():
    assert route("Сделай громче!", COMMANDS) == RoutedAction("system", "volume_up")


def test_no_match_returns_none():
    assert route("расскажи анекдот", COMMANDS) is None
```

- [ ] **Step 2: Запустить тест — убедиться, что падает**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_router.py -v`
Expected: FAIL — `ImportError` / `route` не определена.

- [ ] **Step 3: Реализовать модуль**

`johnny/router.py`:

```python
import re
from dataclasses import dataclass

from .config import CommandRule


@dataclass
class RoutedAction:
    action: str
    argument: str


def _normalize(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[.,!?;:]+$", "", text)   # убрать хвостовую пунктуацию
    text = re.sub(r"\s+", " ", text)          # схлопнуть пробелы
    return text


def _pattern_to_regex(pattern: str) -> re.Pattern:
    parts = _normalize(pattern).split("*")
    body = "(.+?)".join(re.escape(p) for p in parts)
    return re.compile("^" + body + "$")


def route(text: str, commands: list[CommandRule]) -> RoutedAction | None:
    norm = _normalize(text)
    for rule in commands:
        match = _pattern_to_regex(rule.pattern).match(norm)
        if match:
            argument = rule.template.format(*match.groups())
            return RoutedAction(action=rule.action, argument=argument)
    return None
```

- [ ] **Step 4: Запустить тест — убедиться, что проходит**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_router.py -v`
Expected: PASS (6 тестов).

- [ ] **Step 5: Commit**

```bash
git add johnny/router.py tests/test_router.py
git commit -m "feat: add template router"
```

---

### Task 3: Actions — выполнение действий

**Files:**
- Create: `johnny/actions.py`
- Test: `tests/test_actions.py`

**Interfaces:**
- Consumes: `RoutedAction` из `johnny.router`
- Produces:
  - `ActionResult(ok: bool, message: str)` — dataclass; `message` идёт в озвучку
  - `open_url(argument: str) -> None`
  - `launch_app(name: str, apps: dict[str, str]) -> bool` — `False`, если имя не в `apps`
  - `system_command(argument: str) -> bool` — `False`, если команда неизвестна; реальные обработчики в `SYSTEM_HANDLERS`
  - `execute(routed: RoutedAction, apps: dict[str, str]) -> ActionResult` — диспетчер

- [ ] **Step 1: Написать падающий тест**

`tests/test_actions.py`:

```python
import johnny.actions as actions
from johnny.actions import execute, ActionResult
from johnny.router import RoutedAction


def test_execute_open_url_calls_browser(monkeypatch):
    opened = {}
    monkeypatch.setattr(actions.webbrowser, "open", lambda url: opened.setdefault("url", url))
    result = execute(RoutedAction("open_url", "twitch.tv/9impulse"), apps={})
    assert opened["url"] == "https://twitch.tv/9impulse"
    assert result.ok is True


def test_open_url_keeps_existing_scheme(monkeypatch):
    opened = {}
    monkeypatch.setattr(actions.webbrowser, "open", lambda url: opened.setdefault("url", url))
    actions.open_url("steam://rungameid/570")
    assert opened["url"] == "steam://rungameid/570"


def test_execute_launch_known_app(monkeypatch):
    launched = {}
    monkeypatch.setattr(actions, "_start", lambda target: launched.setdefault("t", target))
    result = execute(RoutedAction("launch_app", "дота"), apps={"дота": "steam://rungameid/570"})
    assert launched["t"] == "steam://rungameid/570"
    assert result == ActionResult(True, "Запускаю дота")


def test_execute_launch_unknown_app_reports_failure():
    result = execute(RoutedAction("launch_app", "нечто"), apps={})
    assert result == ActionResult(False, "Не знаю такой программы")


def test_execute_system_calls_handler(monkeypatch):
    called = {}
    monkeypatch.setitem(actions.SYSTEM_HANDLERS, "volume_up", lambda: called.setdefault("v", True))
    result = execute(RoutedAction("system", "volume_up"), apps={})
    assert called.get("v") is True
    assert result.ok is True


def test_execute_unknown_system_command_reports_failure():
    result = execute(RoutedAction("system", "не_существует"), apps={})
    assert result.ok is False
```

- [ ] **Step 2: Запустить тест — убедиться, что падает**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_actions.py -v`
Expected: FAIL — `ImportError`.

- [ ] **Step 3: Реализовать модуль**

`johnny/actions.py`:

```python
import os
import subprocess
import webbrowser
from dataclasses import dataclass

from .router import RoutedAction

_SCHEMES = ("http://", "https://", "steam://")


@dataclass
class ActionResult:
    ok: bool
    message: str


def open_url(argument: str) -> None:
    url = argument if argument.startswith(_SCHEMES) else "https://" + argument
    webbrowser.open(url)


def _start(target: str) -> None:
    """Запуск цели: протокол/URL — через ОС, иначе — как процесс."""
    if target.startswith(_SCHEMES):
        os.startfile(target)  # type: ignore[attr-defined]  # Windows-only
    else:
        subprocess.Popen(target)


def launch_app(name: str, apps: dict[str, str]) -> bool:
    target = apps.get(name.lower().strip())
    if target is None:
        return False
    _start(target)
    return True


def _volume_step(direction: int) -> None:
    from ctypes import POINTER, cast

    from comtypes import CLSCTX_ALL
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

    devices = AudioUtilities.GetSpeakers()
    interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
    volume = cast(interface, POINTER(IAudioEndpointVolume))
    current = volume.GetMasterVolumeLevelScalar()
    volume.SetMasterVolumeLevelScalar(min(1.0, max(0.0, current + direction * 0.1)), None)


def _mute() -> None:
    from ctypes import POINTER, cast

    from comtypes import CLSCTX_ALL
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

    devices = AudioUtilities.GetSpeakers()
    interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
    volume = cast(interface, POINTER(IAudioEndpointVolume))
    volume.SetMute(1, None)


def _lock() -> None:
    subprocess.Popen(["rundll32.exe", "user32.dll,LockWorkStation"])


SYSTEM_HANDLERS = {
    "volume_up": lambda: _volume_step(+1),
    "volume_down": lambda: _volume_step(-1),
    "mute": _mute,
    "lock": _lock,
}


def system_command(argument: str) -> bool:
    handler = SYSTEM_HANDLERS.get(argument)
    if handler is None:
        return False
    handler()
    return True


def execute(routed: RoutedAction, apps: dict[str, str]) -> ActionResult:
    if routed.action == "open_url":
        open_url(routed.argument)
        return ActionResult(True, "Открываю")
    if routed.action == "launch_app":
        if launch_app(routed.argument, apps):
            return ActionResult(True, f"Запускаю {routed.argument}")
        return ActionResult(False, "Не знаю такой программы")
    if routed.action == "system":
        if system_command(routed.argument):
            return ActionResult(True, "Готово")
        return ActionResult(False, "Не знаю такой системной команды")
    return ActionResult(False, "Неизвестное действие")
```

- [ ] **Step 4: Запустить тест — убедиться, что проходит**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_actions.py -v`
Expected: PASS (6 тестов). `pycaw`/`comtypes` не импортируются на верхнем уровне, поэтому тесты идут без них.

- [ ] **Step 5: Commit**

```bash
git add johnny/actions.py tests/test_actions.py
git commit -m "feat: add actions (launch, open url, system)"
```

---

### Task 4: ClaudeBrain — запасной умный разбор

**Files:**
- Create: `johnny/claude_brain.py`
- Test: `tests/test_claude_brain.py`

**Interfaces:**
- Consumes: `RoutedAction` из `johnny.router`
- Produces:
  - `ClaudeResult(routed: RoutedAction | None, reply: str | None)` — dataclass
  - `interpret(text: str, run_claude=<callable>) -> ClaudeResult | None` — `run_claude(prompt: str) -> str` внедряется для тестов; по умолчанию вызывает `claude -p`. Ожидает от Claude JSON `{"action": "...", "argument": "...", "reply": "..."}`

- [ ] **Step 1: Написать падающий тест**

`tests/test_claude_brain.py`:

```python
from johnny.claude_brain import interpret, ClaudeResult
from johnny.router import RoutedAction


def test_interpret_action_response():
    fake = lambda prompt: '{"action": "open_url", "argument": "twitch.tv/x", "reply": "Открываю"}'
    result = interpret("глянь стрим x", run_claude=fake)
    assert result == ClaudeResult(routed=RoutedAction("open_url", "twitch.tv/x"), reply="Открываю")


def test_interpret_answer_response():
    fake = lambda prompt: '{"action": "answer", "reply": "Сейчас в Москве +20."}'
    result = interpret("какая погода", run_claude=fake)
    assert result == ClaudeResult(routed=None, reply="Сейчас в Москве +20.")


def test_interpret_extracts_json_from_surrounding_text():
    fake = lambda prompt: 'Конечно!\n{"action": "answer", "reply": "Готово"}\nСпасибо'
    result = interpret("что-то", run_claude=fake)
    assert result.reply == "Готово"


def test_interpret_returns_none_on_garbage():
    fake = lambda prompt: "тут вообще нет json"
    assert interpret("что-то", run_claude=fake) is None
```

- [ ] **Step 2: Запустить тест — убедиться, что падает**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_claude_brain.py -v`
Expected: FAIL — `ImportError`.

- [ ] **Step 3: Реализовать модуль**

`johnny/claude_brain.py`:

```python
import json
import re
import subprocess
from dataclasses import dataclass

from .router import RoutedAction

_PROMPT = """Ты — маршрутизатор голосового ассистента. Пользователь сказал:
"{text}"

Ответь ТОЛЬКО одним JSON-объектом без пояснений. Поля:
- "action": одно из "open_url", "launch_app", "system", "answer"
- "argument": для open_url — URL; для launch_app — имя программы; для system — одна из volume_up/volume_down/mute/lock; для answer — пустая строка
- "reply": короткая фраза на русском для озвучивания

Если это обычный вопрос, а не команда — action = "answer" и дай короткий ответ в reply."""


def _run_claude(prompt: str) -> str:
    result = subprocess.run(
        ["claude", "-p", prompt],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    return result.stdout


def _extract_json(raw: str) -> dict | None:
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


@dataclass
class ClaudeResult:
    routed: RoutedAction | None
    reply: str | None


def interpret(text: str, run_claude=_run_claude) -> ClaudeResult | None:
    data = _extract_json(run_claude(_PROMPT.format(text=text)))
    if data is None:
        return None
    action = data.get("action")
    reply = data.get("reply") or None
    if action == "answer":
        return ClaudeResult(routed=None, reply=reply)
    if action in ("open_url", "launch_app", "system"):
        routed = RoutedAction(action=action, argument=data.get("argument", ""))
        return ClaudeResult(routed=routed, reply=reply)
    return None
```

- [ ] **Step 4: Запустить тест — убедиться, что проходит**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_claude_brain.py -v`
Expected: PASS (4 теста).

- [ ] **Step 5: Commit**

```bash
git add johnny/claude_brain.py tests/test_claude_brain.py
git commit -m "feat: add claude fallback brain"
```

---

### Task 5: Speaker — озвучка (voice | beep | off)

**Files:**
- Create: `johnny/speaker.py`
- Test: `tests/test_speaker.py`

**Interfaces:**
- Consumes: —
- Produces:
  - `Speaker(mode: str, tts=None, beep=None)` — `mode` из `voice|beep|off`; `tts(text)` и `beep()` внедряются (по умолчанию — pyttsx3 и winsound)
  - `Speaker.say(text: str) -> None` — диспетчер по режиму
  - `make_speaker(mode: str) -> Speaker` — фабрика с реальными движками

- [ ] **Step 1: Написать падающий тест**

`tests/test_speaker.py`:

```python
from johnny.speaker import Speaker


def test_voice_mode_calls_tts():
    said = []
    sp = Speaker("voice", tts=said.append, beep=lambda: said.append("BEEP"))
    sp.say("привет")
    assert said == ["привет"]


def test_beep_mode_calls_beep_only():
    events = []
    sp = Speaker("beep", tts=lambda t: events.append(("tts", t)), beep=lambda: events.append("beep"))
    sp.say("привет")
    assert events == ["beep"]


def test_off_mode_stays_silent():
    events = []
    sp = Speaker("off", tts=lambda t: events.append(t), beep=lambda: events.append("beep"))
    sp.say("привет")
    assert events == []
```

- [ ] **Step 2: Запустить тест — убедиться, что падает**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_speaker.py -v`
Expected: FAIL — `ImportError`.

- [ ] **Step 3: Реализовать модуль**

`johnny/speaker.py`:

```python
class Speaker:
    def __init__(self, mode: str, tts=None, beep=None):
        self.mode = mode
        self._tts = tts
        self._beep = beep

    def say(self, text: str) -> None:
        if self.mode == "voice" and self._tts is not None:
            self._tts(text)
        elif self.mode == "beep" and self._beep is not None:
            self._beep()
        # mode == "off": молчание


def _make_tts():
    import pyttsx3

    engine = pyttsx3.init()
    for voice in engine.getProperty("voices"):
        if "russian" in voice.name.lower() or "ru" in (voice.id or "").lower():
            engine.setProperty("voice", voice.id)
            break

    def say(text: str) -> None:
        engine.say(text)
        engine.runAndWait()

    return say


def _make_beep():
    import winsound

    return lambda: winsound.Beep(880, 150)


def make_speaker(mode: str) -> Speaker:
    tts = _make_tts() if mode == "voice" else None
    beep = _make_beep() if mode in ("voice", "beep") else None
    return Speaker(mode, tts=tts, beep=beep)
```

Примечание: pyttsx3 использует системные голоса Windows (SAPI5). Для более «живого» голоса позже можно заменить `_make_tts` на Silero или edge-tts — интерфейс `say(text)` не меняется.

- [ ] **Step 4: Запустить тест — убедиться, что проходит**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_speaker.py -v`
Expected: PASS (3 теста).

- [ ] **Step 5: Ручная проверка голоса**

```powershell
.\.venv\Scripts\python.exe -m pip install pyttsx3
.\.venv\Scripts\python.exe -c "from johnny.speaker import make_speaker; make_speaker('voice').say('Привет, я Джони')"
```

Expected: слышен голос «Привет, я Джони». Если русского голоса нет — доустановить русский голос в Windows (Параметры → Время и язык → Речь).

- [ ] **Step 6: Commit**

```bash
git add johnny/speaker.py tests/test_speaker.py
git commit -m "feat: add speaker with voice/beep/off modes"
```

---

### Task 6: Recognizer — распознавание команды (Whisper)

**Files:**
- Create: `johnny/recognizer.py`

**Interfaces:**
- Consumes: `Settings` из `johnny.config`
- Produces:
  - `Recognizer(model: str, device: str)` — грузит faster-whisper один раз
  - `Recognizer.listen_command(max_seconds: float = 8.0, silence_seconds: float = 1.2) -> str` — пишет с микрофона до паузы, возвращает распознанный русский текст (пустая строка, если тишина)

Голосовой модуль — проверяется вручную (юнит-тесты не пишем, так как нужен реальный микрофон и GPU).

- [ ] **Step 1: Поставить зависимости**

```powershell
.\.venv\Scripts\python.exe -m pip install faster-whisper sounddevice numpy
```

- [ ] **Step 2: Реализовать модуль**

`johnny/recognizer.py`:

```python
import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel

_SAMPLE_RATE = 16000
_BLOCK = 4000  # 0.25 c


class Recognizer:
    def __init__(self, model: str, device: str):
        compute_type = "float16" if device == "cuda" else "int8"
        self._model = WhisperModel(model, device=device, compute_type=compute_type)

    def _record_until_silence(self, max_seconds: float, silence_seconds: float) -> np.ndarray:
        frames: list[np.ndarray] = []
        silent_blocks = 0
        needed_silent = int(silence_seconds * _SAMPLE_RATE / _BLOCK)
        max_blocks = int(max_seconds * _SAMPLE_RATE / _BLOCK)

        with sd.InputStream(samplerate=_SAMPLE_RATE, channels=1, dtype="float32", blocksize=_BLOCK) as stream:
            for _ in range(max_blocks):
                block, _ = stream.read(_BLOCK)
                block = block.flatten()
                frames.append(block)
                if np.sqrt(np.mean(block**2)) < 0.01:  # RMS-порог тишины
                    silent_blocks += 1
                    if silent_blocks >= needed_silent and len(frames) > needed_silent:
                        break
                else:
                    silent_blocks = 0
        return np.concatenate(frames) if frames else np.zeros(1, dtype="float32")

    def listen_command(self, max_seconds: float = 8.0, silence_seconds: float = 1.2) -> str:
        audio = self._record_until_silence(max_seconds, silence_seconds)
        segments, _ = self._model.transcribe(audio, language="ru", vad_filter=True)
        return " ".join(seg.text.strip() for seg in segments).strip()
```

- [ ] **Step 3: Ручная проверка распознавания**

```powershell
.\.venv\Scripts\python.exe -c "from johnny.recognizer import Recognizer; r = Recognizer('medium', 'cuda'); print('Говори...'); print(repr(r.listen_command()))"
```

Expected: после «Говори...» произнести «открой ютуб» — в консоли печатается распознанный текст. Если CUDA недоступна — временно указать `'cpu'`.

- [ ] **Step 4: Commit**

```bash
git add johnny/recognizer.py
git commit -m "feat: add whisper recognizer"
```

---

### Task 7: Listener — слово-активатор (Porcupine)

**Files:**
- Create: `johnny/listener.py`

**Interfaces:**
- Consumes: `Settings.picovoice_access_key`
- Produces:
  - `Listener(access_key: str, keyword_path: str)` — инициализирует Porcupine + PvRecorder
  - `Listener.wait_for_wake_word() -> None` — блокирует до произнесения «Джони»
  - `Listener.close() -> None`

Голосовой модуль — проверяется вручную.

- [ ] **Step 1: Поставить зависимости и подготовить модель слова**

```powershell
.\.venv\Scripts\python.exe -m pip install pvporcupine pvrecorder
```

Затем вручную: зарегистрироваться на `console.picovoice.ai` (бесплатно), скопировать **AccessKey** в `config/settings.yaml` → `picovoice_access_key`. В разделе Porcupine создать кастомное слово **«джони»** (язык — русский), скачать `.ppn`-файл и положить в `models/johnny_ru.ppn`. Скачать русский `.pv`-параметр-файл модели, положить в `models/porcupine_params_ru.pv`.

- [ ] **Step 2: Реализовать модуль**

`johnny/listener.py`:

```python
import struct

import pvporcupine
from pvrecorder import PvRecorder

_MODEL_PARAMS = "models/porcupine_params_ru.pv"


class Listener:
    def __init__(self, access_key: str, keyword_path: str):
        self._porcupine = pvporcupine.create(
            access_key=access_key,
            keyword_paths=[keyword_path],
            model_path=_MODEL_PARAMS,
        )
        self._recorder = PvRecorder(frame_length=self._porcupine.frame_length)
        self._recorder.start()

    def wait_for_wake_word(self) -> None:
        while True:
            pcm = self._recorder.read()
            if self._porcupine.process(pcm) >= 0:
                return

    def close(self) -> None:
        self._recorder.stop()
        self._recorder.delete()
        self._porcupine.delete()
```

- [ ] **Step 3: Ручная проверка слова-активатора**

```powershell
.\.venv\Scripts\python.exe -c "from johnny.listener import Listener; from johnny.config import load_config; s = load_config('config').settings; l = Listener(s.picovoice_access_key, 'models/johnny_ru.ppn'); print('Скажи Джони...'); l.wait_for_wake_word(); print('УСЛЫШАЛ!'); l.close()"
```

Expected: после «Скажи Джони...» произнести «Джони» — печатается «УСЛЫШАЛ!». Если срабатывает плохо — подобрать созвучное слово в консоли Picovoice (см. спеку).

- [ ] **Step 4: Commit**

```bash
git add johnny/listener.py
git commit -m "feat: add porcupine wake-word listener"
```

---

### Task 8: Оркестратор + точка входа

**Files:**
- Create: `johnny/app.py`
- Create: `main.py`

**Interfaces:**
- Consumes: всё выше — `load_config`, `Listener`, `Recognizer`, `route`, `execute`, `interpret`, `make_speaker`
- Produces:
  - `run(config_dir: str = "config") -> None` — главный цикл
  - `handle_command(text, config, speaker) -> None` — маршрутизация одной распознанной фразы (шаблон → иначе Claude), тестируется юнит-тестом

- [ ] **Step 1: Написать падающий тест для маршрутизации одной фразы**

Добавить в `tests/test_app.py`:

```python
from johnny.app import handle_command
from johnny.config import Config, Settings, CommandRule


def _config():
    return Config(
        apps={"дота": "steam://rungameid/570"},
        commands=[CommandRule("запусти *", "launch_app", "{0}")],
        settings=Settings("джони", "", "off", "medium", "cuda"),
    )


class SpySpeaker:
    def __init__(self):
        self.said = []

    def say(self, text):
        self.said.append(text)


def test_template_match_executes_action(monkeypatch):
    import johnny.app as app
    launched = {}
    monkeypatch.setattr(app, "execute", lambda routed, apps: _fake_result(launched, routed))
    sp = SpySpeaker()
    handle_command("запусти дота", _config(), sp)
    assert launched["action"] == "launch_app"
    assert sp.said == ["Запускаю дота"]


def _fake_result(store, routed):
    from johnny.actions import ActionResult
    store["action"] = routed.action
    return ActionResult(True, "Запускаю дота")


def test_no_template_falls_back_to_claude(monkeypatch):
    import johnny.app as app
    from johnny.claude_brain import ClaudeResult
    monkeypatch.setattr(app, "interpret", lambda text: ClaudeResult(routed=None, reply="Отвечаю"))
    sp = SpySpeaker()
    handle_command("расскажи анекдот", _config(), sp)
    assert sp.said == ["Отвечаю"]
```

- [ ] **Step 2: Запустить тест — убедиться, что падает**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_app.py -v`
Expected: FAIL — `ImportError`.

- [ ] **Step 3: Реализовать оркестратор**

`johnny/app.py`:

```python
from .actions import execute
from .claude_brain import interpret
from .config import load_config
from .router import route
from .speaker import make_speaker


def handle_command(text: str, config, speaker) -> None:
    if not text.strip():
        speaker.say("Не расслышал")
        return

    routed = route(text, config.commands)
    if routed is not None:
        result = execute(routed, config.apps)
        speaker.say(result.message)
        return

    # шаблон не подошёл — умный разбор
    claude = interpret(text)
    if claude is None:
        speaker.say("Не понял команду")
        return
    if claude.routed is not None:
        result = execute(claude.routed, config.apps)
        speaker.say(claude.reply or result.message)
    else:
        speaker.say(claude.reply or "Готово")


def run(config_dir: str = "config") -> None:
    from .listener import Listener
    from .recognizer import Recognizer

    config = load_config(config_dir)
    speaker = make_speaker(config.settings.response_mode)
    recognizer = Recognizer(config.settings.whisper_model, config.settings.whisper_device)
    listener = Listener(config.settings.picovoice_access_key, "models/johnny_ru.ppn")

    print("Джони готов. Скажи «Джони» и команду.")
    try:
        while True:
            listener.wait_for_wake_word()
            speaker.say("Слушаю") if speaker.mode != "off" else None
            text = recognizer.listen_command()
            print(f"Распознано: {text!r}")
            handle_command(text, config, speaker)
    except KeyboardInterrupt:
        print("Выход.")
    finally:
        listener.close()
```

`main.py`:

```python
from johnny.app import run

if __name__ == "__main__":
    run()
```

- [ ] **Step 4: Запустить тест — убедиться, что проходит**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_app.py -v`
Expected: PASS (2 теста).

- [ ] **Step 5: Прогнать все тесты**

Run: `.\.venv\Scripts\python.exe -m pytest -v`
Expected: PASS (все тесты из задач 1–5, 8).

- [ ] **Step 6: Полная ручная проверка (end-to-end)**

```powershell
.\.venv\Scripts\python.exe main.py
```

Expected: сказать «Джони» → «Слушаю» → «открой ютуб» → открывается YouTube и голос «Открываю». Проверить также «запусти дота» и свободный вопрос.

- [ ] **Step 7: Commit**

```bash
git add johnny/app.py main.py tests/test_app.py
git commit -m "feat: add orchestrator and entry point"
```

---

## Self-Review

**Покрытие спеки:**
- Listener (Porcupine, слово «джони») → Task 7 ✅
- Recognizer (Whisper на GPU) → Task 6 ✅
- Router (шаблоны из commands.yaml) → Task 2 ✅
- Actions (launch/open_url/system) → Task 3 ✅
- ClaudeBrain (`claude -p`) → Task 4 ✅
- Speaker (voice/beep/off) → Task 5 ✅
- Config (YAML) → Task 1 ✅
- Orchestrator (главный цикл) → Task 8 ✅
- Обработка ошибок (пусто/не найдено/claude недоступен) → покрыто в execute (Task 3) и handle_command (Task 8) ✅
- Действия v1 все четыре типа → Tasks 2/3/4 ✅

**Отклонение от спеки (осознанное):** TTS в v1 — pyttsx3 вместо Silero (лёгкий, без `torch`); интерфейс `Speaker.say` неизменен, замена на Silero тривиальна. Отмечено в Task 5.

**Плейсхолдеры:** отсутствуют — весь код приведён целиком.

**Согласованность типов:** `RoutedAction(action, argument)`, `ActionResult(ok, message)`, `ClaudeResult(routed, reply)`, `Config/Settings/CommandRule` — имена и сигнатуры совпадают во всех задачах.
