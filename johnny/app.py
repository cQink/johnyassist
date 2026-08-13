import logging
from dataclasses import dataclass, replace

from . import chain, memory, panel_tools
from .actions import execute
from .brain import interpret, interpret_streamed, make_providers, punctuate
from .brain_groq import make_streaming_provider
from .browser_target import strip_tab_modifier
from .config import load_config
from .router import is_stop_word, route, route_exact
from .speaker import make_speaker

logger = logging.getLogger(__name__)


# ── Ответы (Response Layer) ──────────────────────────────────────────────────

def _respond(speaker, result, reply=None, cancel=None) -> None:
    """Успех — свой answer-звук (иначе текст); ошибка — всегда текст.

    cancel: сказали «стоп», пока действие выполнялось — отчитываться о нём
    (даже об ошибке) больше не нужно, человек уже не ждёт ответа.
    """
    if cancel is not None and cancel.is_set():
        return
    if result.ok:
        if not speaker.play_answer():
            speaker.say(reply or result.message)
    else:
        speaker.say(result.message)


# Действия, чей ответ обязан ЗВУЧАТЬ ТЕКСТОМ, а не коротким «дзынь».
# list_memory — иначе «что ты помнишь» тонет в звуке целиком. forget и
# update_fact — их сообщения несут текст затронутого факта («Забыл: ...»,
# «Поправил. Было: ...») ровно затем, чтобы промах поиска по пересечению слов
# был слышен: под звуком эта страховка не работает, а память молча теряет
# не тот факт.
_SPOKEN_ACTIONS = frozenset({"list_memory", "forget", "update_fact"})


def _respond_to_routed(speaker, routed, result, reply=None, cancel=None) -> None:
    """Обычный путь — _respond (звук/фолбэк-текст), кроме _SPOKEN_ACTIONS: там
    речь обязательна независимо от того, каким путём распозналась команда
    (локально или через исправление модели)."""
    if routed.action in _SPOKEN_ACTIONS:
        if cancel is None or not cancel.is_set():
            speaker.say(result.message)
        return
    _respond(speaker, result, reply, cancel=cancel)


# ── Модель данных ────────────────────────────────────────────────────────────

@dataclass
class Outcome:
    """Итог обработки фразы.

    via — как сработало, для истории. handled — сделал ли Джони хоть что-то
    осмысленное. False ровно для семейства «не понял»: по нему контроллер
    решает, уходить ли в восстановление (сыграть звук и ждать команду).
    Сбой ВЫПОЛНЕНИЯ понятой команды — это handled=True: восстанавливаться там
    незачем, человеку уже сказали, что пошло не так.
    """

    via: str
    handled: bool = True


def _chain_outcome(result, source: str) -> Outcome:
    """Пометка цепочки для истории: «цепочка(3)/локально».

    Если цепочка оборвалась, в скобках стоит «сделано/всего» — иначе строка
    истории врала бы, что отработали все шаги, а по ней настраиваются пороги.
    """
    size = f"{result.total}" if result.failure is None else f"{result.done}/{result.total}"
    return Outcome(f"цепочка({size})/{source}")


# ── Подготовка (Preprocessing) ───────────────────────────────────────────────

def _punctuate_dictation(routed, config):
    """«Джони, введи ...» — надиктованный текст приходит от Whisper без знаков
    препинания (длинная запись режется на куски по паузам, куски друг друга
    не видят, см. brain.punctuate). Не трогает никакие другие действия."""
    if routed.action != "type_text":
        return routed
    return replace(routed, argument=punctuate(routed.argument, make_providers(config)))


# ── Маршрутизация (Routing Layer) ────────────────────────────────────────────

def _route_local(text, config):
    """Локальная маршрутизация: точное совпадение → цепочка → шаблоны/фаззи.

    Возвращает (routed_or_steps, source) или (None, None).
    source: "exact", "chain", "template" — для истории.
    """
    # 1. Фиксированная фраза целиком. Стоит первой, чтобы команду, внутри
    # которой есть «и», не разрезал сплиттер: такая фраза заведомо одна.
    routed = route_exact(text, config.commands, literal_only=True)
    if routed is not None:
        return routed, "exact"
    # 2. Цепочка. ОБЯЗАНА идти впереди жадных шаблонов: «запусти *»
    # совпадает со всей фразой «запусти обс и громкость 20» точно.
    steps = chain.split_local(text, config.commands)
    if steps is not None:
        return steps, "chain"
    # 3. Обычная лестница: шаблоны и нечёткое сравнение.
    routed = route(text, config.commands)
    if routed is not None:
        return routed, "template"
    return None, None


# ── Выполнение (Execution Layer) ─────────────────────────────────────────────

def _dispatch(routed, config, speaker, new_tab: bool = True, cancel=None) -> Outcome | None:
    """Выполнить действие. Сценарий разворачивается в цепочку.

    None означает «сценарий не собрался» (шаг из scenarios.yaml не совпал ни
    с одной командой) — фраза идёт дальше по лестнице разбора.
    """
    if routed.action == "scenario":
        steps = chain.resolve(config.scenarios.get(routed.argument, []), config.commands)
        if steps is None:
            logger.warning("Сценарий %r не собрался: шаг не совпал с командой", routed.argument)
            return None
        return _chain_outcome(chain.run(steps, config, speaker, new_tab, cancel=cancel), "сценарий")
    routed = _punctuate_dictation(routed, config)
    result = execute(
        routed,
        config.apps,
        config.channels,
        new_tab=new_tab,
        people=config.people,
        config=config,
    )
    _respond_to_routed(speaker, routed, result, cancel=cancel)
    return Outcome(routed.via)


# ── Модель-корректор (Brain Fallback) ────────────────────────────────────────

def _speak_reply(speaker, answer, cancel) -> None:
    """Произнести реплику модели, если она ещё не прозвучала.

    Сторож против двойной озвучки: в стриминге реплика звучит ПО ХОДУ потока,
    и повторное say() дало бы человеку тот же ответ дважды.
    """
    if answer.spoken:
        return
    if cancel is None or not cancel.is_set():
        speaker.say(answer.reply)


def _streamed_answer(text, config, speaker, memory_block, cancel):
    """Ответ через конвейер или None, если конвейер недоступен.

    None здесь — нормальное состояние (выключен стриминг, не fish-голос, нет
    ключа Groq), и зовущий просто идёт прежним путём.
    """
    api_key = (config.secrets or {}).get("groq_api_key")
    if not api_key or not getattr(config.settings, "streaming", False):
        return None
    if getattr(speaker, "say_stream", None) is None:
        return None
    provider = make_streaming_provider(api_key, config.settings.groq_model)
    return interpret_streamed(
        text, config.commands, provider,
        lambda chunks: speaker.say_stream(chunks, cancel=cancel),
        memory_block=memory_block,
    )


def _brain_fallback(text, config, speaker, new_tab, cancel, not_understood):
    """Вызвать модель (Groq/Claude) для исправления нераспознанной команды.

    Модель может:
    - исправить текст и вернуть routed (исправление ослышки);
    - вернуть цепочку шагов (steps);
    - вернуть свободный ответ (reply);
    - не понять — тогда not_understood().
    """
    memory_block = memory.build_prompt_block(memory.recent_context(), memory.list_facts())
    answer = _streamed_answer(text, config, speaker, memory_block, cancel)
    if answer is None:
        answer = interpret(
            text, config.commands, make_providers(config), memory_block=memory_block
        )
    if cancel is not None and cancel.is_set():
        # «Стоп» пришёл, пока модель думала (Groq/claude -p не прервать
        # на лету) — результат уже никому не нужен, ни говорить, ни
        # выполнять его нельзя.
        return Outcome(answer.provider if answer else "модель")
    if answer is None:
        return not_understood("Не понял команду", "модель недоступна")
    # Короткая память: только опрошенные моделью реплики создают
    # контекст, на который может сослаться следующее «да, давай».
    # Исправление команды (routed задан, reply пуст) — не обмен репликами:
    # запоминать пустой ответ нечего, а пять таких подряд вытеснили бы из
    # 5-слотового буфера единственный настоящий вопрос-ответ.
    if answer.reply:
        memory.record_turn(text, answer.reply)
    if answer.steps:
        return _chain_outcome(
            chain.run(answer.steps, config, speaker, new_tab, cancel=cancel),
            answer.provider or "модель",
        )
    if answer.routed is not None:
        routed = _punctuate_dictation(answer.routed, config)
        _respond_to_routed(
            speaker,
            routed,
            execute(
                routed,
                config.apps,
                config.channels,
                new_tab=new_tab,
                people=config.people,
                config=config,
            ),
            answer.reply,
            cancel=cancel,
        )
        return Outcome(answer.provider or "модель")
    if answer.reply:
        _speak_reply(speaker, answer, cancel)
        return Outcome(answer.provider or "модель")
    if answer.spoken:
        # Поток отзвучал, но связного reply не осталось (например, оборвался
        # в середине и уже сказал об этом сам). Говорить «Не понял команду»
        # поверх этого — врать: Джони как раз ответил.
        return Outcome(answer.provider or "модель")
    # routed=None без reply — модель не поняла/не смогла исправить
    # ослышку. Раньше тут говорилось бодрое «Готово», хотя ничего
    # не произошло.
    return not_understood("Не понял команду", answer.provider or "модель")


# ── Главный пайплайн ─────────────────────────────────────────────────────────

def handle_command(
    text: str,
    config,
    speaker,
    *,
    speak_failures: bool = True,
    use_brain: bool = True,
    cancel=None,
) -> Outcome:
    """Выполнить команду.

    Пайплайн:
      1. Предобработка (стоп-слово, модификатор вкладки)
      2. Локальная маршрутизация (exact → chain → template/fuzzy)
      3. Brain fallback (модель-корректор, если use_brain=True)

    speak_failures=False глушит только «не понял»/«не расслышал» — так ведёт
    себя слитный режим, где срабатывание могло быть ложным. use_brain=False
    отключает модель-корректор: её не спрашивают, когда Whisper не подтвердил
    имя в записи. cancel (threading.Event) — сказали «стоп», пока команда
    выполнялась в фоне (см. controller.py); проверяется в точках, где команда
    иначе заговорила бы или выполнила бы действие ПОСЛЕ уже прозвучавшего
    «стоп» (см. _respond, chain.run — уже идущий шаг они не прерывают).
    """

    def not_understood(message: str, via: str) -> Outcome:
        if speak_failures:
            speaker.say(message)
        return Outcome(via, handled=False)

    # ── 1. Предобработка ──
    if not text.strip():
        return not_understood("Не расслышал", "пусто")
    if is_stop_word(text):
        return Outcome("стоп(нечего прерывать)")
    text, new_tab = strip_tab_modifier(text)

    try:
        # ── 2. Локальная маршрутизация ──
        match, source = _route_local(text, config)
        if match is not None:
            if source == "chain":
                return _chain_outcome(
                    chain.run(match, config, speaker, new_tab, cancel=cancel), "локально"
                )
            # exact или template → единичная команда
            outcome = _dispatch(match, config, speaker, new_tab, cancel=cancel)
            if outcome is not None:
                return outcome

        # ── 3. Brain fallback ──
        if not use_brain:
            return not_understood("Не понял команду", "мимо")
        return _brain_fallback(text, config, speaker, new_tab, cancel, not_understood)

    except Exception:
        logger.exception("Ошибка при выполнении команды")
        # Озвучиваем даже в тихом режиме: команду поняли, значит ждут ответа.
        speaker.say("Не смог выполнить команду")
        return Outcome("ошибка")


# ── Точка входа ──────────────────────────────────────────────────────────────

def run(config_dir: str = "config") -> None:
    from . import single_instance
    from .audio import Microphone
    from .controller import AssistantController
    from .listener import Listener
    from .recognizer import Recognizer, build_vocabulary

    if not single_instance.acquire():
        # Тот же мьютекс, что и у трея (johnny/tray.py) — не даёт запустить
        # консольный и трей-режим одновременно (см. single_instance.py про
        # живой баг с двумя параллельными процессами).
        print("Джони уже запущен другим процессом.")
        return

    config = load_config(config_dir)
    from .actions import load_plugins

    load_plugins()
    # Разговор с прошлого запуска: «а он что ответил?» после перезапуска
    # должно работать так же, как до него. Просроченные обмены отбрасывает
    # сам load_dialog, поэтому вчерашний контекст в промпт не попадёт.
    memory.load_dialog()
    speaker = make_speaker(config.settings, config.secrets)
    vocabulary = build_vocabulary(config.apps, config.channels, config.commands, config.people)
    recognizer = Recognizer(
        config.settings.whisper_model, config.settings.whisper_device, vocabulary
    )
    listener = Listener(config.settings.wake_word, config.settings.vosk_model_path)
    controller = AssistantController(config, speaker)

    # Разбор состояния — общий с панелью и треем (panel_tools.status): три копии
    # этой лестницы уже успели разойтись формулировками.
    state = panel_tools.status(
        listener=getattr(listener, "available", False),
        recognizer=getattr(recognizer, "available", False),
    )
    print(f"Джони запущен. {state.text}.")
    if state.listening:
        print("Скажи «Джони» и команду — можно одной фразой.")

    try:
        # Цикл живёт в контроллере: две ветки вызова (слитно / с паузой)
        # переписывать здесь второй раз нельзя, копии разъедутся.
        with Microphone() as mic:
            controller.run(listener, recognizer, mic)
    except KeyboardInterrupt:
        print("Выход.")
    finally:
        listener.close()
