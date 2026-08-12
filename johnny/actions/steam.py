"""Действия со Steam: переключение аккаунта."""

import os
import shlex
import subprocess
import time

from .registry import ActionResult, registry

_STEAM_RESTART_DELAY = 4.0


def _split_command(target: str) -> list[str]:
    return [part.strip('"') for part in shlex.split(target, posix=False)]


@registry.register("steam_login")
def action_steam_login(argument: str, ctx: dict) -> ActionResult:
    """Переключить аккаунт Steam.

    На уже запущенном Steam `-login` лишь переводит фокус, поэтому сначала
    закрываем клиент (`-shutdown`), ждём, затем входим под нужным аккаунтом.
    Путь к steam.exe берётся из apps["стим"]. False, если Steam не настроен.
    """
    apps = ctx.get("apps", {})
    steam = apps.get("стим")
    if not steam:
        return ActionResult(False, "Не нашёл Steam")
    exe = _split_command(steam)[0]
    cwd = os.path.dirname(exe) or None
    subprocess.Popen([exe, "-shutdown"], cwd=cwd)
    time.sleep(_STEAM_RESTART_DELAY)
    subprocess.Popen([exe, "-login", argument], cwd=cwd)
    return ActionResult(True, "Меняю аккаунт Steam")
