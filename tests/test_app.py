import types

from johnny import app, brain
from johnny.app import handle_command
from johnny.config import Config, Settings, CommandRule


def _config():
    return Config(
        apps={"дота": "steam://rungameid/570"},
        commands=[CommandRule("запусти *", "launch_app", "{0}")],
        settings=Settings("джони", "", "off", "medium", "cuda"),
    )


class SpySpeaker:
    def __init__(self, answer_played=False):
        self.said = []
        self._answer_played = answer_played
        self.answer_calls = 0

    def say(self, text):
        self.said.append(text)

    def play_answer(self):
        self.answer_calls += 1
        return self._answer_played


def test_template_match_executes_action(monkeypatch):
    import johnny.app as app
    launched = {}
    monkeypatch.setattr(app, "execute", lambda routed, apps, channels, new_tab=True, people=None, config=None: _fake_result(launched, routed))
    sp = SpySpeaker()
    handle_command("запусти дота", _config(), sp)
    assert launched["action"] == "launch_app"
    assert sp.said == ["Запускаю дота"]


def _fake_result(store, routed):
    from johnny.actions import ActionResult
    store["action"] = routed.action
    return ActionResult(True, "Запускаю дота")


def test_success_plays_answer_sound_instead_of_tts(monkeypatch):
    import johnny.app as app
    from johnny.actions import ActionResult

    monkeypatch.setattr(app, "execute", lambda routed, apps, channels, new_tab=True, people=None, config=None: ActionResult(True, "Запускаю дота"))
    sp = SpySpeaker(answer_played=True)  # свой звук проигрался
    handle_command("запусти дота", _config(), sp)
    assert sp.answer_calls == 1
    assert sp.said == []  # текст не проговаривается, играет звук


def test_no_template_falls_back_to_brain(monkeypatch):
    import johnny.app as app
    from johnny.brain import BrainResult
    monkeypatch.setattr(app, "interpret", lambda text, commands, providers, **kwargs: BrainResult(routed=None, reply="Отвечаю"))
    sp = SpySpeaker()
    handle_command("расскажи анекдот", _config(), sp)
    assert sp.said == ["Отвечаю"]


def test_action_exception_is_spoken_not_raised(monkeypatch):
    import johnny.app as app
    def boom(routed, apps, channels, new_tab=True, people=None, config=None):
        raise RuntimeError("bad path")
    monkeypatch.setattr(app, "execute", boom)
    sp = SpySpeaker()
    handle_command("запусти дота", _config(), sp)  # must NOT raise
    assert sp.said == ["Не смог выполнить команду"]


def test_failed_brain_action_speaks_failure_not_reply(monkeypatch):
    import johnny.app as app
    from johnny.brain import BrainResult
    from johnny.router import RoutedAction
    from johnny.actions import ActionResult
    monkeypatch.setattr(app, "interpret", lambda text, commands, providers, **kwargs: BrainResult(routed=RoutedAction("launch_app", "notepad"), reply="Запускаю"))
    monkeypatch.setattr(app, "execute", lambda routed, apps, channels, new_tab=True, people=None, config=None: ActionResult(False, "Не знаю такой программы"))
    sp = SpySpeaker()
    handle_command("открой блокнот", _config(), sp)
    assert sp.said == ["Не знаю такой программы"]


def test_failed_correction_says_not_understood(monkeypatch):
    # Коррекция модели не проходит route() (или JSON без распознанных
    # ключей) — routed=None и reply=None. Раньше это озвучивалось как
    # бодрое «Готово», хотя ничего не произошло.
    import johnny.app as app
    from johnny.brain import BrainResult
    monkeypatch.setattr(app, "interpret", lambda text, commands, providers, **kwargs: BrainResult(routed=None, reply=None))
    sp = SpySpeaker()
    handle_command("станцуй лезгинку", _config(), sp)
    assert sp.said == ["Не понял команду"]


def test_brain_unavailable_says_not_understood(monkeypatch):
    import johnny.app as app
    monkeypatch.setattr(app, "interpret", lambda text, commands, providers, **kwargs: None)
    sp = SpySpeaker()
    handle_command("расскажи анекдот", _config(), sp)
    assert sp.said == ["Не понял команду"]


def test_outcome_reports_handled_on_success(monkeypatch):
    import johnny.app as app
    from johnny.actions import ActionResult

    monkeypatch.setattr(app, "execute", lambda routed, apps, channels, new_tab=True, people=None, config=None: ActionResult(True, "Запускаю дота"))
    outcome = handle_command("запусти дота", _config(), SpySpeaker())
    assert outcome.handled is True
    assert outcome.via == "точно"


def test_type_text_argument_gets_punctuated_before_typing(monkeypatch):
    """«Джони, введи ...» — Whisper режет длинную диктовку на куски без
    знаков препинания (см. brain.punctuate). Аргумент, дошедший до execute,
    обязан быть уже почищенным текстом, а не сырой расшифровкой."""
    import johnny.app as app
    from johnny.actions import ActionResult
    from johnny.config import CommandRule, Config, Settings

    captured = {}
    monkeypatch.setattr(
        app,
        "execute",
        lambda routed, apps, channels, new_tab=True, people=None, config=None: captured.setdefault(
            "argument", routed.argument
        )
        or ActionResult(True, "Ввёл"),
    )
    monkeypatch.setattr(app, "make_providers", lambda config: [("тест", lambda prompt: "Привет, как дела?")])
    config = Config(
        apps={},
        commands=[CommandRule("введи *", "type_text", "{0}")],
        settings=Settings("джони", "", "off", "medium", "cuda"),
    )
    handle_command("введи привет как дела", config, SpySpeaker(answer_played=True))
    assert captured["argument"] == "Привет, как дела?"


def test_type_text_from_brain_correction_also_gets_punctuated(monkeypatch):
    """Та же чистка нужна и когда «введи ...» дошло не точным совпадением,
    а через исправление модели-корректора (answer.routed)."""
    import johnny.app as app
    from johnny.actions import ActionResult
    from johnny.brain import BrainResult
    from johnny.router import RoutedAction

    captured = {}
    monkeypatch.setattr(
        app,
        "execute",
        lambda routed, apps, channels, new_tab=True, people=None, config=None: captured.setdefault(
            "argument", routed.argument
        )
        or ActionResult(True, "Ввёл"),
    )
    monkeypatch.setattr(
        app,
        "interpret",
        lambda text, commands, providers, **kwargs: BrainResult(
            routed=RoutedAction("type_text", "привет как дела"), reply=None
        ),
    )
    monkeypatch.setattr(app, "make_providers", lambda config: [("тест", lambda prompt: "Привет, как дела?")])
    handle_command("ведди привет как дела", _config(), SpySpeaker(answer_played=True))
    assert captured["argument"] == "Привет, как дела?"


def test_silent_mode_swallows_not_understood(monkeypatch):
    # Слитный режим: фраза ни во что не сошлась. Джони обязан промолчать —
    # случайное «Джони» в войсчате не должно вызывать реплик вслух.
    import johnny.app as app

    monkeypatch.setattr(app, "interpret", lambda text, commands, providers, **kwargs: None)
    sp = SpySpeaker()
    outcome = handle_command("да я говорю ему", _config(), sp, speak_failures=False)
    assert sp.said == []
    assert outcome.handled is False


def test_silent_mode_still_speaks_execution_failures(monkeypatch):
    # А вот сбой ВЫПОЛНЕНИЯ озвучивается всегда: команду поняли, значит
    # человек ждёт ответа.
    import johnny.app as app

    def boom(routed, apps, channels, new_tab=True, people=None, config=None):
        raise RuntimeError("bad path")

    monkeypatch.setattr(app, "execute", boom)
    sp = SpySpeaker()
    outcome = handle_command("запусти дота", _config(), sp, speak_failures=False)
    assert sp.said == ["Не смог выполнить команду"]
    assert outcome.handled is True


def test_use_brain_false_skips_model(monkeypatch):
    # Имя не подтвердилось Whisper'ом — модель-корректор не зовём вовсе:
    # её работа превратить невнятицу в ближайшую команду, и на случайной
    # болтовне она это честно сделает.
    import johnny.app as app

    called = {}
    monkeypatch.setattr(app, "interpret", lambda *a, **k: called.setdefault("yes", True))
    sp = SpySpeaker()
    outcome = handle_command("что-то непонятное", _config(), sp, use_brain=False)
    assert called == {}
    assert outcome.handled is False
    assert outcome.via == "мимо"


def test_empty_text_is_not_handled():
    sp = SpySpeaker()
    outcome = handle_command("   ", _config(), sp)
    assert sp.said == ["Не расслышал"]
    assert outcome.handled is False
    assert outcome.via == "пусто"


def test_empty_brain_steps_fall_back_to_not_understood(monkeypatch):
    import johnny.app as app
    from johnny.brain import BrainResult

    monkeypatch.setattr(app, "interpret", lambda *a, **k: BrainResult(steps=[], routed=None, reply=None))
    sp = SpySpeaker()
    outcome = handle_command("что-то непонятное", _config(), sp)
    assert sp.said == ["Не понял команду"]
    assert outcome.handled is False
    assert outcome.via == "модель"


def test_stop_word_alone_does_not_reach_brain(monkeypatch):
    """«Джони, стоп», сказанное пока Джони простаивает (перебивать нечего) —
    зарезервированное слово, а не обычная фраза. Раньше оно доходило до
    route()/модели-корректора, а там «стоп» смысловым совпадением падало на
    «останови»/«пауза» из списка медиа-команд (см. commands.yaml) — модель
    «исправляла» ослышку в system/play_pause и переключала видео/музыку,
    хотя пользователь просто проверял команду простоя."""
    import johnny.app as app

    called = {}
    monkeypatch.setattr(app, "interpret", lambda *a, **k: called.setdefault("brain", True))
    sp = SpySpeaker()
    outcome = handle_command("стоп", _config(), sp)
    assert called == {}, "«стоп» не должен доходить до модели-корректора"
    assert sp.said == []
    assert outcome.handled is True


# --- cancel: «Джони, стоп» посреди выполнения (см. controller.py) ---


def test_cancel_set_before_dispatch_silences_success_message(monkeypatch):
    """Действие уже выполнилось (например, к моменту, когда «стоп» долетел),
    но озвучивать результат/ошибку больше не нужно — человек уже не ждёт."""
    import threading

    import johnny.app as app
    from johnny.actions import ActionResult

    monkeypatch.setattr(
        app, "execute", lambda routed, apps, channels, new_tab=True, people=None, config=None: ActionResult(True, "Запускаю дота")
    )
    sp = SpySpeaker()
    cancel = threading.Event()
    cancel.set()
    handle_command("запусти дота", _config(), sp, cancel=cancel)
    assert sp.said == []
    assert sp.answer_calls == 0


def test_cancel_set_silences_brain_reply(monkeypatch):
    """Модель успела ответить (болтовня), но «стоп» пришёл, пока она думала —
    реплику озвучивать нельзя."""
    import threading

    import johnny.app as app
    from johnny.brain import BrainResult

    monkeypatch.setattr(
        app, "interpret", lambda text, commands, providers, **kwargs: BrainResult(routed=None, reply="Отвечаю")
    )
    sp = SpySpeaker()
    cancel = threading.Event()
    cancel.set()
    handle_command("расскажи анекдот", _config(), sp, cancel=cancel)
    assert sp.said == []


def test_cancel_not_set_behaves_as_before(monkeypatch):
    """Явно проверяем, что cancel=None (значение по умолчанию для всех
    старых вызовов) ничего не меняет — регресс на дефолте был бы незаметен
    в остальных тестах этого файла, они его не передают вовсе."""
    import johnny.app as app
    from johnny.actions import ActionResult

    monkeypatch.setattr(
        app, "execute", lambda routed, apps, channels, new_tab=True, people=None, config=None: ActionResult(True, "Запускаю дота")
    )
    sp = SpySpeaker(answer_played=True)
    handle_command("запусти дота", _config(), sp, cancel=None)
    assert sp.answer_calls == 1


def _config_chain(commands, scenarios=None):
    return Config(
        apps={},
        commands=commands,
        settings=Settings("джони", "", "off", "medium", "cuda"),
        scenarios=scenarios or {},
    )


def test_greedy_template_does_not_swallow_a_chain(monkeypatch):
    """Главный регрессионный тест итерации.

    «запусти *» точно совпадает со всей фразой «запусти обс и громкость 20»,
    поэтому сплиттер обязан стоять впереди шаблонов. Иначе Джони будет
    искать программу с именем «обс и громкость 20».
    """
    import johnny.actions as actions
    from johnny.actions import ActionResult

    done = []
    monkeypatch.setattr(
        actions,
        "execute",
        lambda routed, apps, channels, new_tab=True, people=None, config=None: done.append(
            (routed.action, routed.argument)
        )
        or ActionResult(True, "Готово"),
    )
    config = _config_chain(
        [
            CommandRule("запусти *", "launch_app", "{0}"),
            CommandRule("громкость *", "set_volume", "{0}"),
        ]
    )
    speaker = SpySpeaker(answer_played=True)
    outcome = handle_command("запусти обс и громкость 20", config, speaker)
    assert done == [("launch_app", "обс"), ("set_volume", "20")]
    assert outcome.via == "цепочка(2)/локально"
    assert speaker.answer_calls == 1, "звук успеха должен прозвучать один раз на всю цепочку"


def test_scenario_expands_into_chain(monkeypatch):
    import johnny.actions as actions
    from johnny.actions import ActionResult

    done = []
    monkeypatch.setattr(
        actions,
        "execute",
        lambda routed, apps, channels, new_tab=True, people=None, config=None: done.append(routed.action)
        or ActionResult(True, "Готово"),
    )
    config = _config_chain(
        [
            CommandRule("открой твич", "browser_open", "twitch.tv"),
            CommandRule("громкость *", "set_volume", "{0}"),
            CommandRule("режим стрима", "scenario", "режим стрима"),
        ],
        scenarios={"режим стрима": ["открой твич", "громкость 20"]},
    )
    outcome = handle_command("режим стрима", config, SpySpeaker(answer_played=True))
    assert done == ["browser_open", "set_volume"]
    assert outcome.via == "цепочка(2)/сценарий"


def test_fixed_phrase_with_and_is_not_split(monkeypatch):
    """Фиксированная фраза, внутри которой есть «и», остаётся одной командой."""
    import johnny.app as app
    from johnny.actions import ActionResult

    done = []
    monkeypatch.setattr(
        app,
        "execute",
        lambda routed, apps, channels, new_tab=True, people=None, config=None: done.append(routed.argument)
        or ActionResult(True, "Готово"),
    )
    config = _config_chain(
        [
            CommandRule("сверни все и покажи стол", "system", "show_desktop"),
            CommandRule("сверни все", "system", "minimize_all"),
            CommandRule("покажи стол", "system", "show_desktop"),
        ]
    )
    handle_command("сверни всё и покажи стол", config, SpySpeaker(answer_played=True))
    assert done == ["show_desktop"], "фраза распалась на две команды"


def test_broken_chain_history_reports_how_far_it_got(monkeypatch):
    """Оборванная цепочка не должна писать в историю, что сделала всё."""
    import johnny.actions as actions
    from johnny.actions import ActionResult

    calls = []

    def fake_execute(routed, apps, channels, new_tab=True, people=None, config=None):
        calls.append(routed)
        if len(calls) == 2:
            return ActionResult(False, "Не знаю такой программы")
        return ActionResult(True, "Готово")

    monkeypatch.setattr(actions, "execute", fake_execute)
    config = _config_chain(
        [
            CommandRule("открой твич", "browser_open", "twitch.tv"),
            CommandRule("запусти *", "launch_app", "{0}"),
        ]
    )
    outcome = handle_command("открой твич и запусти обс", config, SpySpeaker(answer_played=True))
    assert outcome.via == "цепочка(1/2)/локально"


def test_tab_modifier_reaches_the_browser(monkeypatch):
    """«в этой вкладке» снимается с фразы и доезжает до браузера параметром."""
    import johnny.actions as actions
    from johnny.actions import ActionResult

    seen = {}
    monkeypatch.setattr(
        actions,
        "execute",
        lambda routed, apps, channels, new_tab=True, people=None, config=None: seen.setdefault(
            "new_tab", new_tab
        )
        or ActionResult(True, "Готово"),
    )
    config = _config_chain(
        [
            CommandRule("найди на ютубе *", "browser_open", "yt?q={0}"),
            CommandRule("включи первое видео", "browser_click_result", "1"),
        ]
    )
    handle_command(
        "найди на ютубе котики в этой вкладке и включи первое видео",
        config,
        SpySpeaker(answer_played=True),
    )
    assert seen["new_tab"] is False


# --- Справочник людей обязан доезжать до действия ---
# Без этих трёх тестов (два здесь, один в test_chain.py) из вызовов execute
# можно было убрать people=config.people, и все тесты остались бы зелёными:
# дублёры execute объявляют people со значением по умолчанию. Discord при
# этом замолчал бы целиком — «Не понял, кому писать» на любое имя.
# Поэтому здесь people объявлен обязательным ключевым параметром: потеря
# аргумента на вызывающей стороне ломает вызов, а не проходит молча.

PEOPLE = {"гоша": {"discord": "Гречка", "username": "ne_godjaj", "aliases": ["гоша"]}}


def _config_with_people():
    return Config(
        apps={},
        commands=[CommandRule("напиши *", "discord_message", "{0}")],
        settings=Settings("джони", "", "off", "medium", "cuda"),
        people=PEOPLE,
    )


def test_справочник_людей_доезжает_до_действия(monkeypatch):
    import johnny.app as app
    from johnny.actions import ActionResult

    seen = {}

    def fake_execute(routed, apps, channels, new_tab=True, *, people, config=None):
        seen["people"] = people
        return ActionResult(True, "Отправил")

    monkeypatch.setattr(app, "execute", fake_execute)
    handle_command("напиши гоше привет", _config_with_people(), SpySpeaker(answer_played=True))

    assert seen["people"] == PEOPLE, "справочник людей не доехал до discord_message"


def test_справочник_людей_доезжает_и_через_модель(monkeypatch):
    import johnny.app as app
    from johnny.actions import ActionResult
    from johnny.brain import BrainResult
    from johnny.router import RoutedAction

    seen = {}

    def fake_execute(routed, apps, channels, new_tab=True, *, people, config=None):
        seen["people"] = people
        return ActionResult(True, "Отправил")

    monkeypatch.setattr(app, "execute", fake_execute)
    monkeypatch.setattr(
        app,
        "interpret",
        lambda text, commands, providers, **kwargs: BrainResult(
            routed=RoutedAction("discord_message", "гоше привет"), reply=None
        ),
    )
    handle_command("черкани гоше привет", _config_with_people(), SpySpeaker(answer_played=True))

    assert seen["people"] == PEOPLE, "справочник людей не доехал по пути модели"


class _FakeMicrophone:
    """Микрофон run() открывает через `with Microphone() as mic: ...»."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeListener:
    def close(self):
        pass


class _FakeController:
    def __init__(self, config, speaker):
        pass

    def run(self, listener, recognizer, mic):
        # Реальный контроллер крутит бесконечный цикл прослушивания — в
        # тесте он не нужен, важен только сам факт вызова build_vocabulary
        # ДО этого момента.
        pass


def test_build_vocabulary_получает_людей_в_run(monkeypatch):
    # Без этого теста из run() можно убрать config.people из вызова
    # build_vocabulary, и все тесты останутся зелёными — Whisper перестанет
    # слышать имена людей в словаре подсказки, а сам build_vocabulary при
    # этом отлично протестирован в изоляции (test_recognizer_vocab.py). Тот
    # же класс дефекта, что уже чинили для execute() (см. тесты выше).
    import johnny.app as app
    import johnny.audio as audio_module
    import johnny.controller as controller_module
    import johnny.listener as listener_module
    import johnny.recognizer as recognizer_module
    import johnny.single_instance as single_instance_module
    from johnny.config import Config, Settings

    config = Config(
        apps={},
        commands=[],
        settings=Settings("джони", "", "off", "medium", "cuda"),
        people=PEOPLE,
    )
    seen = {}

    def fake_build_vocabulary(apps, channels, commands, people):
        seen["people"] = people
        return "vocab"

    monkeypatch.setattr(single_instance_module, "acquire", lambda *a, **k: True)
    monkeypatch.setattr(app, "load_config", lambda config_dir: config)
    monkeypatch.setattr(app, "make_speaker", lambda settings, secrets: object())
    monkeypatch.setattr(recognizer_module, "build_vocabulary", fake_build_vocabulary)
    monkeypatch.setattr(recognizer_module, "Recognizer", lambda *a, **k: object())
    monkeypatch.setattr(listener_module, "Listener", lambda *a, **k: _FakeListener())
    monkeypatch.setattr(controller_module, "AssistantController", _FakeController)
    monkeypatch.setattr(audio_module, "Microphone", _FakeMicrophone)

    app.run()

    assert seen["people"] == PEOPLE, "справочник людей не доехал до build_vocabulary в run()"


def test_brain_turn_is_recorded_in_memory(monkeypatch):
    """После обращения к модели обмен должен попасть в короткую память —
    иначе следующее «да, давай» не сможет сослаться на этот вопрос."""
    import johnny.app as app
    from johnny.brain import BrainResult
    import johnny.memory as memory

    memory._turns.clear()
    monkeypatch.setattr(memory, "list_facts", lambda: [])
    monkeypatch.setattr(
        app, "interpret", lambda text, commands, providers, **kwargs: BrainResult(routed=None, reply="Хотите анекдот?")
    )
    handle_command("расскажи что-нибудь", _config(), SpySpeaker())
    context = memory.recent_context()
    assert len(context) == 1
    assert context[0].user_text == "расскажи что-нибудь"
    assert context[0].reply == "Хотите анекдот?"


def test_memory_block_reaches_interpret(monkeypatch):
    """Собранный блок памяти обязан дойти до interpret(), иначе модель не
    увидит ни контекст разговора, ни факты."""
    import johnny.app as app
    import johnny.memory as memory
    from johnny.brain import BrainResult

    memory._turns.clear()
    monkeypatch.setattr(memory, "list_facts", lambda: ["любит кофе без сахара"])
    seen = {}

    def fake_interpret(text, commands, providers, **kwargs):
        seen["memory_block"] = kwargs.get("memory_block", "")
        return BrainResult(routed=None, reply="ок")

    monkeypatch.setattr(app, "interpret", fake_interpret)
    handle_command("расскажи что-нибудь", _config(), SpySpeaker())
    assert "любит кофе без сахара" in seen["memory_block"]


def test_list_memory_is_always_spoken_not_chimed(monkeypatch):
    """«Что ты помнишь» обязано звучать текстом. Обычный путь _respond при
    успехе предпочитает короткий звук (play_answer) вместо речи — для
    списка фактов это означало бы полное молчание."""
    import johnny.app as app
    from johnny.actions import ActionResult
    from johnny.config import CommandRule, Config, Settings

    monkeypatch.setattr(
        app,
        "execute",
        lambda routed, apps, channels, new_tab=True, people=None, config=None: ActionResult(
            True, "Вот что я помню: любит кофе без сахара"
        ),
    )
    config = Config(
        apps={},
        commands=[CommandRule("что ты помнишь", "list_memory", "-")],
        settings=Settings("джони", "", "off", "medium", "cuda"),
    )
    sp = SpySpeaker(answer_played=True)  # звук ГОТОВ проиграться, но не должен
    handle_command("что ты помнишь", config, sp)
    assert sp.said == ["Вот что я помню: любит кофе без сахара"]
    assert sp.answer_calls == 0, "list_memory не должен даже пытаться играть звук"


def test_list_memory_is_spoken_on_model_correction_path(monkeypatch):
    """То же самое, но когда команду распознала не локальная лестница, а
    исправление модели (Whisper ослышался «что ты помнишь», модель поняла
    правильно) — второй путь выполнения routed-действия в handle_command,
    мимо _dispatch. Раньше там всегда играл звук/тишину, факты не звучали."""
    import johnny.app as app
    import johnny.memory as memory
    from johnny.actions import ActionResult
    from johnny.brain import BrainResult
    from johnny.router import RoutedAction

    monkeypatch.setattr(memory, "list_facts", lambda: [])
    monkeypatch.setattr(
        app,
        "interpret",
        lambda text, commands, providers, **kwargs: BrainResult(
            routed=RoutedAction("list_memory", "-"), reply=None
        ),
    )
    monkeypatch.setattr(
        app,
        "execute",
        lambda routed, apps, channels, new_tab=True, people=None, config=None: ActionResult(
            True, "Вот что я помню: любит кофе без сахара"
        ),
    )
    sp = SpySpeaker(answer_played=True)  # звук ГОТОВ проиграться, но не должен
    handle_command("что ты паомнишь", _config(), sp)
    assert sp.said == ["Вот что я помню: любит кофе без сахара"]
    assert sp.answer_calls == 0, "list_memory не должен даже пытаться играть звук"


def test_routed_command_without_reply_is_not_recorded_in_memory(monkeypatch):
    """Исправление модели без разговорного reply (answer.routed задан,
    answer.reply is None) — это не обмен репликами, запоминать в короткую
    память нечего. Иначе пять подряд исправленных команд вытеснят из
    5-слотового буфера единственный настоящий вопрос-ответ."""
    import johnny.app as app
    import johnny.memory as memory
    from johnny.actions import ActionResult
    from johnny.brain import BrainResult
    from johnny.router import RoutedAction

    memory._turns.clear()
    monkeypatch.setattr(memory, "list_facts", lambda: [])
    monkeypatch.setattr(
        app,
        "interpret",
        lambda text, commands, providers, **kwargs: BrainResult(
            routed=RoutedAction("launch_app", "дота"), reply=None
        ),
    )
    # Не должен уйти по локальной лестнице (иначе interpret вообще не
    # вызовется, и тест ничего не проверит) — текст не совпадает с «запусти *».
    monkeypatch.setattr(
        app,
        "execute",
        lambda routed, apps, channels, new_tab=True, people=None, config=None: ActionResult(
            True, "Запускаю дота"
        ),
    )
    handle_command("хочу доту", _config(), SpySpeaker())
    assert memory.recent_context() == []


def test_memory_persists_across_two_handle_command_calls(monkeypatch):
    """Реальный сквозной сценарий фичи: Джони спросил «Хотите X?», следующий
    вызов должен увидеть этот обмен в memory_block, без единого мока памяти."""
    import johnny.app as app
    import johnny.memory as memory
    from johnny.brain import BrainResult

    memory._turns.clear()
    monkeypatch.setattr(memory, "list_facts", lambda: [])

    def fake_interpret(text, commands, providers, **kwargs):
        # На первый вызов отвечаем разговорным ответом с вопросом.
        # На второй — проверяем, что этот вопрос дошёл до промпта.
        if "анекдот" in text:
            return BrainResult(routed=None, reply="Хотите ещё один?")
        assert "Хотите ещё один?" in kwargs.get("memory_block", ""), kwargs.get(
            "memory_block"
        )
        return BrainResult(routed=None, reply="ок, вот ещё")

    monkeypatch.setattr(app, "interpret", fake_interpret)
    sp = SpySpeaker()
    handle_command("расскажи анекдот", _config(), sp)
    handle_command("да давай", _config(), sp)
    assert sp.said == ["Хотите ещё один?", "ок, вот ещё"]


def test_forget_and_update_fact_are_spoken_not_chimed(monkeypatch):
    """«Забыл: X» и «Поправил. Было: X» несут текст затронутого факта именно
    затем, чтобы промах поиска по пересечению слов был слышен. Под коротким
    звуком эта страховка не работает — память молча теряет не тот факт."""
    import johnny.app as app
    from johnny.actions import ActionResult
    from johnny.config import CommandRule, Config, Settings

    for phrase, action, message in (
        ("забудь про кофе", "forget", "Забыл: любит кофе без сахара"),
        ("поправь ник на flynes_", "update_fact", "Поправил. Было: ник old_nick"),
    ):
        monkeypatch.setattr(
            app,
            "execute",
            lambda routed, apps, channels, new_tab=True, people=None, config=None, _m=message: ActionResult(
                True, _m
            ),
        )
        config = Config(
            apps={},
            commands=[CommandRule(phrase.split(" ", 1)[0] + " *", action, "{0}")],
            settings=Settings("джони", "", "off", "medium", "cuda"),
        )
        sp = SpySpeaker(answer_played=True)  # звук ГОТОВ проиграться, но не должен
        handle_command(phrase, config, sp)
        assert sp.said == [message], action
        assert sp.answer_calls == 0, f"{action} не должен даже пытаться играть звук"


# ── Сторож против двойной озвучки (_speak_reply) ────────────────────────────


def test_streamed_reply_is_not_spoken_twice(monkeypatch):
    """Сторож интеграции: reply, уже прозвучавший в конвейере, app обязан
    пропустить молча."""
    said = []
    speaker = types.SimpleNamespace(
        say=lambda text: said.append(text),
        say_stream=lambda chunks, cancel=None: None,
        play_answer=lambda: False,
    )
    answer = brain.BrainResult(routed=None, reply="Дела отлично", provider="groq", spoken=True)
    app._speak_reply(speaker, answer, cancel=None)
    assert said == []


def test_unstreamed_reply_is_spoken(monkeypatch):
    said = []
    speaker = types.SimpleNamespace(
        say=lambda text: said.append(text),
        say_stream=lambda chunks, cancel=None: None,
        play_answer=lambda: False,
    )
    answer = brain.BrainResult(routed=None, reply="Дела отлично", provider="groq", spoken=False)
    app._speak_reply(speaker, answer, cancel=None)
    assert said == ["Дела отлично"]


# ── Развилка _streamed_answer ────────────────────────────────────────────────


def test_streamed_answer_skipped_when_strong_brain_first(monkeypatch):
    """strong_brain_first=True значит «сильная модель — ОСНОВНОЙ мозг» (см.
    make_providers). Стриминг умеет звать только Groq и никогда не зовёт
    make_providers — если бы конвейер молча подключался поверх этой
    настройки, Opus/GPT переставала бы видеть фразу первой, хотя владелец
    явно включил её как основную. Ключ Groq и say_stream у спикера НАРОЧНО
    присутствуют — падать конвейер обязан именно из-за strong_brain_first,
    а не из-за отсутствия остальных условий."""
    settings = Settings(
        "джони", "", "off", "medium", "cuda", streaming=True, strong_brain_first=True
    )
    config = Config(apps={}, commands=[], settings=settings, secrets={"groq_api_key": "k"})
    speaker = types.SimpleNamespace(say_stream=lambda chunks, cancel=None: None)
    result = app._streamed_answer("привет", config, speaker, "", None)
    assert result is None


def test_brain_fallback_uses_streaming_pipeline_when_enabled(monkeypatch):
    """Развилка _streamed_answer сегодня не покрыта ни одним тестом: в
    остальных тестах app.py секреты пусты, поэтому первое же условие (ключ
    Groq) обрывает функцию раньше, чем настройка streaming вообще читается —
    опечатка вроде getattr(config.settings, "streming", ...) прошла бы весь
    набор незамеченной. Гоняем _brain_fallback с ключом Groq в секретах,
    streaming=True и подставным say_stream и проверяем, что ответ дал именно
    конвейер (interpret/make_providers звать не должен)."""
    calls = []

    def fake_say_stream(chunks, cancel=None):
        calls.append(list(chunks))
        return brain.say_stream.StreamResult(text="Дела отлично", spoken=True)

    said = []
    speaker = types.SimpleNamespace(
        say=lambda text: said.append(text),
        say_stream=fake_say_stream,
        play_answer=lambda: False,
    )
    settings = Settings("джони", "", "off", "medium", "cuda", streaming=True)
    config = Config(apps={}, commands=[], settings=settings, secrets={"groq_api_key": "k"})
    monkeypatch.setattr(
        app, "make_streaming_provider",
        lambda api_key, model: (lambda prompt: iter(["Дела отлично"])),
    )

    def boom(*args, **kwargs):
        raise AssertionError("обычный interpret не должен звать — конвейер уже ответил")

    monkeypatch.setattr(app, "interpret", boom)

    def not_understood(message, via):
        raise AssertionError(f"конвейер должен был дать ответ, а не {via!r}")

    outcome = app._brain_fallback("как дела", config, speaker, True, None, not_understood)
    assert calls, "say_stream не был вызван — конвейер не использовался"
    assert outcome.via == "groq"
    # spoken=True у StreamResult -> _speak_reply обязан промолчать.
    assert said == []


def test_календарь_и_напоминание_звучат_текстом_а_не_дзынькают(monkeypatch):
    """Живой промах 2026-08-23: на «что у меня завтра» Джони ответил «Так точно».

    Команда отработала правильно — в логе `Распознано: 'что у меня завтра'`, —
    но ответом на такой вопрос является сам список, а обычный путь _respond
    при успехе предпочитает короткий звук. У remind причина другая и не менее
    важная: в его сообщении лежит РАСПОЗНАННАЯ ДАТА, и это единственный шанс
    услышать, что фразу разобрали не так.
    """
    import johnny.app as app
    from johnny.actions import ActionResult
    from johnny.config import CommandRule, Config, Settings

    for действие, фраза, ответ in (
        ("calendar", "что у меня завтра", "Завтра: 14:30 — врач"),
        ("remind", "напомни завтра про врача", "Записал на завтра: врача"),
    ):
        monkeypatch.setattr(
            app,
            "execute",
            lambda routed, apps, channels, new_tab=True, people=None, config=None, _о=ответ:
                ActionResult(True, _о),
        )
        config = Config(
            apps={},
            commands=[CommandRule(фраза, действие, "-")],
            settings=Settings("джони", "", "off", "medium", "cuda"),
        )
        sp = SpySpeaker(answer_played=True)   # звук ГОТОВ проиграться, но не должен
        handle_command(фраза, config, sp)
        assert sp.said == [ответ], действие
        assert sp.answer_calls == 0, f"{действие} не должен даже пытаться играть звук"
