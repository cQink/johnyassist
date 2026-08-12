import ctypes

import pytest

from johnny import keyboard


def test_размер_input_совпадает_с_настоящей_структурой_windows():
    """Windows INPUT = DWORD type + объединение MOUSEINPUT | KEYBDINPUT | HARDWAREINPUT,
    размер объединения задаёт самый большой член — MOUSEINPUT (dx, dy, mouseData, dwFlags,
    time — пять DWORD, затем ULONG_PTR dwExtraInfo с выравниванием по размеру указателя).

    На x64 (указатель 8 байт, объединение выровнено по 8):
        MOUSEINPUT = 5*4 + паддинг(4) + 8 = 32
        INPUT      = DWORD(4) + паддинг(4) + 32 = 40
    На x86 (указатель 4 байта, объединение выровнено по 4):
        MOUSEINPUT = 5*4 + 4 = 24
        INPUT      = DWORD(4) + 24 = 28

    Если ctypes.sizeof(_INPUT) не совпадает с этим числом, SendInput получает неверный
    cbSize, ничего не вставляет и возвращает 0 (ERROR_INVALID_PARAMETER) — печать
    в активное поле молча не работает. Именно так проявлялся дефект, который ловит эта
    проверка.
    """
    is_64bit = ctypes.sizeof(ctypes.c_void_p) == 8
    expected = 40 if is_64bit else 28

    assert ctypes.sizeof(keyboard._INPUT) == expected


def test_send_поднимает_oserror_если_sendinput_не_вставил_событие(monkeypatch):
    monkeypatch.setattr(keyboard._user32, "SendInput", lambda *args, **kwargs: 0)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 87)

    with pytest.raises(OSError):
        keyboard._send(ord("а"), keyboard.KEYEVENTF_UNICODE)


def test_каждый_символ_даёт_нажатие_и_отпускание(monkeypatch):
    sent = []
    monkeypatch.setattr(keyboard, "_send", lambda scan, flags: sent.append((scan, flags)))

    keyboard.type_text("ок")

    assert [scan for scan, _flags in sent] == [ord("о"), ord("о"), ord("к"), ord("к")]
    down, up = sent[0][1], sent[1][1]
    assert down == keyboard.KEYEVENTF_UNICODE
    assert up == keyboard.KEYEVENTF_UNICODE | keyboard.KEYEVENTF_KEYUP


def test_пустой_текст_ничего_не_печатает(monkeypatch):
    sent = []
    monkeypatch.setattr(keyboard, "_send", lambda scan, flags: sent.append((scan, flags)))

    keyboard.type_text("")

    assert sent == []
