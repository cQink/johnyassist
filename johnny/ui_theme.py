"""Внешний вид панели: палитра, шрифты и нарисованные элементы управления.

Отдельным модулем, а не внутри panel.py, по одной причине: panel.py и так
занят логикой (что показать, что сохранить, что спросить у коннектора), и
подмешивать туда рисование кнопок значит получить файл, в котором нельзя
поменять цвет, не читая обработчики.

ГЛАВНОЕ ПРАВИЛО ПАЛИТРЫ: цвет — это СИГНАЛ, а не площадь.

Прошлая панель заливала 40% окна насыщенным фиолетовым и клала на него
плоские цветные прямоугольники-кнопки. Получалась «бумага на бумаге»: глазу
не за что зацепиться, потому что самое яркое пятно на экране ничего не
означает. Здесь наоборот — корпус тёмный и молчит, а лаймовый горит только
там, где идёт живой сигнал (Джони слышит, думает, говорит). Фиолетовый
остался, но понижен до второго цвета данных: модель, устройство, пороги.

Глубина в Tkinter делается не тенями (их нет) и не градиентами на Frame (их
тоже нет), а двумя вещами: разницей яркости соседних поверхностей и рамкой
в один пиксель. Поэтому здесь три уровня — WELL (утоплено), CHASSIS (фон),
PANEL (приподнято), — и все элементы рисуются на Canvas, где скругления и
рамки доступны, в отличие от tk.Button.
"""

import tkinter as tk
from tkinter import font as tkfont

# ── Палитра ──────────────────────────────────────────────────────────────────
# Три уровня глубины. Их значения намеренно близки: разница в 6–10 единиц
# яркости читается как «поверхность выше/ниже», а разница в 60 — как «другой
# кусок бумаги», что и ломало прошлую панель.
WELL = "#050507"        # утоплено: колодец осциллографа, поля ввода
CHASSIS = "#0A0A0C"     # корпус: фон окна
PANEL = "#141419"       # приподнято: тело вкладки, карточки
PANEL_HI = "#1C1C23"    # приподнято + наведение

EDGE = "#262630"        # светлая рамка в 1px — верхняя грань «приподнятого»
EDGE_SOFT = "#191920"    # та же грань, но приглушённая (спокойные блоки)

# Сигнальные цвета. Лаймовый — ТОЛЬКО про живое состояние; если он появился
# на неактивном элементе, правило нарушено и панель снова врёт глазу.
PHOSPHOR = "#D9F99D"
PHOSPHOR_HI = "#E8FFC0"  # наведение на лаймовую кнопку
PHOSPHOR_DIM = "#7E9459"  # лаймовый в покое (волна на тишине)

INDIGO = "#818CF8"       # второй цвет данных: модель, устройство, пороги
INDIGO_DIM = "#4A4F8A"

TEXT = "#E9E9EE"
TEXT_DIM = "#7C7C8A"
TEXT_MUTE = "#4E4E5A"

DANGER = "#F2555A"
WARN = "#F0A500"

# Цвет фазы. Живёт здесь, а не в panel.py: фазу рисуют и колодец, и точка
# статуса в шапке, и держать два списка оттенков — верный способ развести их.
PHASE_COLOR = {
    "idle": INDIGO_DIM,
    "wake": PHOSPHOR,
    "listen": PHOSPHOR,
    "think": "#FDE68A",   # думает — тёплый, отличим от «слушаю»
    "speak": "#7DD3FC",   # говорит сам
    "paused": "#33333F",  # погашен
}

# ── Шрифты ───────────────────────────────────────────────────────────────────
# Bahnschrift — это DIN, промышленный шрифт приборных панелей и указателей.
# Он стоит в Windows 10+ и здесь выбран не для красоты: узкие прописные в нём
# читаются в мелком кегле, а Segoe UI в тех же 9–10 пунктах превращается в
# кашу. Cascadia Mono — для данных (команда, модель, цифры): моноширинный
# отделяет «что сказал человек» от подписей интерфейса.
#
# Функциями, а не константами: tkfont трогать до создания root нельзя, а
# модуль импортируется раньше.
def display(size=15):
    return ("Bahnschrift SemiCondensed", size, "bold")


def label(size=10):
    return ("Bahnschrift SemiCondensed", size)


def button(size=11):
    return ("Bahnschrift SemiCondensed", size, "bold")


def mono(size=9):
    return ("Cascadia Mono", size)


def body(size=9):
    return ("Segoe UI", size)


# ── Рисование ────────────────────────────────────────────────────────────────

def _rgb(color: str) -> tuple[int, int, int]:
    return tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))


def blend(base: str, top: str, amount: float) -> str:
    """Смешать два цвета: amount=0 — base, amount=1 — top.

    В Tk нет ни прозрачности, ни градиентов: canvas умеет только сплошную
    заливку. Поэтому всякое «свечение» и «затухание» здесь — это заранее
    посчитанные сплошные цвета, положенные слоями. Смешивание с ФОНОМ
    заменяет альфа-канал: полупрозрачный лаймовый поверх тёмного колодца и
    смешанный с ним цвет выглядят одинаково, пока под ними ровный фон.
    """
    amount = max(0.0, min(1.0, amount))
    a, b = _rgb(base), _rgb(top)
    return "#%02x%02x%02x" % tuple(
        round(a[i] + (b[i] - a[i]) * amount) for i in range(3)
    )


def round_rect(canvas, x1, y1, x2, y2, radius, **kwargs):
    """Прямоугольник со скруглёнными углами на Canvas.

    smooth=True превращает ломаную в сплайн — это единственный способ получить
    скругление в Tk: у виджетов border-radius нет вовсе, и прошлые кнопки были
    прямоугольными не по замыслу, а потому что других не бывает.
    """
    points = [
        x1 + radius, y1,
        x2 - radius, y1,
        x2, y1,
        x2, y1 + radius,
        x2, y2 - radius,
        x2, y2,
        x2 - radius, y2,
        x1 + radius, y2,
        x1, y2,
        x1, y2 - radius,
        x1, y1 + radius,
        x1, y1,
    ]
    return canvas.create_polygon(points, smooth=True, **kwargs)


class Button(tk.Canvas):
    """Кнопка, нарисованная на Canvas: скругление, наведение, нажатие, фокус.

    tk.Button не подходит принципиально: он всегда прямоугольный, его фон в
    Windows не совпадает с заданным при наведении, и рамку в один пиксель ему
    не задать. Именно из-за этого прошлая панель выглядела как аппликация —
    шесть разноцветных прямоугольников разного размера.

    variant:
      primary — лаймовая, главное действие (одна на экран);
      ghost   — тёмная с рамкой, обычное действие;
      icon    — квадратная, только символ.
    """

    _VARIANTS = {
        "primary": dict(fill=PHOSPHOR, hover=PHOSPHOR_HI, text=CHASSIS, edge=""),
        "ghost": dict(fill=PANEL, hover=PANEL_HI, text=TEXT, edge=EDGE),
        "icon": dict(fill=PANEL, hover=PANEL_HI, text=TEXT_DIM, edge=EDGE),
    }

    def __init__(self, parent, text, command=None, variant="ghost",
                 width=None, height=30, radius=7, bg=CHASSIS, font=None):
        self._style = dict(self._VARIANTS[variant])
        self._font = font or button()
        self._text = text
        self._radius = radius
        self._command = command
        self._enabled = True
        self._hovered = False

        if width is None:
            # Ширина по тексту, а не «на глаз»: подписи разной длины в кнопках
            # одинаковой ширины — ещё один признак аппликации.
            measure = tkfont.Font(font=self._font)
            width = measure.measure(text) + (24 if variant != "icon" else 18)
            if variant == "icon":
                width = max(width, height)

        super().__init__(parent, width=width, height=height, bg=bg,
                         highlightthickness=0, bd=0, takefocus=1)
        self._cw, self._ch = width, height
        self._draw()

        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        # Клавиатура: пробел и Enter должны работать, иначе панель непроходима
        # без мыши. Фокус видно по рамке (см. _draw).
        self.bind("<FocusIn>", lambda _e: self._draw())
        self.bind("<FocusOut>", lambda _e: self._draw())
        self.bind("<Return>", lambda _e: self._fire())
        self.bind("<space>", lambda _e: self._fire())
        self.configure(cursor="hand2")

    # -- отрисовка --

    def _draw(self):
        self.delete("all")
        style = self._style
        if not self._enabled:
            fill, text_color, edge = PANEL, TEXT_MUTE, EDGE_SOFT
        else:
            fill = style["hover"] if self._hovered else style["fill"]
            text_color, edge = style["text"], style["edge"]

        focused = self.focus_get() is self
        round_rect(self, 1, 1, self._cw - 1, self._ch - 1, self._radius,
                   fill=fill, outline=(PHOSPHOR if focused else edge),
                   width=2 if focused else 1)
        self.create_text(self._cw / 2, self._ch / 2 + 1, text=self._text,
                         fill=text_color, font=self._font)

    # -- поведение --

    def _on_enter(self, _event=None):
        if self._enabled:
            self._hovered = True
            self._draw()

    def _on_leave(self, _event=None):
        self._hovered = False
        self._draw()

    def _on_press(self, _event=None):
        if self._enabled:
            self.focus_set()
            # Нажатие видно сдвигом надписи на пиксель — самый дешёвый отклик,
            # который не требует второго набора цветов.
            self.move("all", 0, 1)

    def _on_release(self, _event=None):
        if self._enabled:
            self._draw()
            self._fire()

    def _fire(self):
        if self._enabled and self._command is not None:
            self._command()

    # -- публичное --

    def set_text(self, text):
        self._text = text
        self._draw()

    def set_enabled(self, enabled):
        self._enabled = bool(enabled)
        self.configure(cursor="hand2" if self._enabled else "arrow")
        self._draw()


class Tabs(tk.Frame):
    """Полоса вкладок: имя раздела + подчёркивание активного.

    Вкладки, а не один длинный скролл, потому что панель открывают чаще
    «посмотреть, что с Джони», чем «покрутить настройки»: раньше статус и
    настройки лежали в одном потоке, и до статуса надо было доскроллить.
    """

    def __init__(self, parent, names, on_change, bg=CHASSIS):
        super().__init__(parent, bg=bg)
        self._on_change = on_change
        self._buttons = {}
        self._active = names[0]
        for name in names:
            canvas = tk.Canvas(self, height=34, bg=bg, highlightthickness=0,
                               bd=0, takefocus=1, cursor="hand2")
            measure = tkfont.Font(font=button())
            canvas.configure(width=measure.measure(name) + 26)
            canvas.pack(side="left", padx=(0, 2))
            canvas.bind("<Button-1>", lambda _e, n=name: self.select(n))
            canvas.bind("<Return>", lambda _e, n=name: self.select(n))
            canvas.bind("<space>", lambda _e, n=name: self.select(n))
            canvas.bind("<Enter>", lambda _e, n=name: self._paint(n, hover=True))
            canvas.bind("<Leave>", lambda _e, n=name: self._paint(n))
            self._buttons[name] = canvas
        self._repaint()

    def _paint(self, name, hover=False):
        canvas = self._buttons[name]
        canvas.delete("all")
        width = int(canvas["width"])
        active = name == self._active
        color = PHOSPHOR if active else (TEXT if hover else TEXT_DIM)
        canvas.create_text(width / 2, 15, text=name, fill=color, font=button())
        # Подчёркивание только у активной: рамка вокруг вкладки съела бы
        # воздух, а линия в 2px читается с любого расстояния.
        if active:
            canvas.create_rectangle(6, 29, width - 6, 31, fill=PHOSPHOR, outline="")

    def _repaint(self):
        for name in self._buttons:
            self._paint(name)

    def select(self, name):
        self._active = name
        self._repaint()
        self._on_change(name)

    @property
    def active(self):
        return self._active


class Slider(tk.Canvas):
    """Ползунок: утопленный жёлоб, лаймовая заполненная часть, круглая ручка.

    Свой, а не tk.Scale, по той же причине, что и кнопки: у tk.Scale ручка —
    объёмный брусок в стиле Windows 95, который невозможно перекрасить целиком
    (борта рисует сама Tk). Рядом с нарисованными кнопками он выглядел бы
    деталью от другого прибора.

    Значение отдаётся наружу через variable (DoubleVar) — как у tk.Scale,
    чтобы _save() не знал, чем именно нарисован ползунок.
    """

    def __init__(self, parent, variable, from_=0.0, to=1.0, step=0.05,
                 width=220, height=26, bg=CHASSIS, on_change=None):
        super().__init__(parent, width=width, height=height, bg=bg,
                         highlightthickness=0, bd=0, takefocus=1, cursor="hand2")
        self._var = variable
        self._from, self._to, self._step = from_, to, step
        self._cw, self._ch = width, height
        self._on_change = on_change
        self._pad = 9  # радиус ручки: трек короче на неё с обеих сторон
        self.bind("<Button-1>", self._on_click)
        self.bind("<B1-Motion>", self._on_click)
        self.bind("<Configure>", self._on_resize)
        self.bind("<FocusIn>", lambda _e: self._draw())
        self.bind("<FocusOut>", lambda _e: self._draw())
        self.bind("<Left>", lambda _e: self._nudge(-1))
        self.bind("<Right>", lambda _e: self._nudge(+1))
        self._draw()

    def _on_resize(self, event):
        self._cw = event.width
        self._draw()

    def _ratio(self):
        span = (self._to - self._from) or 1.0
        return max(0.0, min(1.0, (float(self._var.get()) - self._from) / span))

    def _draw(self):
        self.delete("all")
        mid = self._ch / 2
        x0, x1 = self._pad, self._cw - self._pad
        knob_x = x0 + (x1 - x0) * self._ratio()

        # Жёлоб — утоплен: тёмная заливка + рамка в 1px.
        round_rect(self, x0, mid - 3, x1, mid + 3, 3,
                   fill=WELL, outline=EDGE, width=1)
        # Заполненная часть — сигнал, поэтому лаймовая.
        if knob_x > x0 + 1:
            round_rect(self, x0, mid - 3, knob_x, mid + 3, 3,
                       fill=PHOSPHOR_DIM, outline="")
        focused = self.focus_get() is self
        self.create_oval(knob_x - 7, mid - 7, knob_x + 7, mid + 7,
                         fill=PHOSPHOR, outline=(TEXT if focused else CHASSIS), width=2)

    def _set_from_x(self, x):
        x0, x1 = self._pad, self._cw - self._pad
        ratio = max(0.0, min(1.0, (x - x0) / max(1.0, x1 - x0)))
        raw = self._from + ratio * (self._to - self._from)
        # Округление к шагу: иначе в settings.yaml уезжает 0.6133333333.
        value = round(round(raw / self._step) * self._step, 4)
        self._var.set(value)
        self._draw()
        if self._on_change is not None:
            self._on_change(value)

    def _on_click(self, event):
        self.focus_set()
        self._set_from_x(event.x)

    def _nudge(self, direction):
        current = float(self._var.get())
        self._var.set(max(self._from, min(self._to, current + direction * self._step)))
        self._draw()
        if self._on_change is not None:
            self._on_change(float(self._var.get()))


def hairline(parent, bg=CHASSIS, color=EDGE_SOFT):
    """Разделительная линия в один пиксель.

    Frame высотой 1 — единственная линия в Tk, которая не тянет за собой
    рамку виджета. Разделяет блоки там, где рамка вокруг каждого была бы
    шумом.
    """
    line = tk.Frame(parent, bg=color, height=1)
    return line
