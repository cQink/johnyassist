"""Цепочки команд: одна фраза — несколько действий.

Три источника списка шагов (сплит по союзам, сценарий из scenarios.yaml,
модель) сходятся в одну функцию `resolve`. Она прогоняет каждую фразу через
`route_exact`, поэтому ни один источник не может изобрести действие или
собрать кривой аргумент — ровно тот же приём, что уже применён к
«исправленной» команде в brain.py.
"""

import logging
import time
from dataclasses import dataclass

from . import actions
from .router import RoutedAction, is_unsafe_action, route_exact

logger = logging.getLogger(__name__)

# Потолок на любую цепочку. Больше пяти шагов одной фразой человек не
# произносит — такой список означает, что сплит или модель ошиблись.
MAX_STEPS = 5

# Длинные разделители идут первыми: «а потом» должен съесть оба слова, а не
# распасться на «а» + «потом».
_SEPARATORS = ("а также", "а потом", "потом", "затем", "плюс", "и")


def _split_phrases(text: str) -> list[str]:
    """Разрезать фразу по союзам.

    Режем ТОЛЬКО по отдельным словам — иначе «игра», «или», «история»
    распались бы на куски.
    """
    words = text.lower().replace(",", " ").split()
    parts: list[str] = []
    current: list[str] = []
    i = 0
    while i < len(words):
        matched = 0
        for separator in _SEPARATORS:
            sep_words = separator.split()
            if words[i : i + len(sep_words)] == sep_words:
                matched = len(sep_words)
                break
        if matched:
            parts.append(" ".join(current))
            current = []
            i += matched
            continue
        current.append(words[i])
        i += 1
    parts.append(" ".join(current))
    return [part for part in parts if part]


# Действия, которым в цепочке не место, помимо разрушительных.
#
# scenario — вложенные сценарии запрещены, чтобы не было рекурсии.
# discord_message — текст сообщения это свободная речь: союзы «и», «потом»,
#   «затем» в ней неизбежны, а хвост фразы легко совпадает с короткой
#   командой. «напиши гоше я приду потом включи музыку» собиралось в цепочку
#   и слало Гоше обрезанное «я приду», попутно нажимая паузу. Отвергнутая
#   цепочка означает, что фраза уедет в обычный разбор целиком.
#   discord_call сюда не нужен: он и так отвергается как необратимый
#   (см. is_unsafe_action).
_REJECTED_ACTIONS = frozenset({"scenario", "discord_message"})


def _rejected(routed: RoutedAction) -> bool:
    """Действие, которому в цепочке не место.

    Выключение/перезагрузку/сон можно произнести только целой фразой с
    точным совпадением.
    """
    return is_unsafe_action(routed.action, routed.argument) or routed.action in _REJECTED_ACTIONS


def _split_by_command(phrase: str, commands) -> list[RoutedAction] | None:
    """Разрезать неопознанный кусок по ГРАНИЦЕ КОМАНДЫ, без союза.

    «включи первое видео на полный экран» — цельного такого правила нет, но
    это команда-начало плюс команда-остаток. Ищем самое длинное начало,
    которое само является командой, и требуем, чтобы остаток тоже разобрался.

    Длинные начала пробуются первыми: иначе «включи первое видео» могло бы
    распасться по более короткому совпадению и утащить смысл не туда.

    ДВА ПРОХОДА (литеральный, потом с шаблонами): генерический ловящий-всё
    шаблон вроде «открой *»/«фокус *» (app_focus) совпадает почти с ЛЮБЫМ
    началом фразы, начинающейся на «открой»/«фокус» — не только с настоящей
    командой. Без литерального прохода первым он крал разрез на более
    длинном (и бессмысленном) начале раньше, чем очередь доходила до
    настоящей команды покороче. Живой баг 2026-08-04: «открой первое видео
    на полный экран» резалось на app_focus("первое видео на") +
    browser_fullscreen("полный экран") вместо browser_click_result +
    browser_fullscreen.
    """
    words = phrase.split()
    for literal_only in (True, False):
        for cut in range(len(words) - 1, 0, -1):
            head = route_exact(" ".join(words[:cut]), commands, literal_only=literal_only)
            if head is None or _rejected(head):
                continue
            rest = _resolve_phrase(" ".join(words[cut:]), commands)
            if rest is not None:
                return [head] + rest
    return None


def _resolve_phrase(phrase: str, commands) -> list[RoutedAction] | None:
    """Одна фраза → один или несколько шагов. None, если не разобралась.

    Литеральное целое совпадение проверяется ПЕРВЫМ, а разрез — раньше
    жадного шаблонного совпадения целиком: та же причина, что и в
    `_split_by_command` — «открой *» совпадает почти с любой фразой,
    начинающейся на «открой», и раньше глотал её целиком раньше, чем
    пробовался разрез на настоящие команды. Шаблонное совпадение целиком
    (например «загугли *») остаётся ПОСЛЕДНИМ резервом — только когда и
    литеральное совпадение, и разрез не нашли ничего.
    """
    routed = route_exact(phrase, commands, literal_only=True)
    if routed is not None:
        return None if _rejected(routed) else [routed]
    split = _split_by_command(phrase, commands)
    if split is not None:
        return split
    routed = route_exact(phrase, commands)
    if routed is not None:
        return None if _rejected(routed) else [routed]
    return None


def resolve(phrases: list[str], commands) -> list[RoutedAction] | None:
    """Список фраз → список действий. None, если цепочка не собирается.

    Достаточно одной неразобранной фразы, чтобы отвергнуть цепочку целиком:
    «сделать половину» хуже, чем не сделать ничего, — человек не услышит,
    где именно его не поняли.
    """
    if not phrases or len(phrases) > MAX_STEPS:
        return None
    steps: list[RoutedAction] = []
    for phrase in phrases:
        part = _resolve_phrase(phrase, commands)
        if part is None:
            return None
        steps.extend(part)
        # Потолок считает ШАГИ, а не куски: один кусок способен развернуться
        # в несколько действий, и цепочка из трёх кусков — не обязательно три.
        if len(steps) > MAX_STEPS:
            return None
    return steps


def split_local(text: str, commands) -> list[RoutedAction] | None:
    """Разрезать фразу и принять разбиение, только если КАЖДЫЙ кусок точно
    совпал с известной командой.

    Иначе None — и фраза пойдёт дальше по обычному пути целой.

    Цепочкой считается ДВА шага и больше, а не два куска: фраза без единого
    союза («открой твич включи музыку») тоже цепочка, а один кусок способен
    развернуться в несколько действий по границе команды.
    """
    steps = resolve(_split_phrases(text), commands)
    if steps is None or len(steps) < 2:
        return None
    return steps


def understands(text: str, commands) -> bool:
    """Точная команда или цепочка? Нечёткое сравнение сознательно не в счёт.

    На этой строгости держится восстановление ослышанного имени: пусти сюда
    догадки — и Джони начнёт «узнавать» команду в любой фразе, молча
    выбрасывая её первое слово.
    """
    return route_exact(text, commands) is not None or split_local(text, commands) is not None


@dataclass
class ChainResult:
    done: int
    total: int
    failure: str | None = None


def run(
    steps: list[RoutedAction], config, speaker, new_tab: bool = True, cancel=None
) -> ChainResult:
    """Выполнить шаги по очереди. Первый провал останавливает остаток.

    Звук успеха играется один раз в конце: три «Roger that» подряд за две
    секунды — это шум, а не подтверждение.

    cancel (threading.Event) — команда «стоп» посреди выполнения (см.
    controller._check_for_stop_while_busy). Проверяется МЕЖДУ шагами: уже начавшийся
    шаг (например, ожидание элемента в браузере) доигрывает до конца — только
    следующий шаг и финальное «Готово» отменяются. Прерванная цепочка не
    озвучивается вовсе: человек уже сказал «стоп», отчитываться не о чем.
    """
    total = len(steps)
    chain_start = time.monotonic()
    for index, step in enumerate(steps):
        if cancel is not None and cancel.is_set():
            return ChainResult(done=index, total=total, failure="прервано")
        step_start = time.monotonic()
        result = actions.execute(
            step,
            config.apps,
            config.channels,
            new_tab=new_tab,
            people=config.people,
            config=config,
        )
        # ДИАГНОСТИКА (2026-08-06): «открой видео на полный экран» съедала ~9с
        # на два шага, а прошлая диагностика (только внутри fullscreen) не
        # засекла ничего — непонятно было, где именно уходит время: в клике
        # по результату или в самом разворачивании экрана.
        logger.info(
            "Тайминг цепочки: шаг %d/%d (%s) занял %.2fс, +%.2fс от начала цепочки",
            index + 1,
            total,
            step.action,
            time.monotonic() - step_start,
            time.monotonic() - chain_start,
        )
        if not result.ok:
            # Шаги чаще всего связаны: разворачивать на весь экран, когда
            # видео не открылось, — значит развернуть чужую вкладку.
            speaker.say(f"Сделал {index} из {total}, не смог: {result.message}")
            return ChainResult(done=index, total=total, failure=result.message)
    if cancel is not None and cancel.is_set():
        return ChainResult(done=total, total=total, failure="прервано")
    if not speaker.play_answer():
        speaker.say("Готово")
    return ChainResult(done=total, total=total)
