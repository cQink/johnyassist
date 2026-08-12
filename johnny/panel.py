"""UI-панель настроек Johnny.

Запускается из меню трея или отдельно. Позволяет:
- видеть состояние (что Джони делает прямо сейчас, последняя команда);
- менять голосовые настройки (провайдер, громкость TTS, модель Groq);
- менять параметры распознавания (модель Whisper, устройство);
- менять wake-word;
- запускать тулкиты руками.

Все изменения сохраняются в config/settings.yaml.

Как устроен экран (и почему именно так):

    JONY                        История  Лог  Автозапуск
    ┌──────────────────────────── колодец ──────────────┐
    │ ▪ СЛУШАЕТ                        whisper:medium   │
    │            ▁▃▅█ ◉ █▅▃▁                            │
    │ › последняя команда                               │
    └───────────────────────────────────────────────────┘
    [ ПАУЗА ]  ⟳  ✕
    ГОЛОС · РАСПОЗНАВАНИЕ · ТУЛКИТЫ        ← вкладки
    ...

Статус стоит первым и занимает целый колодец, потому что панель открывают
чаще «посмотреть, живой ли Джони», чем «покрутить настройки». Раньше и то и
другое лежало в одном скролле: чтобы увидеть состояние, надо было прокрутить
мимо настроек, а половину экрана занимала фиолетовая плита, на которой не
было ничего, кроме кружка в 132 пикселя.

Внешний вид (палитра, шрифты, нарисованные кнопки) живёт в ui_theme.py — см.
там про правило «цвет это сигнал, а не площадь».
"""

import logging
import os
import queue
import sys
import threading
import tkinter as tk
from tkinter import filedialog, ttk
from pathlib import Path

import yaml

from . import activity, panel_tools, ui_theme, visualizer

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent
_SETTINGS_PATH = _ROOT / "config" / "settings.yaml"

# Кадров в секунду для волны. 20 — пульсация читается как живая, и это в 12
# раз реже, чем блок с микрофона, так что рисование ничего не догоняет.
_FPS_MS = 50

# Размер окна. Ширина рассчитана на две колонки настроек с воздухом, высота —
# на колодец + вкладку без прокрутки (прокрутка нужна только «Тулкитам», их 8).
# Высота подобрана по самой длинной вкладке из коротких: на 840 под настройками
# оставалась пустая треть экрана, а окно теперь тянется — кому нужно больше
# места под тулкиты, растянет.
_WIN_W = 780
_WIN_H = 748

# Колодец осциллографа: высота подобрана под три строки внутри (состояние,
# волна, команда) так, чтобы волна на максимуме (66px) не упиралась в текст.
# Больше 160 колодец не нужен: в покое он и так пустой, а лишняя высота
# отнимается у вкладки «Тулкиты», где её реально не хватает.
_WELL_H = 158
# Высота поля под волну и отдельно — под кружок. Разведены намеренно, см.
# visualizer.frame: одним числом крупная волна тянула кружок на пол-колодца.
_WAVE_SIZE = 132
_DISC_SIZE = 76

# Цвет строки состояния по тону. Фон теперь везде тёмный, поэтому на тон
# хватает одного цвета — прежней пары «для светлой карточки / для тёмного
# бейджа» больше не нужно.
_TONE_COLOR = {
    "ok": ui_theme.PHOSPHOR,
    "warn": ui_theme.WARN,
    "bad": ui_theme.DANGER,
    "idle": ui_theme.TEXT_DIM,
}

# Что писать в колодце по живой фазе, когда Джони в порядке и не на паузе.
#
# Нужно потому, что слово и волна теперь стоят в ОДНОМ колодце и обязаны
# совпадать. panel_tools.status отвечает на грубый вопрос «движки живы, паузы
# нет» и says «Слушает» всё время; фаза отвечает на живой вопрос «чем занят
# прямо сейчас». Пока они жили порознь (бейдж в шапке и кружок на карточке),
# расхождение не бросалось в глаза — а рядом читается как поломка: надпись
# «СЛУШАЕТ» при голубой волне «говорю».
#
# IDLE — None намеренно: имя не звучало, и общий ответ «Слушает» тут точнее
# любого слова про фазу.
_PHASE_LABEL = {
    activity.WAKE: "Услышал имя",
    activity.LISTEN: "Слушаю команду",
    activity.THINK: "Думаю",
    activity.SPEAK: "Говорю",
}


def _load_settings() -> dict:
    if _SETTINGS_PATH.exists():
        return yaml.safe_load(_SETTINGS_PATH.read_text(encoding="utf-8")) or {}
    return {}


def _save_settings(data: dict) -> None:
    _SETTINGS_PATH.write_text(
        yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )


def _load_full_config():
    """Конфиг целиком (настройки + ключи) — нужен тулкитам.

    Панель сама читает только settings.yaml, а коннекторам нужны ещё и ключи из
    secrets.yaml. Ошибку глотаем: панель обязана открыться и без конфига —
    иначе человек не сможет починить конфиг через ту самую панель.
    """
    try:
        from .config import load_config

        return load_config(str(_ROOT / "config"))
    except Exception:
        logger.exception("Панель не смогла прочитать конфиг, тулкиты будут недоступны")
        return None


def _tooltip(widget, text: str) -> None:
    """Подсказка по наведению.

    Нужна из-за дизайна: перезапуск и выход — иконки без подписи, а «⟳»
    одинаково читается и как «перезапустить панель», и как «перезапустить
    Джони». Второе необратимо на полминуты (одна только модель Vosk грузится
    две минуты), поэтому догадываться человек не должен.
    """
    tip = {"win": None}

    def show(_event=None):
        if tip["win"] is not None:
            return
        try:
            x = widget.winfo_rootx() + widget.winfo_width() // 2
            y = widget.winfo_rooty() - 30
            win = tk.Toplevel(widget)
            win.wm_overrideredirect(True)  # без рамки и заголовка окна
            win.wm_geometry(f"+{x}+{y}")
            # Рамка в 1px вокруг подсказки: на тёмном фоне без неё всплывашка
            # сливается с окном под собой.
            border = tk.Frame(win, bg=ui_theme.EDGE)
            border.pack()
            tk.Label(border, text=text, font=ui_theme.body(), bg=ui_theme.PANEL,
                     fg=ui_theme.TEXT, padx=9, pady=5).pack(padx=1, pady=1)
            tip["win"] = win
        except tk.TclError:
            tip["win"] = None

    def hide(_event=None):
        win = tip["win"]
        tip["win"] = None
        if win is not None:
            try:
                win.destroy()
            except tk.TclError:
                pass

    widget.bind("<Enter>", show, add="+")
    widget.bind("<Leave>", hide, add="+")
    # Подсказка живёт в своём Toplevel и сама не закроется, если кнопку убрали
    # из-под курсора (закрыли панель) — тогда она осталась бы висеть поверх
    # всех окон без владельца.
    widget.bind("<Destroy>", hide, add="+")
    widget.bind("<ButtonPress>", hide, add="+")


class ScopeWell(tk.Canvas):
    """Колодец осциллографа — главный элемент панели.

    Показывает всё «что сейчас» разом: состояние словом, живую волну голоса,
    движок распознавания и последнюю услышанную команду. Раньше это было
    растащено по трём местам (бейдж в шапке, кружок на фиолетовой плите,
    отдельная терминальная полоса), и связь между ними приходилось держать
    в голове.

    Геометрию волны считает visualizer.py, состояние берётся из activity.py —
    здесь только рисование. Разделение не ради красоты: цикл прослушивания
    пишет состояние из своего потока, а Canvas можно трогать только из потока
    Tk, поэтому связь между ними — снимок по таймеру, а не вызов.

    Рисуем прямоугольниками по сетке, а не сглаженными фигурами: дизайн
    просит пиксельную волну с дизерингом по краю кружка.

    Постоянные части (фон, три подписи) создаются один раз и меняются через
    itemconfigure; на каждом кадре перерисовывается только то, что помечено
    тегом "signal". Полный delete("all") двадцать раз в секунду перерисовывал
    бы и текст — он мигал бы на глазах.
    """

    def __init__(self, parent, height=_WELL_H, bg=ui_theme.CHASSIS):
        super().__init__(parent, height=height, bg=bg,
                         highlightthickness=0, bd=0)
        self._h = height
        self._tick = 0.0
        self._built = False
        # Подписи держим ЗДЕСЬ, а не только в элементах холста: set_state и
        # set_engine зовут до первой укладки окна, когда элементов ещё нет.
        # Раньше такой вызов молча пропадал (ловился except AttributeError), и
        # движок в углу колодца не появлялся вовсе.
        self._state_text = "…"
        self._state_color = ui_theme.TEXT_DIM
        self._engine_text = ""
        self._command_text = "—"
        self.bind("<Configure>", self._on_resize)

    def _on_resize(self, _event=None):
        """Ширину узнаём только после укладки — до неё winfo_width() даёт 1."""
        self._build()

    def _width(self) -> int:
        width = self.winfo_width()
        return width if width > 1 else _WIN_W - 48

    def _build(self):
        """Постоянные части колодца. Пересоздаются при смене ширины окна."""
        self.delete("chrome")
        width = self._width()
        # Сам колодец: утоплённая поверхность. Она темнее фона окна, и это
        # единственное, что делает её «ниже» — теней в Tk нет.
        ui_theme.round_rect(self, 1, 1, width - 1, self._h - 1, 10,
                            fill=ui_theme.WELL, outline=ui_theme.EDGE,
                            width=1, tags="chrome")
        # Нулевая линия. Без неё колодец в покое — пустая тёмная коробка:
        # волна на тишине плоская, кружок мал, и смотреть не на что. У осцил-
        # лографа без сигнала линия тоже остаётся, и это честно: прибор
        # включён, сигнала нет.
        _, cy = self._center()
        self.create_line(20, cy, width - 20, cy, fill=ui_theme.EDGE,
                         width=1, tags="chrome")
        if not self._built:
            self._dot = self.create_text(20, 22, text="▪", anchor="w",
                                         fill=ui_theme.TEXT_DIM, font=ui_theme.mono(10))
            self._state = self.create_text(34, 22, text="…", anchor="w",
                                           fill=ui_theme.TEXT_DIM, font=ui_theme.display(13))
            self._engine = self.create_text(width - 20, 22, text="", anchor="e",
                                            fill=ui_theme.INDIGO, font=ui_theme.mono(8))
            self._prompt = self.create_text(20, self._h - 22, text="›", anchor="w",
                                            fill=ui_theme.PHOSPHOR_DIM, font=ui_theme.mono(10))
            self._command = self.create_text(36, self._h - 22, text="—", anchor="w",
                                             fill=ui_theme.TEXT_DIM, font=ui_theme.mono(9))
            self._built = True
        else:
            # Правый край подписи движка держится за ширину окна.
            self.coords(self._engine, width - 20, 22)
        # Подписи могли прийти до того, как элементы появились, — проставляем
        # их из сохранённых значений, а не надеемся на порядок вызовов.
        self.itemconfigure(self._state, text=self._state_text.upper(),
                           fill=self._state_color)
        self.itemconfigure(self._dot, fill=self._state_color)
        self.itemconfigure(self._engine, text=self._engine_text)
        self.itemconfigure(self._command, text=self._command_text)
        self.tag_lower("chrome")

    # -- живые части --

    def draw(self, snapshot) -> None:
        """Один кадр волны. Зовётся только из потока Tk."""
        if not self._built:
            self._build()
        self._tick += _FPS_MS / 1000.0
        frame = visualizer.frame(snapshot, self._tick, _WAVE_SIZE, disc_size=_DISC_SIZE)
        color = ui_theme.PHASE_COLOR.get(frame.tone, ui_theme.PHASE_COLOR["idle"])
        self.delete("signal")
        self._draw_bars(frame, color)
        self._draw_disc(frame, color)

    def _center(self):
        return self._width() / 2.0, self._h / 2.0 + 6

    def _draw_disc(self, frame, color: str) -> None:
        """Пиксельный кружок из клеток сетки.

        Пробовали светящуюся сферу (ореол + затемнение к краю + блик) — решением
        владельца 2026-08-09 вернулись сюда: пиксельная стилизация из макетов
        оказалась ближе. Волну при этом оставили новую, с круглыми концами
        и затуханием к краям (см. _draw_bars).
        """
        cell = visualizer.CELL
        cx, cy = self._center()
        r = frame.radius
        # Клетки внутри окружности. Полклетки внутрь у границы — иначе по краю
        # остаётся ступенька в целую клетку и кружок выглядит восьмиугольником.
        for gy in range(-r, r, cell):
            for gx in range(-r, r, cell):
                dx = gx + cell / 2.0
                dy = gy + cell / 2.0
                dist = (dx * dx + dy * dy) ** 0.5
                if dist > r:
                    continue
                # Дизеринг по краю: внешнее кольцо выкладываем в шахматку,
                # чтобы граница «рассыпалась», как на референсах.
                if dist > r - cell * 1.5 and ((gx // cell) + (gy // cell)) % 2:
                    continue
                self.create_rectangle(cx + gx, cy + gy, cx + gx + cell, cy + gy + cell,
                                      fill=color, outline="", tags="signal")

    def _draw_bars(self, frame, color: str) -> None:
        """Волна — во всю ширину колодца, симметрично от кружка.

        Шаг между столбиками считается от живой ширины, а не задан числом:
        колодец тянется на всю панель, и при фиксированном шаге волна на
        широком окне кончалась бы в середине.
        """
        cell = visualizer.CELL
        cx, cy = self._center()
        width = self._width()
        start = frame.radius + cell * 3
        available = width / 2.0 - start - 18
        if available <= 0 or not frame.bars:
            return
        step = available / len(frame.bars)
        for i, height in enumerate(frame.bars):
            if height <= 0:
                continue
            offset = start + i * step
            # Каждый третий столбик — фиолетовый: второй цвет данных из
            # макетов, он же не даёт волне слипнуться в одно лаймовое пятно.
            bar_color = ui_theme.INDIGO if frame.active and i % 3 == 2 else color
            # Дальние столбики приглушены: волна должна затухать к краям не
            # только высотой, но и яркостью — иначе край выглядит обрубленным.
            fade = 1.0 - (i / max(1, len(frame.bars))) * 0.55
            bar_color = ui_theme.blend(ui_theme.WELL, bar_color, fade)
            for sign in (-1, 1):
                x = cx + sign * offset
                if x - cell < 8 or x + cell > width - 8:
                    continue
                # Линия с круглыми концами вместо прямоугольника: рядом с
                # гладкой сферой прямые торцы читаются как обрезанные.
                self.create_line(x, cy - height / 2.0, x, cy + height / 2.0,
                                 fill=bar_color, width=cell, capstyle="round",
                                 tags="signal")

    # -- подписи --

    def set_state(self, text: str, color: str) -> None:
        self._state_text, self._state_color = text, color
        if self._built:
            try:
                self.itemconfigure(self._state, text=text.upper(), fill=color)
                self.itemconfigure(self._dot, fill=color)
            except tk.TclError:
                pass

    def set_engine(self, text: str) -> None:
        self._engine_text = text
        if self._built:
            try:
                self.itemconfigure(self._engine, text=text)
            except tk.TclError:
                pass

    def set_command(self, text: str) -> None:
        self._command_text = text
        if self._built:
            try:
                self.itemconfigure(self._command, text=text)
            except tk.TclError:
                pass


class ScrollArea(tk.Frame):
    """Прокручиваемая область с телом-фреймом внутри.

    Нужна только вкладке «Тулкиты»: их восемь, каждый с полем, подсказкой и
    строкой ответа, и в окно они не помещаются. Остальные вкладки короткие и
    прокрутки не просят — заводить её всем значило бы получить полосу
    прокрутки там, где крутить нечего.
    """

    def __init__(self, parent, bg=ui_theme.CHASSIS):
        super().__init__(parent, bg=bg)
        self._canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=self._canvas.yview,
                                  style="J.Vertical.TScrollbar")
        self.body = tk.Frame(self._canvas, bg=bg)
        self._window = self._canvas.create_window((0, 0), window=self.body, anchor="nw")
        self._canvas.configure(yscrollcommand=scrollbar.set)
        self._canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        self.body.bind("<Configure>", self._on_body)
        self._canvas.bind("<Configure>", self._on_canvas)
        # Колесо привязано к самой области, а не bind_all: bind_all перехватывал
        # колесо во всём окне, включая вкладки без прокрутки.
        for widget in (self._canvas, self.body):
            widget.bind("<MouseWheel>", self._on_wheel)

    def _on_body(self, _event=None):
        self._canvas.configure(scrollregion=self._canvas.bbox("all"))

    def _on_canvas(self, event):
        # Тело тянется по ширине холста — иначе карточки внутри схлопываются
        # по содержимому и не занимают всю вкладку.
        self._canvas.itemconfigure(self._window, width=event.width)

    def _on_wheel(self, event):
        self._canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def bind_wheel_deep(self, widget):
        """Колесо работает и над вложенными виджетами карточек."""
        widget.bind("<MouseWheel>", self._on_wheel, add="+")
        for child in widget.winfo_children():
            self.bind_wheel_deep(child)


class SettingsPanel(tk.Toplevel):
    """Панель управления Джони — открывается поверх существующего root."""

    def __init__(
        self,
        master=None,
        on_save=None,
        on_toggle_pause=None,
        on_restart=None,
        on_quit=None,
        on_open_history=None,
        on_open_log=None,
        on_toggle_autostart=None,
        get_last_command=None,
        get_paused=None,
        get_engines=None,
    ):
        super().__init__(master)
        self.title("Джони — Панель управления")
        self.configure(bg=ui_theme.CHASSIS)
        self.geometry(f"{_WIN_W}x{_WIN_H}")
        self.minsize(700, 620)
        self._on_save = on_save
        self._on_toggle_pause = on_toggle_pause
        self._on_restart = on_restart
        self._on_quit = on_quit
        self._on_open_history = on_open_history
        self._on_open_log = on_open_log
        self._on_toggle_autostart = on_toggle_autostart
        self._get_last_command = get_last_command
        self._get_paused = get_paused or (lambda: False)
        # Живое состояние движков спрашиваем у того, кто их держит (tray.py):
        # панель сама Vosk и Whisper не поднимает и знать про них не может.
        # None — панель открыли отдельно от Джони, и тогда честный ответ
        # «не запущен», а не бодрое «слушает» по данным с диска.
        self._get_engines = get_engines
        self._settings = _load_settings()
        self._vars: dict[str, tk.Variable] = {}
        # Виджеты тулкитов: поле ввода, кнопка и строка состояния на каждый.
        # Держим по имени коннектора, чтобы фоновая проверка готовности знала,
        # какую строку обновлять, когда доедет.
        self._tool_widgets: dict[str, dict] = {}
        self._tabs: dict[str, tk.Widget] = {}
        # Грубый статус живёт здесь между опросами движков (см. _apply_state).
        # Значение по умолчанию нужно на случай, если кадр волны отрисуется
        # раньше первого опроса.
        self._coarse: tuple[str, str] = ("…", "idle")
        self._config = _load_full_config()
        # Очередь «что показать», которую разбирает только поток Tk. Фоновый
        # поток не может позвать даже self.after: after сам по себе вызов в Tcl,
        # и из чужого потока он падает «main thread is not in main loop». Так что
        # поток кладёт замыкание сюда, а забирает его _pump по таймеру.
        self._ui_queue: queue.Queue = queue.Queue()
        self._style_ttk()
        self._build_ui()
        self._pump()
        # Центрируем на экране
        self.update_idletasks()
        x = (self.winfo_screenwidth() - _WIN_W) // 2
        y = (self.winfo_screenheight() - _WIN_H) // 2
        self.geometry(f"+{x}+{y}")
        self._refresh_status()
        self._animate()

    # ── Оформление ttk ───────────────────────────────────────────────────────

    def _style_ttk(self):
        """Полоса прокрутки — единственный ttk-виджет на панели.

        Её нельзя перекрасить как обычный tk-виджет: цвет берётся из темы.
        Тема clam — единственная встроенная, которая слушается настроек цвета
        на Windows (у vista/xpnative полосу рисует система, и она осталась бы
        светлой посреди тёмной панели).

        Выпадающие списки сознательно НЕ ttk (см. _well_option).
        """
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(
            "J.Vertical.TScrollbar",
            background=ui_theme.PANEL, troughcolor=ui_theme.CHASSIS,
            bordercolor=ui_theme.CHASSIS, arrowcolor=ui_theme.TEXT_DIM,
            borderwidth=0, relief="flat",
        )
        style.map("J.Vertical.TScrollbar", background=[("active", ui_theme.PANEL_HI)])

    # ── Сборка экрана ────────────────────────────────────────────────────────

    def _build_ui(self):
        self._build_masthead()
        self._build_scope()
        self._build_controls()
        self._build_bottom()   # до вкладок: side="bottom" забирает место первым
        self._build_tabs()

    def _build_masthead(self):
        head = tk.Frame(self, bg=ui_theme.CHASSIS)
        head.pack(fill="x", padx=24, pady=(16, 10))
        # Логотип словом, а не картинкой: PNG из макетов — растр под один
        # размер, а окно теперь тянется.
        tk.Label(head, text="JONY", font=ui_theme.display(19),
                 fg=ui_theme.TEXT, bg=ui_theme.CHASSIS).pack(side="left")

        # Служебные действия — справа в шапке, а не в центре экрана: они нужны
        # редко, и в центральной карточке отвлекали от состояния.
        for name, handler, tip in (
            ("Автозапуск", self._toggle_autostart, "Запускать Джони при входе в Windows"),
            ("Лог", self._open_log, "Открыть johnny.log"),
            ("История", self._open_history, "Открыть историю команд"),
        ):
            btn = ui_theme.Button(head, name, command=handler, variant="ghost", height=28)
            btn.pack(side="right", padx=(6, 0))
            _tooltip(btn, tip)

    def _build_scope(self):
        self._scope = ScopeWell(self, height=_WELL_H)
        self._scope.pack(fill="x", padx=24)
        device = self._settings.get("whisper_device", "cpu")
        model = self._settings.get("whisper_model", "medium")
        self._scope.set_engine(f"whisper:{model}/{device}")

    def _build_controls(self):
        row = tk.Frame(self, bg=ui_theme.CHASSIS)
        row.pack(fill="x", padx=24, pady=(12, 4))
        # Одна главная кнопка на экран — лаймовая. Всё остальное «ghost»:
        # раньше лаймовыми были и «Пауза», и «Сохранить», и семь кнопок
        # тулкитов, и глаз не понимал, что здесь главное.
        self._listen_btn = ui_theme.Button(row, "Пауза", command=self._toggle_pause,
                                           variant="primary", height=38, width=150)
        self._listen_btn.pack(side="left")
        for text, handler, tip in (
            ("⟳", self._restart, "Перезапустить Джони"),
            ("✕", self._quit, "Выйти"),
        ):
            icon = ui_theme.Button(row, text, command=handler, variant="icon",
                                   height=38, width=44, font=ui_theme.label(13))
            icon.pack(side="left", padx=(8, 0))
            _tooltip(icon, tip)

    def _build_tabs(self):
        holder = tk.Frame(self, bg=ui_theme.CHASSIS)
        holder.pack(fill="both", expand=True, padx=24, pady=(10, 0))

        names = ("ГОЛОС", "РАСПОЗНАВАНИЕ", "ТУЛКИТЫ")
        self._tab_strip = ui_theme.Tabs(holder, names, self._select_tab)
        self._tab_strip.pack(fill="x")
        ui_theme.hairline(holder).pack(fill="x")

        self._tab_body = tk.Frame(holder, bg=ui_theme.CHASSIS)
        self._tab_body.pack(fill="both", expand=True, pady=(14, 0))

        self._tabs["ГОЛОС"] = self._build_voice_tab()
        self._tabs["РАСПОЗНАВАНИЕ"] = self._build_hearing_tab()
        self._tabs["ТУЛКИТЫ"] = self._build_tools_tab()
        self._select_tab("ГОЛОС")

    def _select_tab(self, name):
        for tab_name, widget in self._tabs.items():
            if tab_name == name:
                widget.pack(fill="both", expand=True)
            else:
                widget.pack_forget()

    def _build_voice_tab(self):
        tab = tk.Frame(self._tab_body, bg=ui_theme.CHASSIS)
        left, right = self._two_columns(tab)
        self._combo(left, "tts_provider", "Провайдер TTS", ["fish", "edge", "local"], default="fish")
        self._slider(left, "tts_volume", "Громкость TTS", 0.0, 1.0, default=0.6)
        self._combo(left, "response_mode", "Режим ответа", ["voice", "beep", "off"], default="voice")
        self._entry(right, "tts_voice", "Голос Edge TTS", default="ru-RU-DmitryNeural")
        self._entry(right, "fish_model_id", "Fish Audio Model ID", default="")
        return tab

    def _build_hearing_tab(self):
        tab = tk.Frame(self._tab_body, bg=ui_theme.CHASSIS)
        left, right = self._two_columns(tab)
        self._combo(left, "whisper_model", "Модель Whisper", ["small", "medium", "large-v3"],
                    default="medium")
        self._combo(left, "whisper_device", "Устройство Whisper", ["cuda", "cpu"], default="cpu")
        self._entry(left, "wake_word", "Wake-word (через |)", default="джонни|джони|джани")
        self._entry(right, "vosk_model_path", "Путь к модели Vosk",
                    default="models/vosk-model-ru-0.42")
        # Groq правит услышанное, а не голос — поэтому он здесь, а не в «Голосе».
        self._combo(right, "groq_model", "Модель-корректор Groq", [
            "llama-3.3-70b-versatile",
            "llama-3.1-8b-instant",
            "mixtral-8x7b-32768",
            "gemma2-9b-it",
        ], default="llama-3.3-70b-versatile")
        return tab

    def _build_tools_tab(self):
        area = ScrollArea(self._tab_body)
        for tool in panel_tools.TOOLS:
            self._tool_card(area.body, tool)
        area.bind_wheel_deep(area.body)
        # Готовность спрашиваем в фоне: available() у переводчика и
        # social-analyzer импортирует пакет, а панель должна открыться сразу.
        threading.Thread(target=self._check_readiness, daemon=True).start()
        return area

    def _two_columns(self, parent):
        grid = tk.Frame(parent, bg=ui_theme.CHASSIS)
        grid.pack(fill="both", expand=True)
        grid.columnconfigure(0, weight=1, uniform="col")
        grid.columnconfigure(1, weight=1, uniform="col")
        left = tk.Frame(grid, bg=ui_theme.CHASSIS)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        right = tk.Frame(grid, bg=ui_theme.CHASSIS)
        right.grid(row=0, column=1, sticky="nsew", padx=(12, 0))
        return left, right

    def _build_bottom(self):
        bottom = tk.Frame(self, bg=ui_theme.CHASSIS)
        bottom.pack(side="bottom", fill="x", padx=24, pady=(10, 18))
        ui_theme.Button(bottom, "Сохранить", command=self._save,
                        variant="primary", height=34).pack(side="right")
        ui_theme.Button(bottom, "Отмена", command=self.destroy,
                        variant="ghost", height=34).pack(side="right", padx=(0, 8))
        tk.Label(bottom, text="config/settings.yaml", font=ui_theme.mono(8),
                 fg=ui_theme.TEXT_MUTE, bg=ui_theme.CHASSIS).pack(side="left")

    # ── Тулкиты ──────────────────────────────────────────────────────────────

    def _tool_card(self, parent, tool):
        """Карточка тулкита: заголовок, поле, кнопка, подсказка, ответ.

        Голосом то же самое доступно всегда; кнопки нужны там, где диктовать
        мучительно — ссылка на картинку и путь к файлу как раз такие.
        """
        # Рамка в 1px вместо заливки цветом: восемь ярких карточек подряд и
        # были той самой «аппликацией». Теперь карточка отделена гранью, а не
        # пятном.
        border = tk.Frame(parent, bg=ui_theme.EDGE_SOFT)
        border.pack(fill="x", pady=3)
        card = tk.Frame(border, bg=ui_theme.PANEL, padx=14, pady=9)
        card.pack(fill="x", padx=1, pady=1)

        head = tk.Frame(card, bg=ui_theme.PANEL)
        head.pack(fill="x")
        tk.Label(head, text=tool.title, font=ui_theme.display(11),
                 fg=ui_theme.TEXT, bg=ui_theme.PANEL).pack(side="left")
        state = tk.Label(head, text="проверяю…", font=ui_theme.mono(8),
                         fg=ui_theme.TEXT_MUTE, bg=ui_theme.PANEL)
        state.pack(side="right")

        row = tk.Frame(card, bg=ui_theme.PANEL)
        row.pack(fill="x", pady=(9, 0))
        var = tk.StringVar()
        if tool.kind == "pick":
            # Захват экрана/камеры: набирать нечего, файла ещё нет. Значение
            # ставим сразу — карточка отказывает на пустом поле, а тут пустым
            # оно быть и не может.
            var.set(tool.options[0][0])
            entry = self._well_option(row, var, [value for value, _ in tool.options])
            entry.pack(side="left", fill="x", expand=True)
        else:
            entry = self._well_entry(row, var)
            entry.pack(side="left", fill="x", expand=True)

        run_btn = ui_theme.Button(row, tool.button, variant="ghost", height=30,
                                  bg=ui_theme.PANEL,
                                  command=lambda t=tool: self._run_tool(t))
        if tool.kind in ("file", "files"):
            ui_theme.Button(row, "Обзор", variant="ghost", height=30, bg=ui_theme.PANEL,
                            command=lambda v=var, t=tool: self._pick_file(v, t.kind == "files")
                            ).pack(side="left", padx=(8, 0))
        run_btn.pack(side="left", padx=(8, 0))
        if tool.kind != "pick":
            # Enter в поле — то же, что кнопка: набрал ник, нажал Enter.
            # У списка набирать нечего, и Enter там открывает сам список.
            entry.bind("<Return>", lambda _e, t=tool: self._run_tool(t))

        tk.Label(card, text=tool.hint, font=ui_theme.body(8), fg=ui_theme.TEXT_MUTE,
                 bg=ui_theme.PANEL, anchor="w", justify="left").pack(fill="x", pady=(7, 0))
        answer = tk.Label(card, text="", font=ui_theme.body(), fg=ui_theme.TEXT,
                          bg=ui_theme.PANEL, anchor="w", justify="left", wraplength=640)
        answer.pack(fill="x", pady=(4, 0))

        # Ключ — action, а не коннектор: «что на картинке» и «прочитай текст»
        # живут на одном azure-vision, и по имени коннектора вторая карточка
        # затёрла бы первую — кнопка первой управляла бы полем второй.
        self._tool_widgets[tool.action] = {
            "var": var, "button": run_btn, "state": state, "answer": answer,
            "label": tool.button,
        }

    def _well_option(self, parent, var, values):
        """Выпадающий список в том же колодце, что и поле ввода.

        tk.OptionMenu, а не ttk.Combobox, хотя Combobox привычнее: кнопку со
        стрелкой у него рисует тема clam, и цвет ей задать нечем — на тёмной
        панели она остаётся светло-серым квадратом, единственным светлым
        пятном на экране. OptionMenu — обычный tk-виджет, красится целиком,
        включая само меню.
        """
        border = tk.Frame(parent, bg=ui_theme.EDGE)
        inner = tk.Frame(border, bg=ui_theme.WELL)
        inner.pack(padx=1, pady=1, fill="x")
        menu = tk.OptionMenu(inner, var, *values)
        # Родной индикатор OptionMenu — объёмная чёрточка в стиле Windows 95,
        # и в плоском виде он неотличим от текстового поля. Убираем его и
        # ставим свою «птичку»: без неё человек не видит, где можно выбрать,
        # а где напечатать.
        menu.configure(
            indicatoron=False,
            bg=ui_theme.WELL, fg=ui_theme.TEXT,
            activebackground=ui_theme.WELL, activeforeground=ui_theme.PHOSPHOR,
            bd=0, relief="flat", highlightthickness=0, anchor="w",
            font=ui_theme.body(10), padx=8, pady=5, cursor="hand2",
        )
        menu["menu"].configure(
            bg=ui_theme.PANEL, fg=ui_theme.TEXT, bd=0, relief="flat",
            activebackground=ui_theme.PHOSPHOR, activeforeground=ui_theme.CHASSIS,
            font=ui_theme.body(10),
        )
        chevron = tk.Label(inner, text="▾", bg=ui_theme.WELL, fg=ui_theme.TEXT_DIM,
                           font=ui_theme.body(9), padx=9, cursor="hand2")
        chevron.pack(side="right", fill="y")
        # По «птичке» тоже должно открываться: она визуально часть поля, и
        # промах по ней читался бы как «не нажалось».
        chevron.bind("<Button-1>", lambda _e: menu.event_generate("<Button-1>", x=6, y=6))
        menu.pack(side="left", fill="x", expand=True)
        return border

    def _well_entry(self, parent, var):
        """Поле ввода как утоплённый колодец: рамка 1px + тёмная заливка.

        tk.Entry сам рамку рисовать не умеет так, чтобы она была ровно в
        пиксель и нужного цвета, — поэтому рамкой служит фрейм под ним.
        """
        border = tk.Frame(parent, bg=ui_theme.EDGE)
        inner = tk.Frame(border, bg=ui_theme.WELL)
        inner.pack(padx=1, pady=1, fill="x")
        entry = tk.Entry(inner, textvariable=var, font=ui_theme.body(10),
                         bg=ui_theme.WELL, fg=ui_theme.TEXT,
                         insertbackground=ui_theme.PHOSPHOR, bd=0,
                         relief="flat", width=1)
        entry.pack(fill="x", padx=8, ipady=6)
        # Наружу отдаём рамку (её пакует вызывающий), а bind вешаем на Entry —
        # поэтому подменяем bind, чтобы вызывающему не нужно было знать про
        # внутреннее устройство.
        border.bind = entry.bind
        return border

    def _pick_file(self, var, append=False):
        path = filedialog.askopenfilename(
            parent=self,
            title="Выберите фото",
            filetypes=[("Изображения", "*.jpg *.jpeg *.png *.webp"), ("Все файлы", "*.*")],
        )
        if not path:
            return
        if not append:
            var.set(path)
            return
        # Пара снимков за два нажатия. Кавычки обязательны: разбор в
        # connector_action._two_paths делит фразу по слову «и» только когда
        # кавычек нет, а в пути к файлу «и» встречается постоянно.
        current = var.get().strip()
        var.set(f'{current} и "{path}"' if current else f'"{path}"')

    def _run_tool(self, tool):
        widgets = self._tool_widgets[tool.action]
        value = widgets["var"].get()
        widgets["button"].set_enabled(False)
        widgets["button"].set_text("…")
        widgets["answer"].config(text="")
        # Блокировка кнопки — не косметика: второй клик по Azure или Face++ это
        # второй списанный вызов из суточной квоты.

        def work():
            ok, message = panel_tools.run(tool, value, self._config)
            # Обратно в поток Tk через очередь: трогать виджеты из чужого потока
            # нельзя, и позвать after отсюда тоже нельзя — он сам вызов в Tcl.
            self._ui_queue.put(lambda: self._tool_done(tool, ok, message))

        threading.Thread(target=work, daemon=True).start()

    def _pump(self):
        """Разобрать очередь ответов из фоновых потоков. Только поток Tk.

        Хвост очереди при закрытии панели никого не волнует: показывать ответ
        уже некуда, а потоки и так daemon.
        """
        if not panel_tools.drain(self._ui_queue, stop_on=(tk.TclError,)):
            return
        try:
            self.after(120, self._pump)
        except tk.TclError:
            pass

    def _tool_done(self, tool, ok, message):
        widgets = self._tool_widgets.get(tool.action)
        if widgets is None:
            return
        try:
            widgets["button"].set_enabled(True)
            widgets["button"].set_text(widgets["label"])
            widgets["answer"].config(text=message,
                                     fg=ui_theme.TEXT if ok else ui_theme.DANGER)
        except tk.TclError:
            # Панель закрыли, пока тулкит работал — ответ просто некому показать.
            pass

    def _check_readiness(self):
        state = panel_tools.readiness(self._config)
        self._ui_queue.put(lambda: self._show_readiness(state))

    def _show_readiness(self, state):
        for action, (ready, why) in state.items():
            widgets = self._tool_widgets.get(action)
            if widgets is None:
                continue
            try:
                widgets["state"].config(
                    text="готов" if ready else "не настроен",
                    fg=ui_theme.PHOSPHOR_DIM if ready else ui_theme.WARN,
                )
                if not ready:
                    # Причина отказа — это и есть инструкция «что сделать»:
                    # какой ключ, какое согласие, какой пакет поставить.
                    widgets["answer"].config(text=why, fg=ui_theme.TEXT_DIM)
            except tk.TclError:
                pass

    # ── Виджеты-помощники ────────────────────────────────────────────────────

    def _field(self, parent, label):
        """Общая обвязка поля: подпись сверху, сам виджет под ней.

        Без карточки-подложки. Подложка на каждом поле давала ту самую
        «бумагу на бумаге»; здесь поля разделяет воздух, а группирует их
        колонка.
        """
        frame = tk.Frame(parent, bg=ui_theme.CHASSIS)
        frame.pack(fill="x", pady=(0, 16))
        tk.Label(frame, text=label.upper(), font=ui_theme.label(9),
                 fg=ui_theme.TEXT_DIM, bg=ui_theme.CHASSIS).pack(anchor="w", pady=(0, 6))
        return frame

    def _entry(self, parent, key, label, default=""):
        frame = self._field(parent, label)
        var = tk.StringVar(value=str(self._settings.get(key, default)))
        self._vars[key] = var
        self._well_entry(frame, var).pack(fill="x")

    def _combo(self, parent, key, label, values, default=""):
        frame = self._field(parent, label)
        var = tk.StringVar(value=str(self._settings.get(key, default)))
        self._vars[key] = var
        self._well_option(frame, var, values).pack(fill="x")

    def _slider(self, parent, key, label, min_val, max_val, default=0.5):
        frame = self._field(parent, label)
        current = float(self._settings.get(key, default))
        var = tk.DoubleVar(value=current)
        self._vars[key] = var
        # Значение цифрой рядом с подписью: без него ползунок показывает
        # «примерно две трети», а в settings.yaml уезжает точное число.
        value_label = tk.Label(frame, text=f"{current:.2f}", font=ui_theme.mono(9),
                               fg=ui_theme.PHOSPHOR, bg=ui_theme.CHASSIS)
        value_label.place(relx=1.0, y=0, anchor="ne")
        ui_theme.Slider(frame, var, min_val, max_val, on_change=lambda v: value_label.config(
            text=f"{v:.2f}")).pack(fill="x")

    # ── Сохранение ───────────────────────────────────────────────────────────

    def _save(self):
        for key, var in self._vars.items():
            val = var.get()
            # tts_volume хранится как float
            if key == "tts_volume":
                self._settings[key] = round(float(val), 2)
            else:
                self._settings[key] = val
        try:
            _save_settings(self._settings)
            logger.info("Настройки сохранены: %s", _SETTINGS_PATH)
        except Exception:
            logger.exception("Ошибка сохранения настроек")
        if self._on_save:
            self._on_save(self._settings)
        self.destroy()

    def _toggle_pause(self):
        if self._on_toggle_pause is not None:
            self._on_toggle_pause()
        self._update_pause_button()

    def _restart(self):
        if self._on_restart is not None:
            self._on_restart()
        else:
            self.destroy()
            os.startfile(str(_ROOT / "johnny_tray.pyw"))
            sys.exit(0)

    def _quit(self):
        if self._on_quit is not None:
            self._on_quit()
        else:
            sys.exit(0)

    def _refresh_status(self):
        last_command = self._get_last_command() if self._get_last_command is not None else ""
        try:
            # Пустая команда — прочерк, а не пустая строка: пустое место
            # читается как «сломалось», а не «пока ничего».
            self._scope.set_command(last_command or "—")
        except Exception:
            pass
        self._show_engine_status()
        self._update_pause_button()
        try:
            self.after(1000, self._refresh_status)
        except tk.TclError:
            pass

    def _animate(self):
        """Кадр волны. Свой таймер, а не общий с _refresh_status: статусу хватает
        раза в секунду, а пульсация на такой частоте выглядела бы поломкой.

        Останавливается сам, когда панель закрыли: after на разрушенном виджете
        бросает TclError, и продолжать цикл незачем — как в panel_tools.drain.
        """
        try:
            self._scope.draw(activity.snapshot())
            # Слово в колодце обновляем тем же кадром, что и волну: фаза
            # живёт секунды, и на односекундном таймере статуса надпись
            # отставала бы от собственной волны.
            self._apply_state()
        except tk.TclError:
            return
        except Exception:
            # Волна — украшение; если она падает, панель обязана остаться
            # рабочей. Один раз в лог и больше не рисуем.
            logger.exception("Визуализатор упал — отключаю анимацию")
            return
        try:
            self.after(_FPS_MS, self._animate)
        except tk.TclError:
            pass

    def _show_engine_status(self):
        """Строку состояния — в колодец. Раз в секунду."""
        if self._get_engines is None:
            # Панель открыли саму по себе (python -m johnny.panel): движков нет
            # и не было. Врать «слушает» тут нельзя — это ровно тот случай,
            # ради которого статус вообще переехал в UI.
            text, tone = "Джони не запущен", "idle"
        else:
            try:
                listener, recognizer = self._get_engines()
            except Exception:
                logger.exception("Не удалось узнать состояние движков")
                text, tone = "Состояние неизвестно", "warn"
            else:
                state = panel_tools.status(
                    listener=listener, recognizer=recognizer, paused=self._get_paused()
                )
                text, tone = state.text, state.tone
        # Грубый статус считается раз в секунду (он лезет к движкам), а живая
        # фаза меняется несколько раз за то же время. Поэтому здесь только
        # запоминаем, а показывает _apply_state — его зовёт каждый кадр.
        self._coarse = (text, tone)
        self._apply_state()

    def _apply_state(self):
        """Слово и цвет в колодце: грубый статус + живая фаза поверх него."""
        text, tone = self._coarse
        color = _TONE_COLOR.get(tone, _TONE_COLOR["idle"])
        # Живая фаза важнее грубого статуса, но ТОЛЬКО когда всё в порядке:
        # при «не запущен» или «на паузе» фазу писать нельзя, она осталась от
        # прошлой команды и соврала бы (см. _PHASE_LABEL).
        if tone == "ok":
            phase = activity.snapshot().phase
            label = _PHASE_LABEL.get(phase)
            if label is not None:
                text = label
                color = ui_theme.PHASE_COLOR.get(phase, color)
        self._scope.set_state(text, color)

    def _update_pause_button(self):
        """Одна кнопка на паузу и одна подпись — без «⏸/▶» рядом с текстом.

        Значок дублировал бы слово и на лаймовой плашке читался хуже него;
        состояние «на паузе» и так видно по погасшей волне (см. visualizer).
        """
        paused = self._get_paused()
        try:
            self._listen_btn.set_text("Слушать" if paused else "Пауза")
        except Exception:
            pass

    def _open_history(self):
        if self._on_open_history is not None:
            self._on_open_history()
            return
        from . import history_viewer

        history_path = _ROOT / "history.log"
        if not history_path.exists():
            history_path.write_text("", encoding="utf-8")
        history_viewer.open_or_focus(history_path)

    def _open_log(self):
        if self._on_open_log is not None:
            self._on_open_log()
            return
        os.startfile(str(_ROOT / "johnny.log"))

    def _toggle_autostart(self):
        if self._on_toggle_autostart is not None:
            self._on_toggle_autostart()
            return
        from . import autostart

        if autostart.is_installed():
            autostart.uninstall()
        else:
            pythonw = str(Path(sys.executable).with_name("pythonw.exe"))
            autostart.install(pythonw, str(_ROOT / "johnny_tray.pyw"), str(_ROOT))


def open_panel(
    master=None,
    on_save=None,
    on_toggle_pause=None,
    on_restart=None,
    on_quit=None,
    on_open_history=None,
    on_open_log=None,
    on_toggle_autostart=None,
    get_last_command=None,
    get_paused=None,
    get_engines=None,
):
    """Открыть панель управления. Если master=None — создаётся скрытый root."""
    if master is None:
        root = tk.Tk()
        root.withdraw()
        panel = SettingsPanel(
            root,
            on_save=on_save,
            on_toggle_pause=on_toggle_pause,
            on_restart=on_restart,
            on_quit=on_quit,
            on_open_history=on_open_history,
            on_open_log=on_open_log,
            on_toggle_autostart=on_toggle_autostart,
            get_last_command=get_last_command,
            get_paused=get_paused,
            get_engines=get_engines,
        )
        panel.protocol("WM_DELETE_WINDOW", lambda: (panel.destroy(), root.destroy()))
        root.mainloop()
    else:
        SettingsPanel(
            master,
            on_save=on_save,
            on_toggle_pause=on_toggle_pause,
            on_restart=on_restart,
            on_quit=on_quit,
            on_open_history=on_open_history,
            on_open_log=on_open_log,
            on_toggle_autostart=on_toggle_autostart,
            get_last_command=get_last_command,
            get_paused=get_paused,
            get_engines=get_engines,
        )
