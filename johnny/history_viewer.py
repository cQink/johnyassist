"""Живое окно истории команд — вызывается из пункта трея «История команд».

Не Notepad: тот не умеет сам обновляться, закрывать/открывать заново ради
новых строк неудобно (жалоба пользователя, 2026-08-06). Маленькое своё
Tk-окно вместо этого само дочитывает файл каждые полсекунды.
"""

import threading
import tkinter as tk
from pathlib import Path

import win32con
import win32gui

_TITLE = "Джони — история команд"
_POLL_MS = 500


def _read_new_tail(path: Path, last_size: int) -> tuple[str, int]:
    """Хвост файла, добавленный после last_size байт. Файла может не быть
    (история ещё не создана) — тогда хвоста нет, размер 0."""
    if not path.exists():
        return "", 0
    size = path.stat().st_size
    if size <= last_size:
        return "", size
    with open(path, "r", encoding="utf-8") as f:
        f.seek(last_size)
        return f.read(), size


def _run_window(history_path: Path) -> None:
    root = tk.Tk()
    root.title(_TITLE)
    root.geometry("700x400")
    text = tk.Text(root, state="disabled", wrap="word")
    text.pack(fill="both", expand=True)

    def _insert(content: str) -> None:
        text.configure(state="normal")
        text.insert("end", content)
        text.see("end")
        text.configure(state="disabled")

    initial, size = _read_new_tail(history_path, 0)
    state = {"size": size}
    if initial:
        _insert(initial)

    def _poll() -> None:
        content, new_size = _read_new_tail(history_path, state["size"])
        if content:
            _insert(content)
            state["size"] = new_size
        root.after(_POLL_MS, _poll)

    root.after(_POLL_MS, _poll)
    root.mainloop()


def open_or_focus(history_path: Path) -> None:
    """Публичная точка входа для трея. Окно уже открыто — поднимаем его
    (тот же процесс только что получил клик по трею, поэтому обычный
    SetForegroundWindow срабатывает без обходов foreground lock, в отличие
    от browser.bring_to_front, который зовётся из фонового потока без
    свежего пользовательского ввода). Иначе — новый поток с Tk-окном."""
    hwnd = win32gui.FindWindow(None, _TITLE)
    if hwnd:
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.SetForegroundWindow(hwnd)
        return
    threading.Thread(target=_run_window, args=(history_path,), daemon=True).start()
