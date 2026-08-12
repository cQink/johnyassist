import os
from pathlib import Path

APP_NAME = "Джони"


def _startup_dir() -> Path:
    return (
        Path(os.environ["APPDATA"])
        / "Microsoft"
        / "Windows"
        / "Start Menu"
        / "Programs"
        / "Startup"
    )


def shortcut_path() -> Path:
    return _startup_dir() / f"{APP_NAME}.lnk"


def is_installed() -> bool:
    return shortcut_path().exists()


def install(pythonw: str, script: str, workdir: str) -> Path:
    """Создать ярлык автозапуска: pythonw johnny_tray.pyw (без окна)."""
    import win32com.client  # из pywin32

    shell = win32com.client.Dispatch("WScript.Shell")
    shortcut = shell.CreateShortcut(str(shortcut_path()))
    shortcut.TargetPath = pythonw
    shortcut.Arguments = f'"{script}"'
    shortcut.WorkingDirectory = workdir
    shortcut.IconLocation = pythonw
    shortcut.Save()
    return shortcut_path()


def uninstall() -> bool:
    path = shortcut_path()
    if path.exists():
        path.unlink()
        return True
    return False
