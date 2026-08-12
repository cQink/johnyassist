"""Запуск/фокус приложений, перенос окон между мониторами."""

import os
import shlex
import subprocess

from .registry import ActionResult, registry
from .browser import open_url

_SCHEMES = ("http://", "https://", "steam://")
# Сколько ждать появления окна программы перед переносом на монитор.
_WINDOW_APPEAR_DELAY = 2.5


def _split_command(target: str) -> list[str]:
    """Разобрать строку запуска в список аргументов.

    posix=False сохраняет обратные слэши Windows; кавычки затем снимаем сами,
    чтобы путь с пробелами стал одним корректным аргументом.
    """
    return [part.strip('"') for part in shlex.split(target, posix=False)]


def _start(target: str) -> None:
    """Запуск цели: протокол/URL — через ОС, иначе — как процесс.

    Программа запускается из своей папки (cwd = папка exe), иначе некоторые
    приложения (например OBS) не находят рядом свои файлы.
    """
    if target.startswith(_SCHEMES):
        os.startfile(target)  # type: ignore[attr-defined]  # Windows-only
        return
    parts = _split_command(target)
    cwd = os.path.dirname(parts[0]) or None
    subprocess.Popen(parts, cwd=cwd)


# ── Зарегистрированные действия ──────────────────────────────────────────────

@registry.register("launch_app")
def action_launch_app(argument: str, ctx: dict) -> ActionResult:
    apps = ctx.get("apps", {})
    target = apps.get(argument.lower().strip())
    if target is None:
        return ActionResult(False, "Не знаю такой программы")
    _start(target)
    return ActionResult(True, f"Запускаю {argument}")


@registry.register("app_focus")
def action_app_focus(argument: str, ctx: dict) -> ActionResult:
    """Вывести окно уже запущенной программы поверх остальных.

    Если окна нет (программа не запущена, или её единственный процесс —
    launcher без окна, как Update.exe у Discord) — запускаем как обычно:
    голосом «не нашёл окно» и «не запущена» неотличимы, а вторая копия
    браузера/OBS не появится (target — обычные exe, не steam:// протокол).
    """
    apps = ctx.get("apps", {})
    target = apps.get(argument.lower().strip())
    if target is None:
        return ActionResult(False, "Не знаю такой программы")
    if not target.startswith(_SCHEMES):
        from .. import discord_ui, windows  # discord_ui.focus — общий приём для hwnd, Discord тут ни при чём

        exe = os.path.basename(_split_command(target)[0])
        hwnd = windows.find_window_by_exe(exe)
        if hwnd is not None:
            discord_ui.focus(hwnd)
            return ActionResult(True, "Готово")
    # Окно не найдено → запускаем.
    _start(target)
    return ActionResult(True, f"Запускаю {argument}")


@registry.register("move_window")
def action_move_window(argument: str, ctx: dict) -> ActionResult:
    """Перенести активное окно на монитор с номером argument (1-based)."""
    from .. import windows

    if windows.move_foreground_to_monitor(int(argument)):
        return ActionResult(True, "Готово")
    return ActionResult(False, "Не смог перенести окно")


@registry.register("launch_on_monitor")
def action_launch_on_monitor(argument: str, ctx: dict) -> ActionResult:
    """argument = «имя|номер»: запустить программу и перенести ПОЯВИВШЕЕСЯ окно.

    Двигаем именно новое окно (а не переднее), иначе можно утащить рабочее окно
    пользователя. Если новое окно не появилось (напр. добавилась вкладка в уже
    открытый браузер) — ничего не двигаем.
    """
    apps = ctx.get("apps", {})
    name, _, idx = argument.rpartition("|")
    from .. import windows

    before = windows.visible_windows()
    result = action_launch_app(name, {"apps": apps})
    if not result.ok:
        return ActionResult(False, "Не знаю такой программы")
    hwnd = windows.wait_for_new_window(before, timeout=_WINDOW_APPEAR_DELAY + 4.0)
    if hwnd:
        windows.move_window_to_monitor(hwnd, windows.list_monitors(), int(idx))
    return ActionResult(True, "Открываю")


@registry.register("open_url_on_monitor")
def action_open_url_on_monitor(argument: str, ctx: dict) -> ActionResult:
    """argument = «url|номер»: открыть ссылку новым окном Chrome на мониторе.

    Chrome игнорирует --window-position, если уже запущен, поэтому форсируем
    новое окно (--new-window) и переносим его сами (win32) — работает всегда.
    Если Chrome не задан — обычный open_url в браузере по умолчанию.
    """
    apps = ctx.get("apps", {})
    url, _, idx = argument.rpartition("|")
    full = url if url.startswith(_SCHEMES) else "https://" + url

    from .. import windows

    chrome = apps.get("хром") or apps.get("chrome")
    if not chrome:
        open_url(url)
        return ActionResult(True, "Открываю")

    exe = _split_command(chrome)[0]
    before = windows.visible_windows()
    subprocess.Popen([exe, "--new-window", full], cwd=os.path.dirname(exe) or None)
    hwnd = windows.wait_for_new_window(before, timeout=10.0)
    if hwnd:
        windows.move_window_to_monitor(hwnd, windows.list_monitors(), int(idx))
    return ActionResult(True, "Открываю")
