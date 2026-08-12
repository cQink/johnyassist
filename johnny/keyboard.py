"""Печать текста в активное поле через SendInput.

Юникодом, а не виртуальными кодами клавиш: раскладка клавиатуры в момент
команды неизвестна, а KEYEVENTF_UNICODE от неё не зависит — русский текст
напечатается и на английской раскладке.
"""

import ctypes
from ctypes import wintypes

KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_KEYUP = 0x0002
_INPUT_KEYBOARD = 1
_VK_RETURN = 0x0D


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUT(ctypes.Structure):
    # Настоящий Windows INPUT.DUMMYUNIONNAME — объединение MOUSEINPUT | KEYBDINPUT |
    # HARDWAREINPUT. Его размер задаёт самый большой член (MOUSEINPUT), поэтому если
    # объявить в объединении только ki, ctypes.sizeof(_INPUT) окажется меньше настоящего
    # размера структуры Windows — и SendInput будет отвергать такой cbSize.
    class _UNION(ctypes.Union):
        _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT), ("hi", _HARDWAREINPUT)]

    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _UNION)]


# use_last_error=True обязателен: без него ctypes не сохраняет настоящий код ошибки
# Windows в module.get_last_error(), и он может быть затёрт внутренними вызовами ctypes
# до того, как мы его прочитаем.
_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.SendInput.restype = wintypes.UINT
_user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(_INPUT), ctypes.c_int)


def _send(scan: int, flags: int) -> None:
    event = _INPUT()
    event.type = _INPUT_KEYBOARD
    event.ki = _KEYBDINPUT(0, scan, flags, 0, None)
    inserted = _user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(event))
    if inserted != 1:
        code = ctypes.get_last_error()
        raise OSError(
            f"SendInput не вставила событие ввода (вставлено {inserted} из 1), "
            f"код ошибки Windows {code}"
        )


def type_text(text: str) -> None:
    for char in text:
        code = ord(char)
        _send(code, KEYEVENTF_UNICODE)
        _send(code, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP)


def press_enter() -> None:
    ctypes.windll.user32.keybd_event(_VK_RETURN, 0, 0, 0)
    ctypes.windll.user32.keybd_event(_VK_RETURN, 0, KEYEVENTF_KEYUP, 0)
