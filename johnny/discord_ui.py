"""Чтение и нажатие элементов десктопного Discord через дерево доступности.

Четыре вещи, выясненные пробником на живом клиенте, и все четыре обязательны:

1. Окно ищется по ПРОЦЕССУ, а не по заголовку. Слово «Discord» бывает в
   заголовке чужих окон (терминал, браузер) — первая версия пробника нашла
   именно терминал.
2. Доступность в Chromium включается ЛЕНИВО: пока никто не спросил, дерева
   нет. Первый обход вернул 0 элементов, второй — 253.
3. У СВЁРНУТОГО окна дерева нет вообще (ровно 0 элементов): свёрнутое окно
   Chromium считает невидимым. Окно обязано быть развёрнуто.
4. У окна НЕ на переднем плане (стоит за другим окном, не свёрнуто) дерево
   отдаётся ОБРЕЗАННЫМ — например, 6-7 системных панелей без единого поля
   ввода или ссылки на переписку — и ожидание эту обрезку не лечит: 12
   замеров подряд за 5 секунд отдали те же 7 элементов. Значит «непустой
   список» ничего не доказывает; содержательным дерево считается, только
   когда в нём есть то, ради чего его читают (EditControl или
   HyperlinkControl, см. _tree_is_ready). Опрашиваем в цикле с дедлайном —
   не потому что обрезку это лечит (не лечит), а чтобы дать легитимной
   задержке между «окно спереди» и «Chromium построил дерево» случиться.
"""

import logging
import time

import uiautomation as auto
import win32api
import win32con
import win32gui
import win32process

logger = logging.getLogger(__name__)

# Глубина обхода. Дерево Discord — около 30 уровней; замер полного обхода 1.4 с.
_DEPTH = 30
# Опрос дерева на содержательность (см. пункт 4 в шапке модуля и
# _tree_is_ready). Пауза между попытками: полный обход стоит ~1.4 с сам по
# себе (см. _DEPTH), поэтому пауза между попытками короткая — время в
# основном уходит на сам обход, а не на сон.
_TREE_POLL_INTERVAL = 0.3
# Общий бюджет ожидания содержательного дерева. С запасом на 2-3 обхода по
# ~1.4 с плюс паузы между ними. НЕ лечит обрезку, вызванную отсутствием
# переднего плана (замерено: она не проходит и за 5 секунд) — только даёт
# время легитимной задержке «окно спереди → дерево построено».
_TREE_DEADLINE = 3.0
# Типы элементов, по которым дерево считается содержательным: поле ввода
# открытой переписки и ссылки в списке переписок (см. пункт 4 в шапке модуля).
_READY_KINDS = ("EditControl", "HyperlinkControl")
# Пауза после разворачивания окна: дереву нужно появиться.
_SHOW_PAUSE = 0.6
# Пауза после запроса переднего плана: смена активного окна не мгновенна,
# и сразу после вызова GetForegroundWindow ещё покажет прежнее окно.
_FOCUS_PAUSE = 0.4
# Пауза внутри приёма «свернуть → развернуть» (см. _minimize_restore_trick).
# Измерено пробником на этой машине: 0.05 с хватало в 4 из 4 попыток на
# постороннем окне (Блокнот) с ForegroundLockTimeout = 2147483647. Берём с
# запасом на менее отзывчивую машину.
_MINIMIZE_RESTORE_PAUSE = 0.15
# BOM (byte order mark), которым Chromium открывает содержимое пустого поля
# ввода Discord. Замерено на живом клиенте ТРЕМЯ способами (discord_ui.value,
# ValuePattern.Value, LegacyIAccessiblePattern.Value — все три согласны):
# пустое поле равно '﻿\n' (BOM + перевод строки), а НЕ ''. Сравнение
# value() с '' напрямую поэтому никогда не сработает на живом клиенте — см.
# normalize/is_blank.
_BOM = "﻿"
# Дедлайн ожидания настоящего клавиатурного фокуса поля ввода (см.
# wait_focused). Замерено на живом клиенте: в только что открытой переписке
# SetFocus() поля возвращает True, но набранный следом текст не долетает ни
# в поле, ни в переписку — HasKeyboardFocus в этот момент ещё False, хотя
# заголовок окна уже сменился на переписку. Опрашиваем в цикле, а не ждём
# один раз и не верим самому SetFocus().
_FOCUS_FIELD_DEADLINE = 2.0
# Пауза между повторными SetFocus()/опросами HasKeyboardFocus.
_FOCUS_FIELD_POLL = 0.1


def _process_name(hwnd) -> str:
    # Окно могло исчезнуть между перечислением и этим вызовом — защищаем.
    try:
        _tid, pid = win32process.GetWindowThreadProcessId(hwnd)
    except Exception:
        return ""
    try:
        handle = win32api.OpenProcess(
            win32con.PROCESS_QUERY_LIMITED_INFORMATION, False, pid
        )
    except Exception:
        return ""
    try:
        return win32process.GetModuleFileNameEx(handle, 0).rsplit("\\", 1)[-1].lower()
    except Exception:
        return ""
    finally:
        win32api.CloseHandle(handle)


def _area(hwnd) -> int:
    """Площадь окна в пикселях. 0 — размер не прочитался.

    У свёрнутого окна берём его РАЗВЁРНУТЫЙ прямоугольник: GetWindowRect
    отдаёт для свёрнутого угол (-32000, -32000) и крошечный размер, по
    которому главное окно проиграло бы любому оверлею.
    """
    try:
        if win32gui.IsIconic(hwnd):
            left, top, right, bottom = win32gui.GetWindowPlacement(hwnd)[4]
        else:
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    except Exception:
        return 0
    return max(0, right - left) * max(0, bottom - top)


def windows() -> list:
    """Видимые окна Discord, самое большое первым."""
    found = []

    def _cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd) and win32gui.GetWindowText(hwnd):
            if _process_name(hwnd) == "discord.exe":
                found.append(hwnd)

    win32gui.EnumWindows(_cb, None)
    return sorted(found, key=_area, reverse=True)


def window():
    """hwnd главного окна Discord. None, если клиент не запущен.

    Окон у Discord бывает несколько: внутриигровой оверлей, окно звонка, окно
    демонстрации экрана. Порядку перечисления доверять нельзя — в
    полноэкранной игре, ради которой всё и делалось, первым запросто окажется
    оверлей, дерево у него пустое, и Джони соврёт, что не нашёл человека.
    Поэтому берём самое большое по площади, а если дерева у него не оказалось —
    следующее по величине.

    Дерево спрашиваем, только когда окон больше одного: обход стоит около
    секунды, а при единственном окне выбирать всё равно не из чего.
    """
    found = windows()
    if len(found) < 2:
        return found[0] if found else None
    for hwnd in found:
        # У свёрнутого окна дерева нет по определению (см. шапку модуля) —
        # проверять его бессмысленно, оно развернётся перед чтением. У
        # остальных дерево обязано быть СОДЕРЖАТЕЛЬНЫМ (см. _tree_is_ready),
        # а не просто непустым — иначе обрезанное дерево (окно не на
        # переднем плане) сошло бы за главное окно, и человек в нём не
        # найдётся.
        if win32gui.IsIconic(hwnd) or _tree_is_ready(_elements(hwnd)):
            return hwnd
    return found[0]


def ensure_visible(hwnd) -> bool:
    """Развернуть окно, если оно свёрнуто. Возвращает, БЫЛО ли оно свёрнуто."""
    if hwnd is None:
        return False
    was_minimized = bool(win32gui.IsIconic(hwnd))
    if was_minimized:
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        time.sleep(_SHOW_PAUSE)
    return was_minimized


def is_foreground(hwnd) -> bool:
    """Окно сейчас на переднем плане (то есть получает ввод с клавиатуры)?"""
    if hwnd is None:
        return False
    try:
        return win32gui.GetForegroundWindow() == hwnd
    except Exception:
        return False


def foreground_window():
    """hwnd окна, которое сейчас на переднем плане. None — не прочиталось.

    Нужен, чтобы запомнить его ДО того, как Джони заберёт передний план под
    Discord, и вернуть обратно после отправки сообщения (см. restore_state).
    """
    try:
        return win32gui.GetForegroundWindow()
    except Exception:
        return None


def _attach_and_focus(hwnd) -> bool:
    """Захватить передний план в обход запрета Windows.

    Windows отдаёт передний план только процессу, который уже впереди или
    получил ввод от человека. Фоновому Джони она откажет молча. Обход
    стандартный: приклеиваемся к потоку ввода нынешнего переднего окна —
    тогда с точки зрения Windows это «оно само» уступает передний план.
    """
    attached = False
    foreground_tid = None
    our_tid = None
    try:
        foreground = win32gui.GetForegroundWindow()
        # Самый лёгкий случай: переднего окна нет вовсе (0) — на реальном
        # Windows GetWindowThreadProcessId(0) на это отвечает ошибкой, и
        # раньше вся функция падала здесь, даже не попробовав обычный
        # SetForegroundWindow. Приклеиваться в таком случае не к чему (нет
        # чужого потока ввода), но сам SetForegroundWindow попробовать всё
        # равно стоит — вдруг Windows его и так пропустит.
        if foreground:
            foreground_tid, _pid = win32process.GetWindowThreadProcessId(foreground)
            our_tid = win32api.GetCurrentThreadId()
            if foreground_tid != our_tid:
                attached = bool(win32process.AttachThreadInput(foreground_tid, our_tid, True))
        win32gui.SetForegroundWindow(hwnd)
        return True
    except Exception:
        return False
    finally:
        if attached:
            try:
                win32process.AttachThreadInput(foreground_tid, our_tid, False)
            except Exception:
                pass


def _was_maximized(hwnd) -> bool:
    """Было ли окно развёрнуто на весь экран до сворачивания."""
    try:
        return win32gui.GetWindowPlacement(hwnd)[1] == win32con.SW_SHOWMAXIMIZED
    except Exception:
        return False


def _minimize_restore_trick(hwnd) -> bool:
    """Последний приём: свернуть окно и тут же развернуть обратно.

    Замерено на этой машине: ForegroundLockTimeout стоит 2147483647
    (вечная блокировка переднего плана), из-за чего ни SetForegroundWindow,
    ни приём с AttachThreadInput передний план забрать не могут (Windows 11),
    а поставить SPI_SETFOREGROUNDLOCKTIMEOUT в 0 нельзя, не имея переднего
    плана — замкнутый круг. При этом ShowWindow(SW_MINIMIZE) сразу за
    ShowWindow(SW_RESTORE) Windows разрешает без отказа — этим же объясняется,
    почему старый код срабатывал только для свёрнутого Discord: ensure_visible
    делал SW_RESTORE, и он же активировал окно.

    У окна, развёрнутого на весь экран (максимизированного), SW_RESTORE может
    вернуть оконный режим вместо максимизированного, поэтому состояние
    запоминается заранее (GetWindowPlacement) и восстанавливается явным
    SW_SHOWMAXIMIZED, если окно было максимизировано.
    """
    was_maximized = _was_maximized(hwnd)
    try:
        win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)
        time.sleep(_MINIMIZE_RESTORE_PAUSE)
        win32gui.ShowWindow(
            hwnd, win32con.SW_SHOWMAXIMIZED if was_maximized else win32con.SW_RESTORE
        )
        time.sleep(_MINIMIZE_RESTORE_PAUSE)
    except Exception:
        return False
    return is_foreground(hwnd)


def focus(hwnd) -> bool:
    """Поднять окно на передний план. Возвращает, ПОЛУЧИЛОСЬ ли.

    Результат обязателен к проверке: печать идёт через SendInput, а он несёт
    ввод в окно с клавиатурным фокусом СИСТЕМЫ, а не в наш hwnd. Не подняли
    окно — печатать нельзя, текст уедет в чужое окно.

    Три попытки по нарастающей: обычный SetForegroundWindow, обход через
    AttachThreadInput, а если и он молча отказал (замерено: с вечным
    ForegroundLockTimeout на Windows 11 отказывают оба) — приём
    «свернуть-развернуть» (см. _minimize_restore_trick), который Windows не
    блокирует никогда.
    """
    if hwnd is None:
        return False
    if is_foreground(hwnd):
        return True
    try:
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        # Отказ Windows — не ошибка кода, а штатная защита переднего плана.
        _attach_and_focus(hwnd)
    time.sleep(_FOCUS_PAUSE)
    if is_foreground(hwnd):
        return True
    # Простой путь не сработал (или сработал вхолостую) — пробуем обход.
    _attach_and_focus(hwnd)
    time.sleep(_FOCUS_PAUSE)
    if is_foreground(hwnd):
        return True
    # И обход не сработал — последний приём, гарантированно не блокируемый.
    return _minimize_restore_trick(hwnd)


def value(control) -> str:
    """Текст внутри поля ввода как есть, без нормализации.

    ВАЖНО: у живого Discord пустое поле — НЕ пустая строка, а '﻿\\n' (BOM +
    перевод строки, см. _BOM). Сравнивать результат этой функции с '' для
    проверки «поле пусто» нельзя — пользуйтесь is_blank().

    Нужен, чтобы убедиться, что сообщение УШЛО: Discord очищает поле после
    отправки, и это единственное доступное подтверждение — SendInput кладёт
    события в очередь и об их доставке ничего не сообщает.
    """
    for read in (
        lambda: control.GetValuePattern().Value,
        lambda: control.GetLegacyIAccessiblePattern().Value,
    ):
        try:
            text = read()
        except Exception:
            continue
        if text:
            return str(text)
    return ""


def normalize(text: str) -> str:
    """Значение поля без шума: без BOM и без обрамляющих пробелов/переводов
    строк.

    Единая точка нормализации: весь код, который решает «поле пустое или
    непустое» либо сравнивает поле с напечатанным текстом, обязан проходить
    через неё (напрямую или через is_blank), а не сравнивать value() с ''
    самостоятельно — двух разных представлений пустоты в коде быть не
    должно.
    """
    return text.replace(_BOM, "").strip()


def is_blank(text: str) -> bool:
    """Поле пусто? Считает по normalize(), а не по сравнению с '' напрямую —
    у живого Discord пустое поле равно '﻿\\n', и наивное `value == ""`
    никогда не сработает (это и был дефект: подтверждение отправки ждало
    пустой строки, которая не наступает никогда, и Джони врал «отправить не
    смог» даже при успешной отправке).
    """
    return normalize(text) == ""


def wait_focused(control) -> bool:
    """Дождаться, что поле ввода ДЕЙСТВИТЕЛЬНО держит клавиатурный фокус.

    SetFocus() может честно вернуть True раньше, чем система в самом деле
    передаст полю фокус — замерено на живом клиенте: в только что открытой
    переписке SetFocus() = True, окно на переднем плане, заголовок уже
    сменился на переписку, а напечатанный следом текст не долетает ни в
    поле, ни в переписку. HasKeyboardFocus — единственное надёжное
    подтверждение; опрашиваем его в цикле с дедлайном, повторяя SetFocus()
    на каждой попытке (мог не удержаться с первого раза).
    """
    deadline = time.monotonic() + _FOCUS_FIELD_DEADLINE
    while True:
        try:
            control.SetFocus()
        except Exception:
            pass
        try:
            if control.HasKeyboardFocus:
                return True
        except Exception:
            pass
        if time.monotonic() >= deadline:
            return False
        time.sleep(_FOCUS_FIELD_POLL)


def restore_state(hwnd, was_minimized: bool, previous_foreground=None) -> None:
    """Вернуть всё как было после сообщения: свёрнутое окно — свернуть
    обратно, и прежнее переднее окно — снова сделать передним.

    previous_foreground — hwnd окна, которое было на переднем плане ДО того,
    как Джони забрал его под Discord (см. foreground_window(), вызывается
    вызывающим кодом в самом начале, пока Discord ещё не тронут). None или
    совпадение с hwnd самого Discord — восстанавливать нечего: значит, Джони
    никого не выдёргивал.

    Без этого возврата Discord оставался бы на переднем плане после каждого
    отправленного сообщения, даже если до команды человек играл в
    полноэкранную игру за другим окном, — пришлось бы возвращаться самому.
    Только для СООБЩЕНИЯ: звонок окно намеренно не возвращает, поэтому
    discord_call эту функцию вообще не вызывает.
    """
    if was_minimized:
        win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)
    if previous_foreground and previous_foreground != hwnd:
        if not focus(previous_foreground):
            logger.warning(
                "Не смог вернуть передний план прежнему окну (hwnd=%s) после "
                "отправки сообщения в Discord — Discord остался впереди",
                previous_foreground,
            )


def _walk(node, depth: int, out: list) -> None:
    if depth > _DEPTH:
        return
    try:
        children = node.GetChildren()
    except Exception:
        return
    for child in children:
        out.append(child)
        _walk(child, depth + 1, out)


def _tree_is_ready(elements: list) -> bool:
    """Дерево содержит то, ради чего его читают, а не просто «непусто».

    Обрезанное дерево (окно не на переднем плане) остаётся непустым — это
    несколько системных панелей без единого поля ввода или ссылки на
    переписку (замер на живом Discord: 6 PaneControl + TitleBar = 7
    элементов). Такое дерево бесполезно для find(), и «список непустой»
    не должно сходить за готовность.
    """
    for element in elements:
        try:
            kind = element.ControlTypeName
        except Exception:
            continue
        if kind in _READY_KINDS:
            return True
    return False


def _elements(hwnd) -> list:
    """Все элементы дерева. Опрашивается в цикле с дедлайном, пока дерево не
    станет содержательным (см. _tree_is_ready).

    Дедлайн истёк — возвращаем, что успели прочитать последним заходом (может
    быть пустым или обрезанным списком): вызывающий код (window()) сам решает,
    годится ли это, а не мы здесь ждём бесконечно.
    """
    deadline = time.monotonic() + _TREE_DEADLINE
    elements: list = []
    while True:
        try:
            root = auto.ControlFromHandle(hwnd)
        except Exception:
            # COM-исключение или невалидный hwnd — дерево недоступно.
            return []
        elements = []
        if root is not None:
            _walk(root, 0, elements)
        if _tree_is_ready(elements):
            return elements
        if time.monotonic() >= deadline:
            logger.debug(
                "Дерево Discord не стало содержательным за %.1f с (%d элементов)",
                _TREE_DEADLINE,
                len(elements),
            )
            return elements
        time.sleep(_TREE_POLL_INTERVAL)


def find(hwnd, kind: str, predicate):
    """Первый элемент нужного типа, чьё имя устраивает predicate. None — нет."""
    for element in _elements(hwnd):
        try:
            if element.ControlTypeName != kind:
                continue
            name = (element.Name or "").strip()
        except Exception:
            continue
        if name and predicate(name):
            return element
    return None


def invoke(control) -> bool:
    """Нажать элемент программно.

    Мышью не нажимаем принципиально: .Click() двигает настоящий курсор и
    выдернет мышь из-под руки. Сначала InvokePattern, затем действие по
    умолчанию — не все элементы Chromium отдают Invoke.
    """
    for attempt in (
        lambda: control.GetInvokePattern().Invoke(),
        lambda: control.GetLegacyIAccessiblePattern().DoDefaultAction(),
    ):
        try:
            attempt()
            return True
        except Exception:
            continue
    return False
