"""Discord-действия: отправка сообщений и голосовые звонки."""

import time

from .registry import ActionResult, registry

# Сколько ждать открытия переписки после нажатия на неё в списке.
_DISCORD_OPEN_DELAY = 1.0
# Подтверждение отправки: сколько раз спрашиваем поле ввода, опустело ли оно
# (см. discord_ui.is_blank — пустое поле живого клиента равно '\ufeff\n', а не
# ''), и пауза между вопросами. События ввода уходят в очередь асинхронно, и о
# доставке SendInput не сообщает — на загруженной машине Enter доедет позже,
# чем отработает следующая строка. Пока поле не опустело, сообщение не ушло:
# объявлять успех и сворачивать окно нельзя. Несколько коротких попыток, а не
# одна длинная пауза, — обычная отправка подтверждается на первой же.
_DISCORD_SENT_ATTEMPTS = 6
_DISCORD_SENT_PAUSE = 0.25
# Подтверждение печати (см. _typed_landed): та же асинхронность очереди
# ввода бьёт и по печати, не только по Enter — keyboard.type_text() отдаёт
# управление раньше, чем Discord успевает обработать события и обновить
# дерево доступности. Мгновенное чтение сразу после набора рискует поймать
# поле ещё не обновившимся, хотя текст на самом деле напечатался. Несколько
# коротких попыток вместо одной мгновенной. Живой замер доказал, что поле
# ввода читается всегда (см. discord_ui.value и is_blank) — поэтому, если
# текст так и не появился за все попытки, это больше не «непонятно, читается
# ли поле», а прямое доказательство: печать не долетела, и жать Enter
# вслепую нельзя (дефект 2 — печать сразу после открытия переписки не всегда
# долетает, потому что клавиатурный фокус приходит с задержкой).
_DISCORD_TYPED_ATTEMPTS = 6
_DISCORD_TYPED_PAUSE = 0.25


def _open_dm(hwnd, person: dict) -> bool:
    """Открыть личную переписку с человеком, нажав её в боковом списке."""
    from .. import contacts, discord_ui

    link = discord_ui.find(
        hwnd, "HyperlinkControl", lambda name: contacts.matches_dm(name, person)
    )
    if link is None:
        return False
    if not discord_ui.invoke(link):
        return False
    time.sleep(_DISCORD_OPEN_DELAY)
    return True


def _typed_landed(field, text: str) -> bool:
    """Убедиться, что напечатанный текст ДЕЙСТВИТЕЛЬНО появился в поле.

    Несколько коротких попыток, а не одно мгновенное чтение сразу после
    keyboard.type_text(): события ввода уходят в очередь асинхронно, и на
    загруженной машине Discord успевает обработать их и обновить дерево
    доступности не сразу. Обычная печать подтверждается уже на первой
    попытке, без сна; сон нужен только когда дерево реально отстаёт.

    Сравниваем именно с тем, что печатали (через discord_ui.normalize, чтобы
    не споткнуться о BOM пустого поля), а не проверяем «поле хоть что-то
    показывает»: живой замер (дефект 2) доказал, что сразу после открытия
    переписки печать иногда не долетает вовсе, хотя окно и было впереди, — и
    отличить это от успешной печати можно только прямым сравнением.
    """
    from .. import discord_ui

    for _ in range(_DISCORD_TYPED_ATTEMPTS):
        if discord_ui.normalize(discord_ui.value(field)) == text:
            return True
        time.sleep(_DISCORD_TYPED_PAUSE)
    return False


def _wait_sent(field) -> bool:
    """Дождаться, пока поле ввода опустеет после Enter. False — так и не опустело.

    К этому моменту уже доказано (см. _typed_landed, вызывается до Enter),
    что discord_ui.value() у этого поля реально читает набранный текст —
    значит, вопрос «а вдруг ValuePattern вообще недоступен» здесь больше не
    стоит. Если поле не опустело за отведённые попытки, это прямое
    доказательство, что Enter не доставлен, а не гадание про недоступный
    паттерн.
    """
    from .. import discord_ui

    for _ in range(_DISCORD_SENT_ATTEMPTS):
        time.sleep(_DISCORD_SENT_PAUSE)
        if discord_ui.is_blank(discord_ui.value(field)):
            return True
    return False


@registry.register("discord_message")
def action_discord_message(argument: str, ctx: dict) -> ActionResult:
    """«гоше привет как дела» — открыть переписку и отправить сообщение."""
    from .. import contacts, discord_ui, keyboard

    people = ctx.get("people") or {}
    split = contacts.split_message(argument, people)
    if split is None:
        return ActionResult(False, "Не понял, кому писать")
    person, text = split
    # Имя в Discord берём один раз и через contacts: опечатка в people.yaml
    # иначе прилетела бы KeyError изнутри предиката find, и Джони сказал бы
    # невнятное «Не смог выполнить команду» вместо понятной причины.
    discord_name = contacts.discord_name(person)
    if not discord_name:
        return ActionResult(
            False, "В people.yaml у этого человека не указано имя в Discord"
        )
    hwnd = discord_ui.window()
    if hwnd is None:
        return ActionResult(False, "Discord не запущен")
    # Запоминаем ДО того, как тронули Discord: иначе после focus(hwnd) окно,
    # которое было впереди (игра, браузер), уже не узнать — оно сменилось.
    previous_foreground = discord_ui.foreground_window()
    was_minimized = discord_ui.ensure_visible(hwnd)
    try:
        # Окно выводится вперёд ДО чтения дерева, а не только перед печатью.
        if not discord_ui.focus(hwnd):
            return ActionResult(False, "Discord не выходит вперёд, вслепую печатать не буду")
        if not _open_dm(hwnd, person):
            return ActionResult(False, f"Не нашёл {discord_name} в переписках")
        # Поле ввода само называет адресата («Написать @Гречка») — сверяем с
        # тем, кого назвали.
        field = discord_ui.find(
            hwnd, "EditControl", lambda name: contacts.matches_input(name, person)
        )
        if field is None:
            return ActionResult(False, "Не нашёл поле ввода нужной переписки")
        if not discord_ui.wait_focused(field):
            return ActionResult(False, "Не смог поставить курсор в поле ввода")
        # Последняя сверка переднего плана.
        if not discord_ui.is_foreground(hwnd):
            return ActionResult(False, "Впереди не Discord, вслепую печатать не буду")
        keyboard.type_text(text)
        if not _typed_landed(field, text):
            return ActionResult(False, "Не смог набрать сообщение")
        keyboard.press_enter()
        if not _wait_sent(field):
            return ActionResult(False, "Набрал сообщение, но отправить не смог")
        return ActionResult(True, f"Отправил {discord_name}: {text}")
    finally:
        discord_ui.restore_state(hwnd, was_minimized, previous_foreground)


@registry.register("discord_call")
def action_discord_call(argument: str, ctx: dict) -> ActionResult:
    """«гоше» — открыть переписку и начать голосовой звонок.

    Окно НЕ возвращается в свёрнутое состояние, в отличие от сообщения:
    идёт разговор, его надо видеть.
    """
    from .. import contacts, discord_ui

    people = ctx.get("people") or {}
    person = contacts.resolve(argument, people)
    if person is None:
        return ActionResult(False, "Не знаю такого человека")
    discord_name = contacts.discord_name(person)
    if not discord_name:
        return ActionResult(
            False, "В people.yaml у этого человека не указано имя в Discord"
        )
    hwnd = discord_ui.window()
    if hwnd is None:
        return ActionResult(False, "Discord не запущен")
    discord_ui.ensure_visible(hwnd)
    if not discord_ui.focus(hwnd):
        return ActionResult(False, "Discord не выходит вперёд, вслепую звонить не буду")
    if not _open_dm(hwnd, person):
        return ActionResult(False, f"Не нашёл {discord_name} в переписках")
    field = discord_ui.find(
        hwnd, "EditControl", lambda name: contacts.matches_input(name, person)
    )
    if field is None:
        return ActionResult(False, "Не нашёл поле ввода нужной переписки")
    button = discord_ui.find(
        hwnd, "ButtonControl", lambda name: name == "Начать голосовой звонок"
    )
    if button is None:
        return ActionResult(False, "Не нашёл кнопку звонка")
    if not discord_ui.invoke(button):
        return ActionResult(False, "Не смог нажать кнопку звонка")
    return ActionResult(True, f"Звоню {discord_name}")
