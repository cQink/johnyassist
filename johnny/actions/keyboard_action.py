"""Клавиатурные действия: ввод текста и нажатие Enter."""

from .registry import ActionResult, registry


@registry.register("type_text")
def action_type_text(argument: str, ctx: dict) -> ActionResult:
    """«Джони, ввод/вводи/введи <текст>» — печатает туда, где сейчас курсор
    ПОЛЬЗОВАТЕЛЯ (не Джони): Джони ничего не ищет и не переключает, SendInput
    несёт ввод в окно с системным клавиатурным фокусом (см. keyboard.py)."""
    from .. import keyboard

    keyboard.type_text(argument)
    return ActionResult(True, "Ввёл")


@registry.register("press_enter")
def action_press_enter(argument: str, ctx: dict) -> ActionResult:
    """«Джони, энтер/нажми энтер» — отдельно от «ввод» намеренно: распознавание
    иногда обрезает хвост фразы, объединять «ввод+Enter» рискованно."""
    from .. import keyboard

    keyboard.press_enter()
    return ActionResult(True, "Готово")
