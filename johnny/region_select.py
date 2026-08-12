"""Выделение области экрана мышью. Запускается ОТДЕЛЬНЫМ ПРОЦЕССОМ.

Почему процессом, а не окном внутри Джони: панель держит свой Tk-цикл в своём
потоке (см. tray.py — каждое окно открывает свой mainloop в своём потоке), а
тулкиты выполняются в фоновом. Создавать Tk-объекты из чужого потока нельзя:
tkinter не потокобезопасен, и это не теория — оно виснет молча, без исключения,
и вешает панель вместе с собой. Отдельный процесс снимает вопрос целиком: у
него свой интерпретатор Tk и свой поток, а обратно приходит четыре числа.

Запуск: python -m johnny.region_select
Печатает в stdout «left top right bottom» или ничего, если человек передумал.

Сам снимок здесь НЕ делается: экран снимается ДО показа оверлея (см.
capture.region), иначе полупрозрачная плёнка попала бы в кадр и OCR читал бы
текст сквозь неё.
"""

from __future__ import annotations

import sys
import tkinter as tk

# Плёнка поверх экрана. 0.25 — компромисс: видно, что выделяешь, и видно, что
# режим выделения включён. На 0.0 человек не понимает, что происходит, на 0.5
# уже не разобрать мелкий шрифт, по которому и целятся.
_ALPHA = 0.25
_OUTLINE = "#00d2ff"


def select() -> tuple[int, int, int, int] | None:
    """Показать оверлей и вернуть (left, top, right, bottom). None — отмена."""
    root = tk.Tk()
    root.withdraw()
    # Оверлей на ВСЕ мониторы: у владельца их два, и окно во весь «экран»
    # накрыло бы только один — выделить на втором стало бы нечем.
    width = root.winfo_vrootwidth() or root.winfo_screenwidth()
    height = root.winfo_vrootheight() or root.winfo_screenheight()
    left = root.winfo_vrootx()
    top = root.winfo_vrooty()

    overlay = tk.Toplevel(root)
    overlay.overrideredirect(True)
    overlay.geometry(f"{width}x{height}+{left}+{top}")
    overlay.attributes("-topmost", True)
    overlay.attributes("-alpha", _ALPHA)
    overlay.configure(bg="black", cursor="crosshair")

    canvas = tk.Canvas(overlay, bg="black", highlightthickness=0, cursor="crosshair")
    canvas.pack(fill="both", expand=True)

    state: dict = {"x": 0, "y": 0, "rect": None, "result": None}

    def press(event):
        state["x"], state["y"] = event.x, event.y
        state["rect"] = canvas.create_rectangle(
            event.x, event.y, event.x, event.y, outline=_OUTLINE, width=2
        )

    def drag(event):
        if state["rect"] is not None:
            canvas.coords(state["rect"], state["x"], state["y"], event.x, event.y)

    def release(event):
        # Координаты канвы — от угла оверлея, а он сам может начинаться в
        # минусе (второй монитор слева от главного). Обратно в экранные
        # переводим здесь, иначе ImageGrab снимет не то место.
        x1, x2 = sorted((state["x"] + left, event.x + left))
        y1, y2 = sorted((state["y"] + top, event.y + top))
        # Случайный клик без протяжки — это не выделение нулевой области, это
        # «передумал». Снимать 1×1 и отдавать его в Azure незачем.
        if x2 - x1 >= 4 and y2 - y1 >= 4:
            state["result"] = (x1, y1, x2, y2)
        root.quit()

    def cancel(_event=None):
        root.quit()

    canvas.bind("<ButtonPress-1>", press)
    canvas.bind("<B1-Motion>", drag)
    canvas.bind("<ButtonRelease-1>", release)
    # Escape обязателен: оверлей поверх всех окон и без клавиши выхода
    # выглядит как зависший экран.
    overlay.bind("<Escape>", cancel)
    overlay.focus_force()
    root.mainloop()

    try:
        root.destroy()
    except tk.TclError:
        pass
    return state["result"]


if __name__ == "__main__":
    box = select()
    if box is None:
        sys.exit(1)
    print(" ".join(str(value) for value in box))
