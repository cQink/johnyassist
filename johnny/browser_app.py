"""Регистрация «Chrome (Джони)» как браузера Windows.

Отдельный профиль Джони нужен вынужденно: с Chrome 136 отладочный порт
запрещён на профиле по умолчанию. Значит основным браузером должен стать
профиль Джони — иначе ссылки из других программ открывают второй Chrome,
до которого Джони не дотягивается.

Саму ассоциацию Windows защищает хешем, программно её не переставить. Этот
модуль только РЕГИСТРИРУЕТ приложение, чтобы оно появилось в списке
браузеров; выбирает человек руками, один раз.
"""

import winreg
from pathlib import Path

from . import browser

APP_NAME = "Chrome (Джони)"
PROG_ID = "JohnnyChromeHTML"

_CLIENT_KEY = rf"Software\Clients\StartMenuInternet\{APP_NAME}"
_CAPABILITIES = rf"{_CLIENT_KEY}\Capabilities"
_PROGID_KEY = rf"Software\Classes\{PROG_ID}"
_REGISTERED = r"Software\RegisteredApplications"


def launch_command(chrome: str, profile: str, port: int) -> str:
    """Строка запуска для реестра. %1 — ссылка, которую подставит Windows."""
    return f'"{chrome}" --user-data-dir="{profile}" --remote-debugging-port={port} -- "%1"'


def _flags() -> str:
    """Флаги без %1 — для ярлыка и для записи запуска без ссылки."""
    return f'--user-data-dir="{browser._PROFILE}" --remote-debugging-port={browser._PORT}'


def _set(path: str, name: str, value: str) -> None:
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, path) as key:
        winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)


def _delete_tree(path: str) -> bool:
    """Удалить ключ со всеми подключами. False — его и не было."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
            while True:
                try:
                    child = winreg.EnumKey(key, 0)
                except OSError:
                    break
                _delete_tree(f"{path}\\{child}")
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
        return True
    except FileNotFoundError:
        return False


def install() -> str:
    """Зарегистрировать приложение. Возвращает имя, видимое в настройках."""
    chrome = browser._chrome_exe()
    command = launch_command(chrome, str(browser._PROFILE), browser._PORT)

    _set(_PROGID_KEY, "", "Джони: ссылка в Chrome")
    _set(rf"{_PROGID_KEY}\DefaultIcon", "", f"{chrome},0")
    _set(rf"{_PROGID_KEY}\shell\open\command", "", command)

    _set(_CLIENT_KEY, "", APP_NAME)
    _set(rf"{_CLIENT_KEY}\DefaultIcon", "", f"{chrome},0")
    _set(rf"{_CLIENT_KEY}\shell\open\command", "", f'"{chrome}" {_flags()}')
    _set(_CAPABILITIES, "ApplicationName", APP_NAME)
    _set(_CAPABILITIES, "ApplicationDescription", "Chrome на профиле Джони")
    _set(_CAPABILITIES, "ApplicationIcon", f"{chrome},0")
    for protocol in ("http", "https"):
        _set(rf"{_CAPABILITIES}\URLAssociations", protocol, PROG_ID)
    for suffix in (".htm", ".html"):
        _set(rf"{_CAPABILITIES}\FileAssociations", suffix, PROG_ID)

    _set(_REGISTERED, APP_NAME, _CAPABILITIES)
    return APP_NAME


def uninstall() -> bool:
    """Снять регистрацию. False — её и не было."""
    removed = _delete_tree(_CLIENT_KEY)
    removed = _delete_tree(_PROGID_KEY) or removed
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _REGISTERED, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.DeleteValue(key, APP_NAME)
            removed = True
    except FileNotFoundError:
        pass
    return removed


def make_shortcut() -> Path:
    """Ярлык на рабочем столе — его человек закрепит на панели задач."""
    import win32com.client  # из pywin32

    chrome = browser._chrome_exe()
    path = Path.home() / "Desktop" / f"{APP_NAME}.lnk"
    shell = win32com.client.Dispatch("WScript.Shell")
    shortcut = shell.CreateShortcut(str(path))
    shortcut.TargetPath = chrome
    shortcut.Arguments = _flags()
    shortcut.IconLocation = chrome
    shortcut.Save()
    return path
