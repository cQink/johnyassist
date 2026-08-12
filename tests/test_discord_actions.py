import pytest

from johnny import actions, discord_ui, keyboard
from johnny.actions import discord_action as actions_discord
from johnny.router import RoutedAction

PEOPLE = {
    "гоша": {"discord": "Гречка", "username": "ne_godjaj", "aliases": ["гоша"]},
    "ярик": {"discord": "Грущенко", "username": "r1ealy", "aliases": ["ярик"]},
}


# Обработчики живут в johnny/actions/discord_action.py и вызываются только
# через реестр (см. actions/registry.py) — прямых функций
# actions.discord_message/discord_call больше нет. Тесты ходят тем же путём,
# что и app.py: RoutedAction → execute → реестр. Обёртки ниже держат
# читаемость вызовов, не заводя обратно фасадный API.

def discord_message(argument, people):
    return actions.execute(RoutedAction("discord_message", argument), apps={}, people=people)


def discord_call(argument, people):
    return actions.execute(RoutedAction("discord_call", argument), apps={}, people=people)


class FakeElement:
    def __init__(self, name, state):
        self.Name = name
        self._state = state
        # Сколько раз вызвали SetFocus() — по этому счётчику HasKeyboardFocus
        # решает, пришёл ли фокус (см. wait_focused и дефект 2: настоящий
        # SetFocus() может честно вернуть True раньше, чем система реально
        # передаст полю клавиатурный фокус).
        self.focus_calls = 0

    def SetFocus(self):
        # Настоящий SetFocus возвращает bool и умеет отказать — поддельный
        # обязан уметь то же самое, иначе провал фокуса нечем изобразить.
        self.focus_calls += 1
        return state_flag(self._state, "field_focus")

    @property
    def HasKeyboardFocus(self):
        # Единственное, чему верит wait_focused. "field_focus" = False —
        # фокус не приходит никогда. Иначе — приходит после стольких попыток,
        # сколько указано в "field_focus_after_polls" (по умолчанию — сразу).
        if not state_flag(self._state, "field_focus"):
            return False
        return self.focus_calls >= self._state.get("field_focus_after_polls", 1)


def state_flag(state, key):
    return bool(state[key])


@pytest.fixture
def discord(monkeypatch):
    """Поддельный Discord: дерево из пар (тип, имя) + журнал действий."""

    state = {
        "tree": [
            ("HyperlinkControl", "не прочитано, Гречка (личное сообщение)"),
            ("HyperlinkControl", "Грущенко (личное сообщение)"),
            ("EditControl", "Написать @Гречка"),
            ("ButtonControl", "Начать голосовой звонок"),
        ],
        "typed": [],
        "invoked": [],
        "minimized": False,
        "restored": [],
        # Какие hwnd'ы получили назад передний план через restore_state
        # (пункт B: окно, которое было впереди до команды, должно вернуться
        # после отправки сообщения — но не после звонка).
        "restored_foreground": [],
        # Порядок событий: подтверждение отправки обязано случиться ДО того,
        # как окно свернётся обратно.
        "log": [],
        # Разведены намеренно: focus() и is_foreground() — разные проверки в
        # разные моменты. Раньше один флаг «foreground» отвечал за оба сразу,
        # и сценарий «focus() удался, но пока открывали переписку, вперёд
        # вышло чужое окно» не проверялся — is_foreground() перед печатью не
        # мог соврать независимо от focus().
        "focus_result": True,   # что вернёт discord_ui.focus(hwnd)
        "is_foreground": True,  # что вернёт discord_ui.is_foreground(hwnd) перед печатью
        "field_focus": True,
        # Сколько попыток SetFocus() нужно, прежде чем HasKeyboardFocus
        # станет True. По умолчанию — фокус приходит сразу первой попыткой.
        "field_focus_after_polls": 1,
        # hwnd окна, которое было передним ДО команды (например, полноэкранная
        # игра). 999 — заведомо не совпадает с hwnd Discord (42).
        "previous_foreground": 999,
        # Что сейчас набрано в поле ввода. Discord очищает его после отправки,
        # по этому и узнаётся, что Enter доехал. Пустое поле живого клиента —
        # НЕ пустая строка, а BOM + перевод строки (замерено тремя способами,
        # см. discord_ui.value) — фикстура обязана изображать именно это,
        # иначе оба живых дефекта проскочат мимо тестов, как уже случилось.
        "field_text": "﻿\n",
        # False — Enter «не доехал»: поле остаётся непустым, как на загруженной
        # машине, где события ввода приходят позже, чем отработал код.
        "enter_arrives": True,
        # False — ValuePattern (и LegacyIAccessible) у поля недоступны вовсе:
        # value() всегда читает пустую строку, что бы ни было набрано на
        # самом деле (реальный Chromium вправе не отдавать ValuePattern у
        # contenteditable — так и написано в спецификации UI Automation).
        "value_readable": True,
    }

    def fake_type(text):
        state["typed"].append(text)
        state["field_text"] = text

    def fake_enter():
        state["typed"].append("<enter>")
        if state["enter_arrives"]:
            # Discord очищает поле не до пустой строки, а до того же маркера
            # BOM + перевод строки, которым отмечено пустое поле изначально.
            state["field_text"] = "﻿\n"

    def fake_value(control):
        state["log"].append("проверил поле")
        if not state["value_readable"]:
            return ""
        return state["field_text"]

    def fake_restore(hwnd, was, previous_foreground=None):
        state["log"].append("свернул обратно")
        state["restored"].append(was)
        if previous_foreground and previous_foreground != hwnd:
            state["restored_foreground"].append(previous_foreground)

    def fake_find(hwnd, kind, predicate):
        for element_kind, name in state["tree"]:
            if element_kind == kind and predicate(name):
                return FakeElement(name, state)
        return None

    monkeypatch.setattr(discord_ui, "window", lambda: 42)
    monkeypatch.setattr(discord_ui, "focus", lambda hwnd: state["focus_result"])
    monkeypatch.setattr(discord_ui, "is_foreground", lambda hwnd: state["is_foreground"])
    monkeypatch.setattr(discord_ui, "ensure_visible", lambda hwnd: state["minimized"])
    monkeypatch.setattr(discord_ui, "foreground_window", lambda: state["previous_foreground"])
    monkeypatch.setattr(discord_ui, "restore_state", fake_restore)
    monkeypatch.setattr(discord_ui, "find", fake_find)
    monkeypatch.setattr(
        discord_ui, "invoke", lambda control: state["invoked"].append(control.Name) or True
    )
    monkeypatch.setattr(discord_ui, "value", fake_value)
    monkeypatch.setattr(keyboard, "type_text", fake_type)
    monkeypatch.setattr(keyboard, "press_enter", fake_enter)
    monkeypatch.setattr(actions_discord, "_DISCORD_OPEN_DELAY", 0)
    monkeypatch.setattr(actions_discord, "_DISCORD_SENT_PAUSE", 0)
    monkeypatch.setattr(actions_discord, "_DISCORD_TYPED_PAUSE", 0)
    # Опрос HasKeyboardFocus (см. discord_ui.wait_focused, дефект 2): без
    # укороченного дедлайна тест на «фокус так и не пришёл» ждал бы полные
    # _FOCUS_FIELD_DEADLINE секунд по-настоящему.
    monkeypatch.setattr(discord_ui, "_FOCUS_FIELD_POLL", 0)
    monkeypatch.setattr(discord_ui, "_FOCUS_FIELD_DEADLINE", 0.05)
    return state


def test_сообщение_печатается_и_отправляется(discord):
    result = discord_message("гоше привет как дела", PEOPLE)

    assert result.ok
    assert discord["typed"] == ["привет как дела", "<enter>"]
    assert "не прочитано, Гречка (личное сообщение)" in discord["invoked"]


def test_незнакомому_человеку_не_пишем(discord):
    result = discord_message("пете привет", PEOPLE)

    assert not result.ok
    assert discord["typed"] == []


def test_человека_нет_в_списке_переписок(discord):
    discord["tree"] = [("EditControl", "Написать @Гречка")]

    result = discord_message("гоше привет", PEOPLE)

    assert not result.ok
    assert discord["typed"] == []


def test_чужое_поле_ввода_останавливает_отправку(discord):
    # Открылась переписка не с тем человеком: поле называет другого адресата
    discord["tree"] = [
        ("HyperlinkControl", "Гречка (личное сообщение)"),
        ("EditControl", "Написать @Грущенко"),
    ]

    result = discord_message("гоше привет", PEOPLE)

    assert not result.ok
    assert discord["typed"] == []


def test_без_переднего_плана_ничего_не_печатается(discord):
    # Discord не вышел вперёд: SendInput отдал бы текст и Enter тому окну,
    # в котором человек сейчас работает. Отказ строго лучше отправки.
    discord["focus_result"] = False

    result = discord_message("гоше привет", PEOPLE)

    assert not result.ok
    assert discord["typed"] == [], "печатать вслепую нельзя"


def test_без_курсора_в_поле_ничего_не_печатается(discord):
    # Окно впереди, но курсор в поле ввода не встал — текст ушёл бы мимо поля.
    discord["field_focus"] = False

    result = discord_message("гоше привет", PEOPLE)

    assert not result.ok
    assert discord["typed"] == []


def test_отказ_фокуса_возвращает_понятную_причину(discord):
    discord["focus_result"] = False

    result = discord_message("гоше привет", PEOPLE)

    assert "Discord" in result.message


def test_чужое_окно_вышло_вперёд_пока_открывали_переписку(discord):
    # focus() удался (Discord вышел вперёд ДО чтения дерева), но пока искали
    # человека и открывали переписку, вперёд успело выйти чужое окно —
    # последняя проверка is_foreground() перед печатью обязана это поймать
    # независимо от результата focus(). Раньше один флаг «foreground» отвечал
    # за оба сразу, и этот сценарий был неотличим от «focus() не удался».
    discord["focus_result"] = True
    discord["is_foreground"] = False

    result = discord_message("гоше привет", PEOPLE)

    assert not result.ok
    assert discord["typed"] == [], "печатать вслепую нельзя"
    assert "Discord" in result.message


def test_неушедшее_сообщение_не_выдаётся_за_успех(discord):
    # Enter не доехал (загруженная машина): поле осталось непустым. Раньше
    # Джони говорил «Отправил», а сообщение оставалось черновиком.
    discord["enter_arrives"] = False

    result = discord_message("гоше привет", PEOPLE)

    assert not result.ok
    assert "отправить" in result.message.lower()


def test_поле_вечно_нечитаемое_даёт_честный_отказ_а_не_молчаливый_успех(discord):
    # Раньше: если ValuePattern в принципе недоступен, code решал, что
    # проверить нечем, и молча объявлял успех. Живой замер (дефект 2) это
    # опроверг — поле ввода читается ВСЕГДА (см. discord_ui.is_blank и её
    # докстринг), поэтому «текст так и не появился» — больше не повод для
    # тихого допущения об успехе, а прямое доказательство, что печать не
    # долетела. Честный отказ, Enter не жмём.
    discord["value_readable"] = False

    result = discord_message("гоше привет", PEOPLE)

    assert not result.ok
    assert discord["typed"] == ["привет"], "Enter жать нельзя — текст не подтверждён"


def test_текст_не_появившийся_в_поле_после_печати_даёт_отказ_без_enter(discord, monkeypatch):
    # Дефект 2: сразу после открытия переписки печать иногда не долетает
    # вовсе (текст не появился ни в поле, ни в переписке), хотя окно и было
    # впереди. Раньше это было неотличимо от «ValuePattern недоступен» и
    # сходило за успех — по факту это была причина живой осечки. Теперь —
    # честный отказ до Enter, а не «набрал, но не смог отправить» постфактум.
    discord["value_readable"] = True

    def fake_type_nothing(text):
        discord["typed"].append(text)
        # печать не долетела — field_text остаётся тем же маркером пустого
        # поля, каким и было до печати ('﻿\n', см. фикстуру).

    monkeypatch.setattr(keyboard, "type_text", fake_type_nothing)

    result = discord_message("гоше привет", PEOPLE)

    assert not result.ok
    assert "набрать" in result.message.lower()
    assert discord["typed"] == ["привет"], "Enter жать нельзя — текст не долетел"


def test_фокус_приходит_не_сразу_но_отправка_всё_равно_успешна(discord):
    # Дефект 2: HasKeyboardFocus становится True не с первой попытки —
    # SetFocus() приходится повторять, прежде чем система реально передаст
    # полю клавиатурный фокус. Код обязан не сдаваться после первой неудачи
    # и в итоге всё равно набрать и отправить сообщение.
    discord["field_focus_after_polls"] = 3

    result = discord_message("гоше привет", PEOPLE)

    assert result.ok
    assert discord["typed"] == ["привет", "<enter>"]


def test_отправка_подтверждается_до_сворачивания(discord):
    # Свёрнутому окну Enter уже не дойдёт, поэтому подтверждение обязано
    # случиться раньше, чем окно вернётся в свёрнутое состояние. «проверил
    # поле» встречается дважды: снимок сразу после набора (до Enter) и сама
    # проверка внутри _wait_sent — оба раза до «свернул обратно».
    discord["minimized"] = True

    result = discord_message("гоше привет", PEOPLE)

    assert result.ok
    assert discord["log"] == ["проверил поле", "проверил поле", "свернул обратно"]


def test_подтверждение_повторяет_попытки(discord, monkeypatch):
    # Поле опустевает не мгновенно: одной проверки мало, нужно несколько.
    discord["enter_arrives"] = False
    calls = {"n": 0}
    fake_value = discord_ui.value

    def slow_value(control):
        calls["n"] += 1
        if calls["n"] >= 3:
            discord["field_text"] = ""
        return fake_value(control)

    monkeypatch.setattr(discord_ui, "value", slow_value)

    result = discord_message("гоше привет", PEOPLE)

    assert result.ok
    assert calls["n"] == 3, "должно быть несколько коротких попыток, а не одна проверка"


def test_снимок_набранного_ждёт_запаздывающее_поле(discord, monkeypatch):
    # Печать долетает не мгновенно: поле становится непустым не сразу после
    # keyboard.type_text(), а лишь через несколько обращений к
    # discord_ui.value() — та же гонка, что и с подтверждением после Enter,
    # но на этот раз при проверке набранного текста ДО Enter. Мгновенное
    # чтение поймало бы поле ещё в состоянии маркера пустоты ('﻿\n') и
    # ошибочно отказало бы — хотя поле на самом деле читается, просто не
    # сразу.
    def fake_type_delayed(text):
        discord["typed"].append(text)
        # field_text намеренно НЕ обновляется здесь — как будто событие
        # набора ещё не долетело до дерева доступности Discord.

    monkeypatch.setattr(keyboard, "type_text", fake_type_delayed)

    fake_value = discord_ui.value
    calls = {"n": 0}

    def slow_value(control):
        calls["n"] += 1
        if calls["n"] == 3 and discord_ui.is_blank(discord["field_text"]):
            discord["field_text"] = "привет"
        return fake_value(control)

    monkeypatch.setattr(discord_ui, "value", slow_value)

    result = discord_message("гоше привет", PEOPLE)

    assert result.ok
    assert discord["typed"][-1] == "<enter>", "сообщение всё же должно быть отправлено"
    assert calls["n"] >= 3, "проверка обязана ждать несколькими попытками, а не одним чтением"


def test_свёрнутое_окно_сворачивается_обратно(discord):
    discord["minimized"] = True

    discord_message("гоше привет", PEOPLE)

    assert discord["restored"] == [True]


def test_сообщение_возвращает_прежнее_окно_на_передний_план(discord):
    # До команды передним было чужое окно (например, игра) — Джони выдернул
    # его под Discord, и после отправки обязан вернуть как было.
    discord["previous_foreground"] = 777

    result = discord_message("гоше привет", PEOPLE)

    assert result.ok
    assert discord["restored_foreground"] == [777]


def test_сообщение_не_поднимает_окно_если_discord_и_так_был_передним(discord):
    # Discord и так стоял впереди до команды (previous_foreground == hwnd) —
    # восстанавливать нечего, лишнего focus() быть не должно.
    discord["previous_foreground"] = 42  # тот же hwnd, что и у Discord

    result = discord_message("гоше привет", PEOPLE)

    assert result.ok
    assert discord["restored_foreground"] == []


def test_звонок_не_возвращает_прежнее_окно_на_передний_план(discord):
    # Идёт разговор — окно Discord должно остаться на экране, а не
    # уступить передний план тому, что было впереди до звонка.
    discord["previous_foreground"] = 777

    result = discord_call("гоше", PEOPLE)

    assert result.ok
    assert discord["restored_foreground"] == []
    assert discord["restored"] == [], "restore_state вообще не должен вызываться для звонка"


def test_execute_доводит_людей_до_действия(discord):
    routed = RoutedAction(action="discord_message", argument="гоше привет")

    result = actions.execute(routed, apps={}, people=PEOPLE)

    assert result.ok
    assert discord["typed"] == ["привет", "<enter>"]


def test_звонок_нажимает_кнопку(discord):
    result = discord_call("гоше", PEOPLE)

    assert result.ok
    assert "Начать голосовой звонок" in discord["invoked"]


def test_звонок_незнакомому_не_проходит(discord):
    result = discord_call("пете", PEOPLE)

    assert not result.ok
    assert discord["invoked"] == []


def test_чужое_поле_ввода_останавливает_звонок(discord):
    # Ссылка в списке переписок совпала с нужным человеком, но открывшееся
    # поле ввода называет ДРУГОГО — значит, открылась не та переписка, и
    # звонить нельзя: в отличие от сообщения, звонок не отозвать.
    discord["tree"] = [
        ("HyperlinkControl", "Гречка (личное сообщение)"),
        ("EditControl", "Написать @Грущенко"),
        ("ButtonControl", "Начать голосовой звонок"),
    ]

    result = discord_call("гоше", PEOPLE)

    assert not result.ok
    assert "Начать голосовой звонок" not in discord["invoked"]


def test_звонок_без_переднего_плана_не_состоялся(discord):
    # Окно не свёрнуто, но стоит за другим (браузер, игра) — focus() должен
    # был бы вывести его вперёд и не смог. Без этого дерево обрезано, и
    # нажимать кнопку звонка вслепую нельзя: звонок не отозвать.
    discord["focus_result"] = False

    result = discord_call("гоше", PEOPLE)

    assert not result.ok
    assert discord["invoked"] == []
    assert "Discord" in result.message


def test_звонок_не_сворачивает_окно_обратно(discord):
    # Идёт разговор — окно должно остаться на экране
    discord["minimized"] = True

    discord_call("гоше", PEOPLE)

    assert discord["restored"] == []


def test_без_переднего_плана_переписка_даже_не_открывается(discord):
    # Отказ до нажатий: раз печатать всё равно не сможем, незачем открывать
    # человеку переписку и оставлять её открытой.
    discord["focus_result"] = False

    result = discord_message("гоше привет", PEOPLE)

    assert not result.ok
    assert discord["invoked"] == []


NO_DISCORD = {"петя": {"username": "petya", "aliases": ["петя"]}}


def test_человек_без_поля_discord_даёт_внятный_отказ(discord):
    # Опечатка в people.yaml раньше давала KeyError изнутри предиката find:
    # discord_ui.find ловит исключения только вокруг чтения свойств элемента,
    # поэтому Джони отвечал невнятное «Не смог выполнить команду».
    result = discord_message("пете привет", NO_DISCORD)

    assert not result.ok
    assert "people.yaml" in result.message
    assert discord["typed"] == []


def test_звонок_человеку_без_поля_discord_даёт_внятный_отказ(discord):
    result = discord_call("петя", NO_DISCORD)

    assert not result.ok
    assert "people.yaml" in result.message
    assert discord["invoked"] == []
