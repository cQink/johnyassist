"""Действия с памятью: запомнить, забыть, поправить, перечислить факты."""

import logging

from .registry import ActionResult, registry

logger = logging.getLogger(__name__)


@registry.register("remember")
def action_remember(argument: str, ctx: dict) -> ActionResult:
    from .. import memory

    memory.remember(argument)
    return ActionResult(True, "Запомнил")


@registry.register("forget")
def action_forget(argument: str, ctx: dict) -> ActionResult:
    from .. import memory

    removed = memory.forget(argument)
    if removed is not None:
        # Проговариваем сам удалённый факт (а не голое «Забыл»): forget()
        # находит его по доле пересечения слов, и на коротких/стоп-
        # словных запросах может задеть не тот — иначе заметить это
        # нечем ни на слух, ни в логах.
        logger.info("Забыт факт: %r (запрос: %r)", removed, argument)
        return ActionResult(True, f"Забыл: {removed}")
    return ActionResult(False, "Не нашёл такое в памяти")


@registry.register("list_memory")
def action_list_memory(argument: str, ctx: dict) -> ActionResult:
    from .. import memory

    facts = memory.list_facts()
    if not facts:
        return ActionResult(True, "Я пока ничего не помню")
    return ActionResult(True, "Вот что я помню: " + "; ".join(facts))


@registry.register("update_fact")
def action_update_fact(argument: str, ctx: dict) -> ActionResult:
    """«поправь стим-аккаунт на art_vol_teror» — заменить факт одной командой.

    Без этого правка требовала двух команд («забудь» + «запомни»), и если
    вторая не доезжала — распознавание сорвалось, человека отвлекли — факт
    просто исчезал вместо того, чтобы обновиться.
    """
    from .. import memory

    split = _split_update(argument)
    if split is None:
        return ActionResult(False, "Скажите, что поправить и на что — «поправь X на Y»")
    query, text = split
    previous = memory.update_fact(query, text)
    if previous is None:
        return ActionResult(False, "Не нашёл такое в памяти")
    # Проговариваем и прежний текст: update_fact ищет факт по доле
    # пересечения слов и на коротком запросе может задеть не тот — как и в
    # forget(), иначе подмену не заметить ни на слух, ни в логах.
    logger.info("Факт обновлён: %r → %r (запрос: %r)", previous, text, argument)
    return ActionResult(True, f"Поправил. Было: {previous}")


def _split_update(argument: str) -> tuple[str, str] | None:
    """«стим-аккаунт на art_vol_teror» → («стим-аккаунт», «art_vol_teror»).

    Разрезаем по ПОСЛЕДНЕМУ « на »: предлог часто встречается и внутри
    самого факта («перешёл на новый ник на твиче»), а новое значение стоит
    в конце — по последнему разделителю запрос и значение не перепутаются.
    """
    parts = argument.rsplit(" на ", 1)
    if len(parts) != 2:
        return None
    query, text = parts[0].strip(), parts[1].strip()
    if not query or not text:
        return None
    return query, text
