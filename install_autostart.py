"""Включить/выключить автозапуск Джони с Windows.

  python install_autostart.py             # включить
  python install_autostart.py --uninstall # выключить
"""
import sys
from pathlib import Path

from johnny import autostart

ROOT = Path(__file__).resolve().parent


def main() -> None:
    if "--uninstall" in sys.argv:
        print("Автозапуск убран" if autostart.uninstall() else "Автозапуск и так не стоял")
        return
    pythonw = str(Path(sys.executable).with_name("pythonw.exe"))
    path = autostart.install(pythonw, str(ROOT / "johnny_tray.pyw"), str(ROOT))
    print(f"Автозапуск включён: {path}")


if __name__ == "__main__":
    main()
