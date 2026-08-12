import os
import time

import win32api
import win32con
import win32gui
import win32process


def visible_windows() -> set:
    """Множество видимых окон верхнего уровня с заголовком."""
    found = set()

    def _cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd) and win32gui.GetWindowText(hwnd):
            found.add(hwnd)

    win32gui.EnumWindows(_cb, None)
    return found


def wait_for_new_window(before: set, timeout: float = 6.0, poll: float = 0.3):
    """Дождаться окна, которого не было в before. None, если не появилось."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        appeared = visible_windows() - before
        if appeared:
            return next(iter(appeared))
        time.sleep(poll)
    return None


def list_monitors() -> list[tuple[int, int, int, int]]:
    """Прямоугольники (left, top, right, bottom) мониторов, слева направо."""
    rects = [rect for _hmon, _hdc, rect in win32api.EnumDisplayMonitors()]
    rects.sort(key=lambda r: r[0])
    return rects


def pick_monitor(monitors, index):
    """Монитор по 1-based номеру; None, если номера нет."""
    if not monitors or index < 1 or index > len(monitors):
        return None
    return monitors[index - 1]


def move_window_to_monitor(hwnd, monitors, index) -> bool:
    rect = pick_monitor(monitors, index)
    if not hwnd or rect is None:
        return False
    left, top, right, bottom = rect
    width, height = right - left, bottom - top
    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)  # снять «развёрнуто»
    # поставить с отступом внутри целевого монитора, затем развернуть на нём
    win32gui.MoveWindow(hwnd, left + 40, top + 40, width - 200, height - 200, True)
    win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)
    return True


def move_foreground_to_monitor(index) -> bool:
    hwnd = win32gui.GetForegroundWindow()
    return move_window_to_monitor(hwnd, list_monitors(), index)


def find_window_by_exe(exe_name: str):
    """Видимое окно верхнего уровня, чей процесс — exe_name (без учёта пути
    и регистра). None — не нашли, программа, судя по всему, не запущена.

    Процессы, к которым нет доступа (чужой пользователь, защищённый), просто
    пропускаем — падать из-за одного недоступного окна нельзя, там может
    найтись искомое.
    """
    exe_name = exe_name.lower()
    found = []

    def _cb(hwnd, _):
        if not (win32gui.IsWindowVisible(hwnd) and win32gui.GetWindowText(hwnd)):
            return
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            handle = win32api.OpenProcess(
                win32con.PROCESS_QUERY_INFORMATION | win32con.PROCESS_VM_READ, False, pid
            )
            try:
                path = win32process.GetModuleFileNameEx(handle, 0)
            finally:
                win32api.CloseHandle(handle)
            if os.path.basename(path).lower() == exe_name:
                found.append(hwnd)
        except Exception:
            pass

    win32gui.EnumWindows(_cb, None)
    return found[0] if found else None
