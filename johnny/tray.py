import logging
import os
import sys
import threading
import time
from pathlib import Path

import pystray
from PIL import Image, ImageDraw

from . import autostart, game_watch, panel_tools, single_instance
from .config import load_config
from .controller import AssistantController
from .speaker import make_speaker

_ROOT = Path(__file__).resolve().parent.parent
_LOG = _ROOT / "johnny.log"
_HISTORY = _ROOT / "history.log"


def _icon_image(active: bool) -> Image.Image:
    """Зелёный кружок — слушает, серый — пауза."""
    color = (60, 200, 90) if active else (130, 130, 130)
    img = Image.new("RGB", (64, 64), (25, 25, 28))
    draw = ImageDraw.Draw(img)
    draw.ellipse((12, 12, 52, 52), fill=color)
    return img


def _setup_logging() -> None:
    # Под pythonw консоли нет — направляем и логи, и случайные print в файл.
    logging.basicConfig(
        filename=str(_LOG),
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        encoding="utf-8",
    )
    for noisy in ("comtypes", "httpx", "huggingface_hub", "faster_whisper"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    log_file = open(_LOG, "a", encoding="utf-8", buffering=1)
    sys.stdout = log_file
    sys.stderr = log_file


def main() -> None:
    _setup_logging()
    force = os.environ.get("JOHNNY_FORCE_SINGLE_INSTANCE") == "1"
    if not single_instance.acquire(force=force):
        # Живой баг (2026-08-05): автозапуск и ручной запуск сработали
        # одновременно — два процесса слушали один микрофон и дублировали
        # каждую реакцию на «Джони» (звук внахлёст, двойное приглушение
        # громкости и т.п.). Второй процесс должен тихо выйти, не трогая
        # микрофон/модели, до того как нанесёт вред.
        logging.info("Джони уже запущен другим процессом — выхожу")
        return
    logging.info("=== Джони запускается ===")

    # ДИАГНОСТИКА (2026-08-05, живая жалоба «запускается дольше минуты, может
    # дольше двух»): каждый тяжёлый шаг замеряется отдельно — до сих пор в
    # логе было видно только суммарное время «=== запускается ===» →
    # «Модели загружены» (стабильно ~3 минуты по истории johnny.log), без
    # разбивки, где именно оно уходит.
    t0 = time.monotonic()

    config = load_config(str(_ROOT / "config"))
    speaker = make_speaker(config.settings, config.secrets)
    controller = AssistantController(config, speaker)
    logging.info("Тайминг запуска: конфиг+speaker +%.1fс", time.monotonic() - t0)

    # Тяжёлые модули грузим здесь (микрофон/модели).
    from .audio import Microphone
    from .listener import Listener
    from .recognizer import Recognizer, build_vocabulary

    vocabulary = build_vocabulary(config.apps, config.channels, config.commands, config.people)
    logging.info("Тайминг запуска: словарь Whisper +%.1fс", time.monotonic() - t0)

    recognizer = Recognizer(
        config.settings.whisper_model, config.settings.whisper_device, vocabulary
    )
    logging.info("Тайминг запуска: faster-whisper (%s, %s) +%.1fс", config.settings.whisper_model, config.settings.whisper_device, time.monotonic() - t0)

    listener = Listener(config.settings.wake_word, str(_ROOT / config.settings.vosk_model_path))
    logging.info("Тайминг запуска: vosk (%s) +%.1fс", config.settings.vosk_model_path, time.monotonic() - t0)

    mic = Microphone().open()
    logging.info("Тайминг запуска: микрофон +%.1fс", time.monotonic() - t0)

    # Сторож видеопамяти: пока идёт игра, Whisper уходит на маленькую модель и
    # возвращает карте ~1.5 ГБ. Выключен, если whisper_model_gaming пуст.
    guard = game_watch.Guard(recognizer, config.settings)
    guard.start()

    listener_available = getattr(listener, "available", False)
    recognizer_available = getattr(recognizer, "available", False)
    # Один и тот же разбор состояния, что показывает панель — иначе лог и UI
    # расходятся формулировками, и по логу не понять, что человек видел.
    startup = panel_tools.status(listener=listener_available, recognizer=recognizer_available)
    logging.log(
        {"ok": logging.INFO, "warn": logging.WARNING}.get(startup.tone, logging.ERROR),
        "Джони запущен: %s",
        startup.text,
    )

    def loop() -> None:
        # Микрофон закрывает поток цикла, а не quit_app: controller.stop()
        # выводит run из цикла, и этот finally отрабатывает сам.
        try:
            controller.run(listener, recognizer, mic)
        except Exception:
            logging.exception("Цикл прослушивания упал")
        finally:
            mic.close()

    threading.Thread(target=loop, daemon=True).start()

    stop_event = threading.Event()
    icon = pystray.Icon(
        "johnny",
        _icon_image(not controller.paused and (listener_available or recognizer_available)),
        "Джони",
    )

    def _start_tray() -> None:
        try:
            icon.visible = True
            if hasattr(icon, "run_detached"):
                logging.info("Запускаю трей в режиме run_detached")
                icon.run_detached()
                stop_event.wait()
            else:
                logging.info("Запускаю трей в режиме run")
                icon.run()
        except Exception:
            logging.exception("Не удалось запустить трей-иконку")

    threading.Thread(target=_start_tray, daemon=True).start()

    def toggle_pause(_icon, _item) -> None:
        controller.resume() if controller.paused else controller.pause()
        icon.icon = _icon_image(not controller.paused)
        logging.info("Пауза: %s", controller.paused)

    def toggle_autostart(_icon, _item) -> None:
        if autostart.is_installed():
            autostart.uninstall()
        else:
            pythonw = str(Path(sys.executable).with_name("pythonw.exe"))
            autostart.install(pythonw, str(_ROOT / "johnny_tray.pyw"), str(_ROOT))

    def open_history(_icon, _item) -> None:
        from . import history_viewer

        if not _HISTORY.exists():
            _HISTORY.write_text("", encoding="utf-8")
        history_viewer.open_or_focus(_HISTORY)

    def open_log(_icon, _item) -> None:
        os.startfile(str(_LOG))  # type: ignore[attr-defined]

    def quit_app(_icon, _item) -> None:
        logging.info("Выход по команде из трея")
        guard.stop()
        controller.stop()
        stop_event.set()
        icon.stop()

    def restart_app(_icon, _item) -> None:
        logging.info("Перезапуск по команде из трея")
        controller.stop()
        stop_event.set()
        icon.stop()
        os.startfile(str(_ROOT / "johnny_tray.pyw"))

    # Каждый вызов открывает своё окно со своим mainloop в своём потоке, поэтому
    # держим ссылку и не плодим копии: два окна показывали бы один статус, а
    # «Сохранить» в них перетирало бы настройки друг друга.
    panel_thread: list[threading.Thread] = []

    def open_control_panel(_icon=None, _item=None) -> None:
        from . import panel

        if panel_thread and panel_thread[0].is_alive():
            logging.info("Панель уже открыта")
            return

        def on_save(new_settings):
            logging.info("Настройки сохранены из панели")

        def engines() -> tuple[bool, bool]:
            # Спрашиваем каждый раз, а не отдаём снимок с момента запуска:
            # available у Listener/Recognizer отражает живое состояние.
            return (
                bool(getattr(listener, "available", False)),
                bool(getattr(recognizer, "available", False)),
            )

        def run_panel() -> None:
            try:
                panel.open_panel(
                    on_save=on_save,
                    on_toggle_pause=lambda: toggle_pause(None, None),
                    on_restart=lambda: restart_app(None, None),
                    on_quit=lambda: quit_app(None, None),
                    on_open_history=lambda: open_history(None, None),
                    on_open_log=lambda: open_log(None, None),
                    on_toggle_autostart=lambda: toggle_autostart(None, None),
                    get_last_command=lambda: controller.last_command or "—",
                    get_paused=lambda: controller.paused,
                    get_engines=engines,
                )
            except Exception:
                logging.exception("Панель упала")

        thread = threading.Thread(target=run_panel, daemon=True)
        panel_thread[:] = [thread]
        thread.start()

    icon.menu = pystray.Menu(
        pystray.MenuItem("Открыть интерфейс", open_control_panel),
        pystray.MenuItem("Выход", quit_app),
    )
    # Панель — это и есть статус: показываем сразу, чтобы после трёхминутной
    # загрузки моделей было видно, что Джони жив, без охоты за иконкой в трее.
    open_control_panel()
    icon.run()

