"""Сделать профиль Джони основным браузером.

  python install_browser.py             # зарегистрировать
  python install_browser.py --uninstall # убрать
"""
import sys

from johnny import browser_app


def main() -> None:
    if "--uninstall" in sys.argv:
        print("Регистрация убрана" if browser_app.uninstall() else "Её и не было")
        return
    name = browser_app.install()
    path = browser_app.make_shortcut()
    print(f"Зарегистрировано приложение: {name}")
    print(f"Ярлык на рабочем столе: {path}")
    print()
    print("Остался один шаг руками — Windows не даёт выбрать браузер программно:")
    print("  1. Параметры → Приложения → Приложения по умолчанию")
    print(f"  2. Найти «{name}» в списке")
    print("  3. Назначить его для HTTP и HTTPS")
    print()
    print("Потом перетащи ярлык с рабочего стола на панель задач вместо старого Chrome.")
    print("Откатить всё: python install_browser.py --uninstall")


if __name__ == "__main__":
    main()
