"""Захват картинки для команд зрения: экран, активное окно, веб-камера.

Зачем модуль вообще. Команды «прочитай текст» и «что на картинке» уже работают,
но требуют, чтобы человек САМ назвал путь к файлу — голосом или через «Обзор».
Из-за этого «прочитай, что на экране» не работало вовсе: назвать путь к тому,
чего ещё нет на диске, нельзя. Здесь снимок появляется, а дальше идёт по тому
же пути, что и любой файл.

Почему Pillow и OpenCV, а не mss/pyautogui: оба уже стоят в проекте (Pillow —
ради иконки в трее, cv2 приехал с insightface). Новая зависимость ради снимка
экрана не нужна.

Приватность — главное здесь, и она устроена в два слоя:

  - Снимок экрана это не «картинка», а всё, что на нём открыто: переписка,
    пароли в почте, чужие данные. Отправку наружу по-прежнему решает согласие у
    коннектора (`requires_consent`), и этот модуль его НЕ обходит — он лишь
    делает файл.
  - Сам файл тоже опасен, поэтому он живёт в `models/` (каталог целиком в
    .gitignore) и удаляется сразу после ответа — см. `discard()`. Держать на
    диске снимок чужой переписки «на случай отладки» — цена, которой этот
    случай не стоит.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# Рядом с кешем коннекторов: тот же models/, целиком закрытый .gitignore. Своё
# подкаталог — чтобы «удалить все снимки» не значило «стереть кеш».
CAPTURE_DIR = Path("models") / "captures"

# Сколько ждать камеру. Первый кадр с веб-камеры почти всегда чёрный или
# пересвеченный: матрице нужно время на экспозицию, поэтому кадры сначала
# прогреваются, и это не «магическое число», а свойство железа.
CAMERA_WARMUP_FRAMES = 5

# Сколько ждать человека с выделением области. Оверлей висит поверх всех окон,
# и брошенный он выглядит как зависший экран — поэтому предел нужен.
REGION_TIMEOUT_SECONDS = 60.0


def _new_path(kind: str) -> Path:
    """Путь для нового снимка. Имя со временем — чтобы два подряд не затирали
    друг друга, если предыдущий ещё не удалён."""
    CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    return CAPTURE_DIR / f"{kind}-{stamp}.png"


def screen() -> tuple[str, str]:
    """Снимок всех экранов. Возвращает (путь, причина отказа).

    `all_screens=True` намеренно: у владельца два монитора, и снимок только
    главного молча терял бы половину рабочего стола — человек сказал «что на
    экране» про тот экран, куда смотрит, а какой из них главный, он не помнит.
    """
    try:
        from PIL import ImageGrab
    except ImportError:
        return "", "нет пакета Pillow"
    try:
        image = ImageGrab.grab(all_screens=True)
        path = _new_path("screen")
        image.save(path)
        return str(path), ""
    except Exception as error:
        logger.info("Снимок экрана не получился: %s", error)
        return "", f"не удалось снять экран ({error})"


def window() -> tuple[str, str]:
    """Снимок активного окна. Возвращает (путь, причина отказа).

    Нужен отдельно от `screen()`: на снимке всего экрана мелкий шрифт в одном
    окне читается хуже, а OCR тем точнее, чем меньше лишнего. Плюс приватность —
    уедет только то окно, о котором спросили, а не весь рабочий стол.
    """
    try:
        import win32gui
        from PIL import ImageGrab
    except ImportError:
        return "", "нет пакетов pywin32/Pillow"
    try:
        handle = win32gui.GetForegroundWindow()
        if not handle:
            return "", "не вижу активного окна"
        # Свёрнутое окно спрашиваем у Windows, а не считаем по размеру: оно
        # отдаёт прямоугольник (-32000,-32000,-31840,-31972) — это 160×28,
        # размер совершенно правдоподобный, и проверка «слишком маленькое»
        # его пропускает. Мимо экрана ImageGrab снимет пустоту, а OCR ответит
        # «текста нет» — враньё: текст есть, просто окно свёрнуто.
        if win32gui.IsIconic(handle):
            return "", "активное окно свёрнуто"
        left, top, right, bottom = win32gui.GetWindowRect(handle)
        if right - left < 2 or bottom - top < 2:
            # Вырожденный прямоугольник у не свёрнутого окна: редкость, но
            # снимать нечего и здесь.
            return "", "у активного окна нет размера"
        image = ImageGrab.grab(bbox=(left, top, right, bottom), all_screens=True)
        path = _new_path("window")
        image.save(path)
        return str(path), ""
    except Exception as error:
        logger.info("Снимок окна не получился: %s", error)
        return "", f"не удалось снять окно ({error})"


def region(timeout_seconds: float = REGION_TIMEOUT_SECONDS) -> tuple[str, str]:
    """Снимок выделенной мышью области. Возвращает (путь, причина отказа).

    Порядок здесь единственно возможный: сначала снимаем экран, и только потом
    показываем рамку выделения. Наоборот — полупрозрачная плёнка оверлея попала
    бы в кадр, и OCR читал бы текст сквозь неё.

    Выделение живёт в ОТДЕЛЬНОМ ПРОЦЕССЕ (johnny/region_select.py): панель
    держит свой Tk-цикл в своём потоке, а тулкиты выполняются в фоновом, и
    создание Tk-объектов из чужого потока вешает панель молча, без исключения.
    """
    try:
        from PIL import Image
    except ImportError:
        return "", "нет пакета Pillow"
    # Экран снимаем первым и сами: region_select ничего не снимает, он только
    # спрашивает координаты.
    full, refusal = screen()
    if not full:
        return "", refusal
    try:
        box, why = _ask_region(timeout_seconds)
        if box is None:
            return "", why
        image = Image.open(full)
        # Координаты приходят экранные, а картинка начинается от левого верхнего
        # угла ВИРТУАЛЬНОГО рабочего стола — на втором мониторе слева от
        # главного он начинается в минусе, и без сдвига обрезка уедет.
        origin_x, origin_y = _virtual_origin()
        left, top, right, bottom = box
        crop = image.crop(
            (left - origin_x, top - origin_y, right - origin_x, bottom - origin_y)
        )
        path = _new_path("region")
        crop.save(path)
        return str(path), ""
    except Exception as error:
        logger.info("Снимок области не получился: %s", error)
        return "", f"не удалось снять область ({error})"
    finally:
        # Полный экран был промежуточным: на нём весь рабочий стол, а человек
        # выделил кусок именно потому, что остальное отправлять не хотел.
        discard(full)


def _virtual_origin() -> tuple[int, int]:
    """Левый верхний угол виртуального рабочего стола. (0, 0) — если не узнать.

    Отрицателен, когда второй монитор стоит слева от главного: Windows считает
    координаты от главного, а ImageGrab(all_screens=True) отдаёт картинку от
    угла общей области.
    """
    try:
        import win32api
        import win32con

        return (
            win32api.GetSystemMetrics(win32con.SM_XVIRTUALSCREEN),
            win32api.GetSystemMetrics(win32con.SM_YVIRTUALSCREEN),
        )
    except Exception:
        return 0, 0


def _ask_region(timeout_seconds: float):
    """Спросить координаты у отдельного процесса. (box, причина отказа)."""
    command = [sys.executable, "-m", "johnny.region_select"]
    try:
        done = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            cwd=str(Path(__file__).resolve().parent.parent),
        )
    except subprocess.TimeoutExpired:
        # Оверлей поверх всех окон: если его бросили, он так и висит поверх
        # экрана. Ограничение по времени тут не роскошь.
        return None, "область так и не выбрали"
    except Exception as error:
        return None, f"не удалось показать выделение ({error})"
    if done.returncode != 0:
        return None, "выделение отменили"
    parts = (done.stdout or "").split()
    if len(parts) != 4:
        logger.info("region_select вернул неожиданное: %r", done.stdout)
        return None, "выделение не получилось"
    try:
        return tuple(int(value) for value in parts), ""
    except ValueError:
        logger.info("region_select вернул не числа: %r", done.stdout)
        return None, "выделение не получилось"


def camera(index: int = 0) -> tuple[str, str]:
    """Кадр с веб-камеры. Возвращает (путь, причина отказа).

    Камеру открываем и закрываем на каждый кадр, а не держим открытой: занятая
    камера — это горящий индикатор и «устройство используется» в Zoom. Для
    ассистента, который снимает раз в минуту по просьбе, держать её открытой
    значило бы мешать всему остальному.
    """
    try:
        import cv2
    except ImportError:
        return "", "нет пакета opencv-python"
    device = None
    try:
        device = cv2.VideoCapture(index)
        if not device.isOpened():
            return "", "камера не открывается — занята другой программой или её нет"
        frame = None
        for _ in range(CAMERA_WARMUP_FRAMES):
            ok, frame = device.read()
            if not ok:
                return "", "камера не отдала кадр"
        if frame is None:
            return "", "камера не отдала кадр"
        path = _new_path("camera")
        # cv2.imwrite не понимает не-ASCII в пути, а проект лежит в «johny
        # assist» и у людей бывает кириллица в имени пользователя. Пишем через
        # imencode + сами байты — путь тогда обрабатывает Python, а не OpenCV.
        ok, buffer = cv2.imencode(".png", frame)
        if not ok:
            return "", "кадр не удалось закодировать"
        path.write_bytes(buffer.tobytes())
        return str(path), ""
    except Exception as error:
        logger.info("Кадр с камеры не получился: %s", error)
        return "", f"не удалось снять кадр ({error})"
    finally:
        if device is not None:
            device.release()


def discard(path: str) -> None:
    """Удалить снимок. Молча, и это намеренно.

    Зовётся после ответа: на снимке могла быть переписка или документ, и
    оставлять его на диске нельзя. Ошибку удаления человеку не докладываем — он
    просил прочитать текст, а не следить за файлами; в лог она всё же идёт,
    иначе каталог тихо распухнет и никто не узнает.
    """
    if not path:
        return
    try:
        Path(path).unlink(missing_ok=True)
    except OSError as error:
        logger.info("Снимок %s не удалился: %s", path, error)
