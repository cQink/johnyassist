"""Снимок панели для глазной проверки дизайна. Одноразовый, в поставку не входит.

Поднимает панель без Джони, прогоняет кружок по фазам и сохраняет PNG на каждой.
Нужен потому, что проверить «кружок горит на имени» тестом можно (см.
tests/test_visualizer.py), а «раскладка не разъехалась» — нет.

Запуск: python -m tools.panel_shot
"""

import sys
import tkinter as tk
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from johnny import activity, panel  # noqa: E402

OUT = _ROOT / "docs" / "designs" / "shots"


class FakeEngine:
    available = True
    model_path = "models/vosk-model-ru-0.42"
    model = "medium"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    root = tk.Tk()
    root.withdraw()
    paused = {"on": False}
    p = panel.SettingsPanel(
        root,
        get_last_command=lambda: "открой youtube",
        get_paused=lambda: paused["on"],
        get_engines=lambda: (FakeEngine(), FakeEngine()),
        on_toggle_pause=lambda: paused.__setitem__("on", not paused["on"]),
    )
    p.update()

    shots = [
        ("idle", activity.IDLE, 0.0),
        ("wake", activity.WAKE, 0.9),
        ("listen-loud", activity.LISTEN, 1.0),
        ("listen-quiet", activity.LISTEN, 0.25),
        ("think", activity.THINK, 0.0),
        ("speak", activity.SPEAK, 0.7),
        ("paused", activity.PAUSED, 0.0),
    ]
    for name, phase, level in shots:
        activity.reset()
        activity.set_phase(phase)
        if level:
            activity.pulse(level)
        paused["on"] = phase == activity.PAUSED
        p._wave.draw(activity.snapshot())
        p._update_pause_button()
        # Подпись тоже перерисовываем: живая панель зовёт оба метода раз в
        # секунду, а здесь таймеры не крутятся — без этой строки на снимке
        # «paused» под погасшим кружком оставалась надпись «Слушает».
        p._show_engine_status()
        p.update()
        p.update_idletasks()
        _grab(p, OUT / f"panel-{name}.png")
        print(name, "ok")

    print("size:", p.winfo_width(), "x", p.winfo_height())
    root.destroy()


def _grab(widget, path: Path) -> None:
    """Снимок ОКНА через PrintWindow, а не области экрана.

    ImageGrab.grab(bbox) снимает то, что в этот момент лежит на экране по этим
    координатам: первый же чат поверх панели — и в файле оказывается он.
    PrintWindow просит окно перерисовать себя в наш контекст, и что там сверху,
    роли не играет.
    """
    import win32gui
    import win32ui
    from ctypes import windll
    from PIL import Image

    widget.update_idletasks()
    hwnd = windll.user32.GetParent(widget.winfo_id())
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    w, h = right - left, bottom - top

    win_dc = win32gui.GetWindowDC(hwnd)
    src = win32ui.CreateDCFromHandle(win_dc)
    dst = src.CreateCompatibleDC()
    bmp = win32ui.CreateBitmap()
    bmp.CreateCompatibleBitmap(src, w, h)
    dst.SelectObject(bmp)
    try:
        # 2 = PW_RENDERFULLCONTENT: без него окна с составными слоями
        # приходят чёрными.
        windll.user32.PrintWindow(hwnd, dst.GetSafeHdc(), 2)
        info = bmp.GetInfo()
        Image.frombuffer(
            "RGB", (info["bmWidth"], info["bmHeight"]), bmp.GetBitmapBits(True),
            "raw", "BGRX", 0, 1,
        ).save(path)
    finally:
        win32gui.DeleteObject(bmp.GetHandle())
        dst.DeleteDC()
        src.DeleteDC()
        win32gui.ReleaseDC(hwnd, win_dc)


if __name__ == "__main__":
    main()
