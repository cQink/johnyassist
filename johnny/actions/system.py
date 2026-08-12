"""Системные действия: громкость, lock, sleep, медиаклавиши, скриншот и т.п."""

import logging
import re
import subprocess
import threading

from .registry import ActionResult, registry

logger = logging.getLogger(__name__)


# ── Громкость (pycaw) ───────────────────────────────────────────────────────

def _volume_endpoint():
    from pycaw.pycaw import AudioUtilities

    return AudioUtilities.GetSpeakers().EndpointVolume


def _set_scalar(value: float) -> None:
    _volume_endpoint().SetMasterVolumeLevelScalar(min(1.0, max(0.0, value)), None)


def _get_scalar() -> float:
    return _volume_endpoint().GetMasterVolumeLevelScalar()


_duck_active = False
_duck_lock = threading.Lock()


def duck_pulse(amount: float = 0.3, duration: float = 1.5) -> None:
    """Короткое приглушение громкости — сигнал «услышал имя», ещё до того как
    понял, есть ли команда (см. controller.run_one_cycle, живая просьба
    2026-08-04: пользователь хочет ЗАМЕТНЫЙ сигнал, чтобы самому на слух
    оценить частоту ложных срабатываний Vosk на слово «Джони»).

    Приглушает СРАЗУ и возвращается, не дожидаясь возврата громкости —
    иначе цикл прослушивания подвисал бы на `duration` секунд на каждое
    подтверждение имени. Возврат к исходному уровню — таймером, из фонового
    потока. Ошибки pycaw (нет устройства вывода, COM-сбой) не должны ронять
    цикл прослушивания — глушатся здесь же.

    ЖИВОЙ БАГ (2026-08-05, «сам становится тише»): вызов ДО того, как
    предыдущий восстановил громкость (например, два процесса Джони
    одновременно услышали одно и то же имя — см. память), читал уже
    приглушённую громкость как «исходную» и приглушал её ЕЩЁ раз; оба
    таймера восстановления откатывали каждый к СВОЕЙ (уже сниженной) базе —
    итог ратчетом уходил вниз и не восстанавливался полностью. Пока
    приглушение ещё активно, повторный вызов — no-op: сигнал «услышал имя»
    уже подан, дублировать незачем.
    """
    global _duck_active
    with _duck_lock:
        if _duck_active:
            return
        try:
            original = _get_scalar()
            _set_scalar(original - amount)
        except Exception:
            logger.exception("Не смог приглушить громкость (сигнал «услышал имя»)")
            return
        _duck_active = True

    def _restore() -> None:
        global _duck_active
        try:
            _set_scalar(original)
        except Exception:
            logger.exception("Не смог вернуть громкость после сигнала «услышал имя»")
        finally:
            with _duck_lock:
                _duck_active = False

    timer = threading.Timer(duration, _restore)
    timer.daemon = True
    timer.start()


def _volume_step(direction: int) -> None:
    _set_scalar(_get_scalar() + direction * 0.1)


def _mute() -> None:
    _volume_endpoint().SetMute(1, None)


def parse_volume(text: str):
    """«5»->50, «2»->20 (цифра ×10), «50»->50, «80»->80. None, если нет числа."""
    match = re.search(r"\d+", text)
    if not match:
        return None
    n = int(match.group())
    return n * 10 if n <= 10 else min(100, n)


@registry.register("set_volume")
def set_volume(argument: str, ctx: dict) -> ActionResult:
    percent = parse_volume(argument)
    if percent is None:
        return ActionResult(False, "Не понял громкость")
    _set_scalar(percent / 100.0)
    return ActionResult(True, "Готово")


@registry.register("volume_delta")
def volume_delta(argument: str, ctx: dict) -> ActionResult:
    """argument вида «+{текст}» или «-{текст}» с числом процентов."""
    match = re.search(r"\d+", argument)
    if not match:
        return ActionResult(False, "Не понял громкость")
    sign = -1 if argument.strip().startswith("-") else 1
    _set_scalar(_get_scalar() + sign * int(match.group()) / 100.0)
    return ActionResult(True, "Готово")


# ── Системные утилиты ────────────────────────────────────────────────────────

def _lock() -> None:
    subprocess.Popen(["rundll32.exe", "user32.dll,LockWorkStation"])


def _screenshot() -> None:
    from datetime import datetime
    from pathlib import Path

    from PIL import ImageGrab

    folder = Path.home() / "Pictures" / "Johnny"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"screenshot-{datetime.now():%Y%m%d-%H%M%S}.png"
    ImageGrab.grab(all_screens=True).save(path)


def _minimize_all() -> None:
    import win32com.client

    win32com.client.Dispatch("Shell.Application").MinimizeAll()


def _show_desktop() -> None:
    import win32com.client

    win32com.client.Dispatch("Shell.Application").ToggleDesktop()


def _sleep() -> None:
    subprocess.Popen(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"])


def _media_key(vk: int) -> None:
    """Нажать системную медиа-клавишу (работает и в Spotify, и в YouTube)."""
    import ctypes

    keyeventf_keyup = 0x0002
    ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
    ctypes.windll.user32.keybd_event(vk, 0, keyeventf_keyup, 0)


SYSTEM_HANDLERS = {
    "volume_up": lambda: _volume_step(+1),
    "volume_down": lambda: _volume_step(-1),
    "mute": _mute,
    "lock": _lock,
    "play_pause": lambda: _media_key(0xB3),   # VK_MEDIA_PLAY_PAUSE
    "next_track": lambda: _media_key(0xB0),    # VK_MEDIA_NEXT_TRACK
    "prev_track": lambda: _media_key(0xB1),    # VK_MEDIA_PREV_TRACK
    "screenshot": _screenshot,
    "minimize_all": _minimize_all,
    "show_desktop": _show_desktop,
    "sleep": _sleep,
    # Выключение/перезагрузка с задержкой 20с — можно отменить голосом.
    "shutdown": lambda: subprocess.Popen(["shutdown", "/s", "/t", "20"]),
    "restart": lambda: subprocess.Popen(["shutdown", "/r", "/t", "20"]),
    "cancel_shutdown": lambda: subprocess.Popen(["shutdown", "/a"]),
}


@registry.register("system")
def system_command(argument: str, ctx: dict) -> ActionResult:
    handler = SYSTEM_HANDLERS.get(argument)
    if handler is None:
        return ActionResult(False, "Не знаю такой системной команды")
    handler()
    return ActionResult(True, "Готово")
