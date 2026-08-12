import os
import subprocess
import sys
from pathlib import Path

from johnny.app import run


def _prompt_python_environment() -> None:
    if "--no-env-prompt" in sys.argv or os.environ.get("JOHNNY_NO_ENV_PROMPT") == "1":
        return

    current = Path(sys.executable)
    print("Выберите Python-среду для запуска Джони.")
    print(f"1) Текущая среда: {current}")
    print("2) Указать путь к другому python.exe")
    print("3) Выйти")

    while True:
        choice = input("Ваш выбор [1-3]: ").strip()
        if choice == "" or choice == "1":
            return

        if choice == "2":
            path = input("Путь к python.exe: ").strip('" ').strip()
            if not path:
                print("Путь не указан. Повторите ввод.")
                continue
            interpreter = Path(path)
            if not interpreter.exists():
                print("Файл не найден. Повторите ввод.")
                continue
            if interpreter.name.lower() not in {"python.exe", "python3.exe", "python"}:
                print("Укажите корректный python-исполняемый файл.")
                continue
            subprocess.run(
                [str(interpreter.resolve()), str(Path(__file__).resolve()), "--no-env-prompt"]
                + [arg for arg in sys.argv[1:] if arg != "--no-env-prompt"]
            )
            sys.exit(0)

        if choice == "3":
            sys.exit(0)

        print("Неверный выбор. Введите 1, 2 или 3.")


if __name__ == "__main__":
    _prompt_python_environment()
    run()
