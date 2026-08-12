"""Слой UI Automation на поддельном Windows.

Живой Discord тут не участвует: win32gui подменяется целиком, поэтому
проверяются ровно решения нашего кода — какое окно считать главным и когда
считать, что окно вышло на передний план.
"""

import pytest
import win32con

from johnny import discord_ui


class FakeControl:
    """Элемент дерева доступности — ровно то, что читает _tree_is_ready."""

    def __init__(self, control_type: str):
        self.ControlTypeName = control_type


class FakeWin32Gui:
    """Ровно те вызовы win32gui, которыми пользуется discord_ui."""

    def __init__(
        self, windows, rects, iconic=(), placements=None, foreground=None, show_cmd=None
    ):
        self.windows = windows
        self.rects = rects
        self.iconic = set(iconic)
        # Прямоугольники для GetWindowPlacement()[4] — нужны только свёрнутым
        # окнам в _area().
        self.placements = placements or {}
        # showCmd для GetWindowPlacement()[1] — по нему _was_maximized решает,
        # было ли окно развёрнуто на весь экран. По умолчанию — обычное.
        self.show_cmd = show_cmd or {}
        self.foreground = foreground
        self.focus_calls = []
        self.show_calls = []
        # Кому Windows разрешает захватить передний план. None — всем.
        self.allow_focus = None
        # Кому помогает приём «свернуть-развернуть». None — всем (обычная
        # ситуация: этот приём Windows не блокирует никогда).
        self.allow_minimize_restore = None

    def EnumWindows(self, callback, extra):
        for hwnd in self.windows:
            callback(hwnd, extra)

    def IsWindowVisible(self, hwnd):
        return True

    def GetWindowText(self, hwnd):
        return "Discord"

    def IsIconic(self, hwnd):
        return hwnd in self.iconic

    def GetWindowRect(self, hwnd):
        return self.rects[hwnd]

    def GetWindowPlacement(self, hwnd):
        show_cmd = self.show_cmd.get(hwnd, win32con.SW_SHOWNORMAL)
        rect = self.placements.get(hwnd, (0, 0, 0, 0))
        return (0, show_cmd, 0, (0, 0), rect)

    def GetForegroundWindow(self):
        return self.foreground

    def SetForegroundWindow(self, hwnd):
        self.focus_calls.append(hwnd)
        if self.allow_focus is not None and hwnd not in self.allow_focus:
            raise OSError("отказ Windows в захвате переднего плана")
        self.foreground = hwnd

    def ShowWindow(self, hwnd, cmd):
        # Приём «свернуть-развернуть» (_minimize_restore_trick): Windows его
        # не блокирует никогда — поэтому по умолчанию любой SW_RESTORE или
        # SW_SHOWMAXIMIZED переводит окно на передний план.
        self.show_calls.append((hwnd, cmd))
        if cmd == win32con.SW_MINIMIZE:
            self.iconic.add(hwnd)
        elif cmd in (win32con.SW_RESTORE, win32con.SW_SHOWMAXIMIZED):
            self.iconic.discard(hwnd)
            self.show_cmd[hwnd] = cmd
            if self.allow_minimize_restore is None or hwnd in self.allow_minimize_restore:
                self.foreground = hwnd


class FakeWin32Process:
    """Потоки для приёма с AttachThreadInput: настоящих здесь нет."""

    def __init__(self):
        self.attached = []

    def GetWindowThreadProcessId(self, hwnd):
        return 10, 20

    def AttachThreadInput(self, source, target, attach):
        self.attached.append((source, target, attach))
        return 1


class FakeWin32Api:
    def GetCurrentThreadId(self):
        return 11


@pytest.fixture(autouse=True)
def _no_pauses(monkeypatch):
    monkeypatch.setattr(discord_ui, "_FOCUS_PAUSE", 0)
    monkeypatch.setattr(discord_ui, "_MINIMIZE_RESTORE_PAUSE", 0)
    monkeypatch.setattr(discord_ui, "_TREE_POLL_INTERVAL", 0)


def _patch(monkeypatch, fake, trees=None):
    monkeypatch.setattr(discord_ui, "win32gui", fake)
    monkeypatch.setattr(discord_ui, "win32process", FakeWin32Process())
    monkeypatch.setattr(discord_ui, "win32api", FakeWin32Api())
    monkeypatch.setattr(discord_ui, "_process_name", lambda hwnd: "discord.exe")
    if trees is not None:
        monkeypatch.setattr(discord_ui, "_elements", lambda hwnd: trees.get(hwnd, []))


def test_окно_выбирается_по_площади_а_не_по_порядку(monkeypatch):
    # Оверлей перечисляется первым, но главное окно — большое.
    fake = FakeWin32Gui(
        windows=[1, 2],
        rects={1: (0, 0, 200, 100), 2: (0, 0, 1600, 900)},
    )
    _patch(monkeypatch, fake, trees={1: [], 2: [FakeControl("EditControl")]})

    assert discord_ui.window() == 2


def test_пустое_дерево_уступает_следующему_окну(monkeypatch):
    # Самое большое окно оказалось без дерева (демонстрация экрана на весь
    # монитор) — берём следующее по величине, а не сдаёмся.
    fake = FakeWin32Gui(
        windows=[1, 2],
        rects={1: (0, 0, 1920, 1080), 2: (0, 0, 1200, 800)},
    )
    _patch(monkeypatch, fake, trees={1: [], 2: [FakeControl("EditControl")]})

    assert discord_ui.window() == 2


def test_обрезанное_дерево_уступает_следующему_окну(monkeypatch):
    # Пункт D: окно не на переднем плане отдаёт НЕПУСТОЕ, но обрезанное
    # дерево (замер на живом Discord: 6 PaneControl + TitleBar = 7 элементов,
    # ни поля ввода, ни ссылок на переписки). Раньше «список непустой» само
    # по себе сходило за готовность, и запасной путь на следующее окно не
    # срабатывал — теперь обязан сработать.
    fake = FakeWin32Gui(
        windows=[1, 2],
        rects={1: (0, 0, 1920, 1080), 2: (0, 0, 1200, 800)},
    )
    truncated = [FakeControl("PaneControl") for _ in range(6)] + [FakeControl("TitleBarControl")]
    _patch(monkeypatch, fake, trees={1: truncated, 2: [FakeControl("EditControl")]})

    assert discord_ui.window() == 2


def test_свёрнутое_окно_меряется_развёрнутым_размером(monkeypatch):
    # У свёрнутого GetWindowRect отдаёт (-32000,…) — по нему главное окно
    # проиграло бы оверлею. Дерево у свёрнутого пустое всегда, спрашивать
    # его бессмысленно: оно развернётся перед чтением.
    fake = FakeWin32Gui(
        windows=[1, 2],
        rects={1: (0, 0, 400, 300), 2: (-32000, -32000, -31840, -31972)},
        iconic=[2],
        placements={2: (0, 0, 1600, 900)},
    )
    _patch(monkeypatch, fake, trees={1: [FakeControl("HyperlinkControl")], 2: []})

    assert discord_ui.window() == 2


def test_единственное_окно_отдаётся_без_чтения_дерева(monkeypatch):
    # Обход дерева стоит около секунды, а выбирать всё равно не из чего.
    fake = FakeWin32Gui(windows=[7], rects={7: (0, 0, 800, 600)})
    _patch(monkeypatch, fake)
    monkeypatch.setattr(
        discord_ui, "_elements", lambda hwnd: pytest.fail("дерево читать не должны")
    )

    assert discord_ui.window() == 7


def test_без_окон_discord_возвращается_none(monkeypatch):
    _patch(monkeypatch, FakeWin32Gui(windows=[], rects={}))

    assert discord_ui.window() is None


def test_tree_is_ready_обнаруживает_edit_control():
    assert discord_ui._tree_is_ready([FakeControl("EditControl")]) is True


def test_tree_is_ready_обнаруживает_hyperlink_control():
    assert discord_ui._tree_is_ready([FakeControl("HyperlinkControl")]) is True


def test_tree_is_ready_обрезанное_дерево_не_готово():
    # Замер на живом Discord за другим окном: 6 PaneControl + TitleBar.
    truncated = [FakeControl("PaneControl") for _ in range(6)] + [FakeControl("TitleBarControl")]
    assert discord_ui._tree_is_ready(truncated) is False


def test_tree_is_ready_пустой_список_не_готов():
    assert discord_ui._tree_is_ready([]) is False


def test_tree_is_ready_терпит_элемент_без_controltypename():
    class Broken:
        pass

    assert discord_ui._tree_is_ready([Broken()]) is False


class FakeRoot:
    """Корень дерева: у _walk он нужен только ради GetChildren()."""

    def __init__(self, children):
        self.children = children

    def GetChildren(self):
        return self.children


class FakeAutoModule:
    """Подмена uiautomation: ControlFromHandle зовёт переданный provider."""

    def __init__(self, provider):
        self._provider = provider

    def ControlFromHandle(self, hwnd):
        return self._provider()


def test_elements_опрашивает_пока_дерево_не_станет_содержательным(monkeypatch):
    # Первые попытки отдают обрезанное дерево (окно ещё не совсем спереди
    # или Chromium ещё не достроил дерево) — код обязан не сдаваться сразу.
    calls = {"n": 0}

    def provider():
        calls["n"] += 1
        if calls["n"] < 3:
            return FakeRoot([FakeControl("PaneControl")])
        return FakeRoot([FakeControl("EditControl")])

    monkeypatch.setattr(discord_ui, "auto", FakeAutoModule(provider))

    result = discord_ui._elements(1)

    assert calls["n"] == 3, "должно было потребоваться несколько попыток"
    assert any(el.ControlTypeName == "EditControl" for el in result)


def test_elements_возвращает_последнее_прочитанное_на_дедлайне(monkeypatch):
    # Дерево НИКОГДА не становится содержательным (окно так и не вышло
    # вперёд) — код обязан сдаться по дедлайну, а не ждать бесконечно, и
    # вернуть последнее прочитанное (пусть и обрезанное), а не пусто.
    monkeypatch.setattr(discord_ui, "_TREE_DEADLINE", 0.05)

    def provider():
        return FakeRoot([FakeControl("PaneControl")])

    monkeypatch.setattr(discord_ui, "auto", FakeAutoModule(provider))

    result = discord_ui._elements(1)

    assert len(result) == 1
    assert result[0].ControlTypeName == "PaneControl"


def test_is_foreground_сравнивает_с_передним_окном(monkeypatch):
    fake = FakeWin32Gui(windows=[1], rects={1: (0, 0, 10, 10)}, foreground=1)
    _patch(monkeypatch, fake)

    assert discord_ui.is_foreground(1) is True
    assert discord_ui.is_foreground(2) is False
    assert discord_ui.is_foreground(None) is False


def test_focus_поднимает_окно_вперёд(monkeypatch):
    fake = FakeWin32Gui(windows=[1], rects={1: (0, 0, 10, 10)}, foreground=99)
    _patch(monkeypatch, fake)

    assert discord_ui.focus(1) is True
    assert fake.focus_calls == [1]


def test_focus_не_дёргает_окно_которое_и_так_впереди(monkeypatch):
    fake = FakeWin32Gui(windows=[1], rects={1: (0, 0, 10, 10)}, foreground=1)
    _patch(monkeypatch, fake)

    assert discord_ui.focus(1) is True
    assert fake.focus_calls == []


def test_focus_возвращает_false_если_ничего_не_помогло(monkeypatch):
    # Windows отказывает фоновому процессу в захвате переднего плана и в
    # обходе с AttachThreadInput, и даже приём «свернуть-развернуть» не
    # переводит окно вперёд (гипотетический предельный случай) — честный
    # False, по которому вызывающий код откажется печатать вслепую.
    fake = FakeWin32Gui(windows=[1], rects={1: (0, 0, 10, 10)}, foreground=99)
    fake.allow_focus = set()
    fake.allow_minimize_restore = set()
    _patch(monkeypatch, fake)

    assert discord_ui.focus(1) is False


def test_focus_использует_приём_свернуть_развернуть_когда_остальное_не_помогло(monkeypatch):
    # Ровно сценарий блокера: ForegroundLockTimeout вечный, SetForegroundWindow
    # и AttachThreadInput бессильны, но «свернуть-развернуть» Windows не
    # блокирует никогда — окно обязано выйти вперёд именно этим приёмом.
    fake = FakeWin32Gui(windows=[1], rects={1: (0, 0, 10, 10)}, foreground=99)
    fake.allow_focus = set()
    _patch(monkeypatch, fake)

    assert discord_ui.focus(1) is True
    assert (1, win32con.SW_MINIMIZE) in fake.show_calls
    assert (1, win32con.SW_RESTORE) in fake.show_calls


def test_приём_свернуть_развернуть_восстанавливает_максимизированное_окно(monkeypatch):
    # У окна, развёрнутого на весь экран, наивный SW_RESTORE увёл бы его в
    # оконный режим. Запомненное заранее состояние обязано вернуть именно
    # SW_SHOWMAXIMIZED, а не SW_RESTORE.
    fake = FakeWin32Gui(
        windows=[1],
        rects={1: (0, 0, 10, 10)},
        foreground=99,
        show_cmd={1: win32con.SW_SHOWMAXIMIZED},
    )
    fake.allow_focus = set()
    _patch(monkeypatch, fake)

    assert discord_ui.focus(1) is True
    assert (1, win32con.SW_MINIMIZE) in fake.show_calls
    assert (1, win32con.SW_SHOWMAXIMIZED) in fake.show_calls
    assert (1, win32con.SW_RESTORE) not in fake.show_calls


def test_focus_пробует_обход_после_молчаливого_отказа(monkeypatch):
    # SetForegroundWindow не бросает исключение, а просто ничего не делает —
    # самый частый вид отказа. Проверяем, что после него код не сдаётся сразу,
    # а пробует ещё раз через AttachThreadInput. Приём «свернуть-развернуть»
    # тут намеренно тоже заблокирован — иначе он забрал бы передний план сам,
    # и повторный заход через AttachThreadInput стало бы нечем отличить.
    fake = FakeWin32Gui(windows=[1], rects={1: (0, 0, 10, 10)}, foreground=99)
    fake.allow_minimize_restore = set()

    def silent(hwnd):
        fake.focus_calls.append(hwnd)

    monkeypatch.setattr(fake, "SetForegroundWindow", silent)
    _patch(monkeypatch, fake)

    assert discord_ui.focus(1) is False
    assert len(fake.focus_calls) > 1, "после молчаливого отказа нужен второй заход"


def test_attach_and_focus_без_переднего_окна_пробует_обычный_setforeground(monkeypatch):
    # GetForegroundWindow() == 0 — переднего окна нет вовсе (самый лёгкий
    # случай). На реальном Windows GetWindowThreadProcessId(0) на это отвечает
    # ошибкой — раньше вся функция падала здесь и возвращала False, даже не
    # попробовав обычный SetForegroundWindow.
    fake = FakeWin32Gui(windows=[1], rects={1: (0, 0, 10, 10)}, foreground=0)
    _patch(monkeypatch, fake)

    def boom(hwnd):
        raise OSError("GetWindowThreadProcessId(0) — на реальном Windows это ошибка")

    monkeypatch.setattr(discord_ui.win32process, "GetWindowThreadProcessId", boom)

    assert discord_ui._attach_and_focus(1) is True
    assert fake.focus_calls == [1]


class FakeValue:
    def __init__(self, value):
        self.Value = value


def test_value_читает_текст_поля():
    class Control:
        def GetValuePattern(self):
            return FakeValue("привет")

    assert discord_ui.value(Control()) == "привет"


def test_value_пустого_поля_пустая_строка():
    class Control:
        def GetValuePattern(self):
            return FakeValue("")

        def GetLegacyIAccessiblePattern(self):
            return FakeValue("")

    assert discord_ui.value(Control()) == ""


def test_value_терпит_элемент_без_паттернов():
    class Control:
        pass

    assert discord_ui.value(Control()) == ""


def test_normalize_убирает_bom_и_обрамляющие_пробелы():
    # Живой замер (см. шапку discord_ui.value): пустое поле живого Discord
    # равно '﻿\n' (BOM + перевод строки), а не ''.
    assert discord_ui.normalize("﻿привет\n") == "привет"


def test_normalize_пустую_строку_не_портит():
    assert discord_ui.normalize("") == ""


def test_is_blank_bom_маркер_живого_discord_считается_пустым():
    # Дефект 1: сравнение с '' напрямую никогда не сработает на живом
    # клиенте — пустое поле там равно '﻿\n'.
    assert discord_ui.is_blank("﻿\n") is True


def test_is_blank_пустая_строка_тоже_пуста():
    assert discord_ui.is_blank("") is True


def test_is_blank_непустой_текст_не_пуст():
    assert discord_ui.is_blank("﻿привет\n") is False


class FakeFocusControl:
    """Элемент с SetFocus()/HasKeyboardFocus — то, чем пользуется wait_focused.

    HasKeyboardFocus — отдельное от SetFocus() состояние: настоящий SetFocus()
    может честно вернуть True раньше, чем система реально передаст полю
    клавиатурный фокус (замерено на живом клиенте — см. wait_focused).
    """

    def __init__(self, focus_after: int = 1, never: bool = False):
        self.focus_calls = 0
        self._focus_after = focus_after
        self._never = never

    def SetFocus(self):
        self.focus_calls += 1
        return True

    @property
    def HasKeyboardFocus(self):
        if self._never:
            return False
        return self.focus_calls >= self._focus_after


def test_wait_focused_подтверждает_с_первой_попытки(monkeypatch):
    monkeypatch.setattr(discord_ui, "_FOCUS_FIELD_POLL", 0)
    control = FakeFocusControl(focus_after=1)

    assert discord_ui.wait_focused(control) is True
    assert control.focus_calls == 1


def test_wait_focused_повторяет_setfocus_пока_фокус_не_придёт(monkeypatch):
    # Ровно сценарий дефекта 2: фокус приходит не сразу, а через несколько
    # опросов — код обязан не сдаваться после первой неудачи.
    monkeypatch.setattr(discord_ui, "_FOCUS_FIELD_POLL", 0)
    control = FakeFocusControl(focus_after=3)

    assert discord_ui.wait_focused(control) is True
    assert control.focus_calls == 3


def test_wait_focused_сдаётся_по_дедлайну_если_фокус_так_и_не_пришёл(monkeypatch):
    monkeypatch.setattr(discord_ui, "_FOCUS_FIELD_POLL", 0)
    monkeypatch.setattr(discord_ui, "_FOCUS_FIELD_DEADLINE", 0.05)
    control = FakeFocusControl(never=True)

    assert discord_ui.wait_focused(control) is False
    assert control.focus_calls > 1, "должно быть несколько попыток, а не одна"


def test_foreground_window_читает_переднее_окно(monkeypatch):
    fake = FakeWin32Gui(windows=[1], rects={1: (0, 0, 10, 10)}, foreground=7)
    _patch(monkeypatch, fake)

    assert discord_ui.foreground_window() == 7


def test_restore_state_сворачивает_и_возвращает_прежнее_окно(monkeypatch):
    # Discord (hwnd=1) свернули обратно, а окно, которое было впереди до
    # команды (hwnd=2, например игра), обязано вернуться на передний план.
    fake = FakeWin32Gui(windows=[1, 2], rects={1: (0, 0, 10, 10), 2: (0, 0, 10, 10)}, foreground=1)
    _patch(monkeypatch, fake)

    discord_ui.restore_state(1, was_minimized=True, previous_foreground=2)

    assert 1 in fake.iconic, "Discord обязан свернуться обратно"
    assert fake.foreground == 2, "прежнее окно обязано вернуться на передний план"


def test_restore_state_без_прежнего_окна_ничего_не_поднимает(monkeypatch):
    fake = FakeWin32Gui(windows=[1], rects={1: (0, 0, 10, 10)}, foreground=1)
    _patch(monkeypatch, fake)

    discord_ui.restore_state(1, was_minimized=False, previous_foreground=None)

    assert fake.focus_calls == []
    assert fake.show_calls == []


def test_restore_state_совпадение_с_discord_ничего_не_поднимает(monkeypatch):
    # Discord и так был передним до команды — восстанавливать нечего.
    fake = FakeWin32Gui(windows=[1], rects={1: (0, 0, 10, 10)}, foreground=1)
    _patch(monkeypatch, fake)

    discord_ui.restore_state(1, was_minimized=False, previous_foreground=1)

    assert fake.focus_calls == []
    assert fake.show_calls == []


def test_restore_state_логирует_неудачу_возврата_фокуса(monkeypatch, caplog):
    # Если вернуть передний план прежнему окну не удалось (Windows отказала
    # во всех приёмах — гипотетический предельный случай, см.
    # test_focus_возвращает_false_если_ничего_не_помогло), Discord молча
    # остаётся впереди. Без записи в лог это никак не отследить, если
    # человек потом пожалуется.
    fake = FakeWin32Gui(windows=[1, 2], rects={1: (0, 0, 10, 10), 2: (0, 0, 10, 10)}, foreground=1)
    fake.allow_focus = set()
    fake.allow_minimize_restore = set()
    _patch(monkeypatch, fake)

    with caplog.at_level("WARNING", logger="johnny.discord_ui"):
        discord_ui.restore_state(1, was_minimized=False, previous_foreground=2)

    assert any(
        "2" in record.message and "передний план" in record.message
        for record in caplog.records
    ), "неудача возврата фокуса обязана попасть в лог"
