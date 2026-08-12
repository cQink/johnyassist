"""Точка входа без окна консоли (запуск через pythonw). Джони в трее."""
import sys

from johnny.tray import main

if __name__ == "__main__":
    force = "--force" in sys.argv
    if force:
        import os
        os.environ["JOHNNY_FORCE_SINGLE_INSTANCE"] = "1"
    main()
