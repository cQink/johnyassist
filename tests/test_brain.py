import pytest

from johnny import brain, brain_claude
from johnny.brain import BrainResult, interpret, make_providers, punctuate
from johnny.config import CommandRule, Config, Settings
from johnny.router import RoutedAction


def _provider(answer):
    """Один провайдер-заглушка под сигнатуру interpret."""
    return [("тест", lambda prompt: answer)]


CORRECTOR_COMMANDS = [
    CommandRule("громкость *", "set_volume", "{0}"),
    CommandRule("сверни всё", "system", "minimize_all"),
]

UNSAFE_CORRECTOR_COMMANDS = CORRECTOR_COMMANDS + [
    CommandRule("выключи компьютер", "system", "shutdown"),
]


def test_interpret_action_response():
    result = interpret(
        "глянь стрим x",
        providers=_provider('{"action": "open_url", "argument": "twitch.tv/x", "reply": "Открываю"}'),
    )
    assert result == BrainResult(routed=RoutedAction("open_url", "twitch.tv/x"), reply="Открываю")


def test_interpret_answer_response():
    result = interpret(
        "какая погода", providers=_provider('{"action": "answer", "reply": "Сейчас в Москве +20."}')
    )
    assert result == BrainResult(routed=None, reply="Сейчас в Москве +20.")


def test_interpret_extracts_json_from_surrounding_text():
    result = interpret(
        "что-то", providers=_provider('Конечно!\n{"action": "answer", "reply": "Готово"}\nСпасибо')
    )
    assert result.reply == "Готово"


def test_interpret_plain_text_is_spoken_answer():
    # обычный текст без JSON — это разговорный ответ, озвучиваем как есть
    result = interpret("столица франции", providers=_provider("Париж — столица Франции."))
    assert result == BrainResult(routed=None, reply="Париж — столица Франции.")


def test_interpret_empty_output_returns_none():
    # пусто у всех провайдеров = моделей нет на связи → None
    assert interpret("что-то", providers=_provider("")) is None
    assert interpret("что-то", providers=_provider("   \n")) is None


def test_correction_is_rerouted():
    result = interpret("академика с 5", CORRECTOR_COMMANDS, _provider('{"command": "громкость 5"}'))
    assert result.routed == RoutedAction("set_volume", "5")


def test_invented_command_is_rejected():
    # Такой команды в конфиге нет — выполнять нечего.
    result = interpret("станцуй", CORRECTOR_COMMANDS, _provider('{"command": "станцуй лезгинку"}'))
    assert result.routed is None


def test_action_mode_still_works():
    result = interpret(
        "открой сайт погоды",
        CORRECTOR_COMMANDS,
        _provider('{"action": "open_url", "argument": "pogoda.ru", "reply": "Открываю"}'),
    )
    assert result.routed == RoutedAction("open_url", "pogoda.ru")
    assert result.reply == "Открываю"


def test_conversation_still_works():
    result = interpret("столица японии", CORRECTOR_COMMANDS, _provider("Токио, сэр."))
    assert result.routed is None
    assert result.reply == "Токио, сэр."


def test_commands_are_listed_in_prompt():
    seen = {}

    def fake(prompt):
        seen["prompt"] = prompt
        return "ок"

    interpret("что-то", CORRECTOR_COMMANDS, [("тест", fake)])
    assert "громкость *" in seen["prompt"]
    assert "сверни всё" in seen["prompt"]


def test_correction_to_destructive_command_is_rejected():
    # «включи компьютер» не похоже ни на что, но модель может «исправить»
    # его на «выключи компьютер» (0.97 похожести на буквальном сравнении) —
    # такое исправление обязано быть отклонено, а не выполнено через route().
    result = interpret(
        "включи компьютер", UNSAFE_CORRECTOR_COMMANDS, _provider('{"command": "выключи компьютер"}')
    )
    assert result.routed is None


def test_action_destructive_is_rejected():
    result = interpret(
        "выключи",
        CORRECTOR_COMMANDS,
        _provider('{"action": "system", "argument": "shutdown", "reply": "Выключаю"}'),
    )
    assert result.routed is None


def test_unsafe_commands_are_omitted_from_prompt():
    seen = {}

    def fake(prompt):
        seen["prompt"] = prompt
        return "ок"

    interpret("что-то", UNSAFE_CORRECTOR_COMMANDS, [("тест", fake)])
    assert "выключи компьютер" not in seen["prompt"]
    # безопасные команды по-прежнему должны присутствовать
    assert "громкость *" in seen["prompt"]


def test_action_via_is_provider_name():
    # via должен называть того, кто ответил, а не дефолтную «точно» —
    # иначе история команд врёт.
    result = interpret(
        "открой блокнот",
        CORRECTOR_COMMANDS,
        _provider('{"action": "launch_app", "argument": "notepad", "reply": "Открываю"}'),
    )
    assert result.routed.via == "тест"


def test_json_without_recognized_keys_is_not_spoken_as_raw_json():
    # Валидный JSON, но ни «command», ни распознанный «action» — озвучивать
    # такое как есть (сырой JSON) нельзя, это не то же самое, что «Готово».
    result = interpret("что-то", CORRECTOR_COMMANDS, _provider('{"foo": "bar"}'))
    assert result.routed is None
    assert result.reply is None


def test_first_answering_provider_wins():
    called = []

    def first(prompt):
        called.append("first")
        return "Ответ первого."

    def second(prompt):
        called.append("second")
        return "Ответ второго."

    result = interpret("что-то", providers=[("первый", first), ("второй", second)])
    assert result.reply == "Ответ первого."
    assert result.provider == "первый"
    assert called == ["first"]      # второго не трогали


def test_empty_providers_list_does_not_fall_back_to_claude(monkeypatch):
    # providers=[] явно означает «провайдеров нет», а не «не передали».
    # Баг `providers or [...]` не отличал пустой список от None и в этом
    # случае молча уходил бы на живой claude -p.
    called = []
    monkeypatch.setattr(brain_claude, "run", lambda prompt: called.append(prompt) or "не должно вызваться")
    assert interpret("что-то", providers=[]) is None
    assert called == []


def test_empty_provider_falls_through_to_next():
    def first(prompt):
        return ""

    def second(prompt):
        return "Ответ второго."

    result = interpret("что-то", providers=[("первый", first), ("второй", second)])
    assert result.reply == "Ответ второго."
    assert result.provider == "второй"


def _config(secrets):
    return Config(
        apps={},
        commands=[],
        settings=Settings("джони", "", "voice", "medium", "cuda"),
        secrets=secrets,
    )


def _config_with(secrets, **settings):
    config = _config(secrets)
    for field, value in settings.items():
        setattr(config.settings, field, value)
    return config


def test_make_providers_puts_groq_first_when_key_present():
    names = [name for name, _ in make_providers(_config({"groq_api_key": "g"}))]
    assert names == ["groq", "claude"]


def test_make_providers_without_key_is_claude_only():
    names = [name for name, _ in make_providers(_config({}))]
    assert names == ["claude"]


def test_strong_brain_sits_between_groq_and_claude_cli():
    """Позиция = политика расходов: Opus 5 включается там, где не смог Groq."""
    config = _config_with({"groq_api_key": "g", "anthropic_api_key": "a"}, strong_brain="opus")
    assert [name for name, _ in make_providers(config)] == ["groq", "opus", "claude"]


def test_strong_brain_first_makes_it_the_main_brain():
    config = _config_with(
        {"groq_api_key": "g", "anthropic_api_key": "a"},
        strong_brain="opus",
        strong_brain_first=True,
    )
    assert [name for name, _ in make_providers(config)] == ["opus", "groq", "claude"]


def test_gpt_slot_is_interchangeable_with_opus():
    """Слот один и сменный — опрашивать обе значило бы платить дважды за ответ."""
    config = _config_with({"openai_api_key": "o"}, strong_brain="gpt")
    assert [name for name, _ in make_providers(config)] == ["gpt", "claude"]


def test_key_without_setting_does_not_enable_strong_brain():
    """Ключ значит «могу», strong_brain — «хочу». Оплата ключа не включает траты."""
    config = _config({"anthropic_api_key": "a", "openai_api_key": "o"})
    assert [name for name, _ in make_providers(config)] == ["claude"]


def test_setting_without_key_does_not_enable_strong_brain():
    config = _config_with({}, strong_brain="opus")
    assert [name for name, _ in make_providers(config)] == ["claude"]


def test_unknown_strong_brain_name_is_ignored():
    """Опечатка в настройке не должна валить запуск — модель просто выключена."""
    config = _config_with({"anthropic_api_key": "a"}, strong_brain="opuss")
    assert [name for name, _ in make_providers(config)] == ["claude"]


def test_address_of_the_strong_brain_reaches_the_module(monkeypatch):
    """Адрес посредника должен доехать от настроек до модуля модели.

    Потеряйся он здесь — запрос уйдёт на официальный сервер, где ключ
    посредника неизвестен, и вместо ответа придёт 401. Проверяем именно
    проводку: сам модуль про настройки ничего не знает и знать не должен.
    """
    import johnny.brain_anthropic as brain_anthropic

    seen = []
    monkeypatch.setattr(
        brain_anthropic,
        "make_provider",
        lambda key, model="", base_url="": seen.append((key, model, base_url)) or (lambda p: ""),
    )
    config = _config_with(
        {"anthropic_api_key": "a"},
        strong_brain="opus",
        strong_brain_base_url="https://agentrouter.org",
    )
    make_providers(config)
    assert seen == [("a", "claude-opus-5", "https://agentrouter.org")]


def test_without_address_the_strong_brain_stays_official(monkeypatch):
    """Пустая настройка = официальный адрес, то есть поведение до посредника."""
    import johnny.brain_anthropic as brain_anthropic

    seen = []
    monkeypatch.setattr(
        brain_anthropic,
        "make_provider",
        lambda key, model="", base_url="": seen.append(base_url) or (lambda p: ""),
    )
    make_providers(_config_with({"anthropic_api_key": "a"}, strong_brain="opus"))
    assert seen == [""]


CHAIN_COMMANDS = [
    CommandRule("открой твич", "browser_open", "twitch.tv"),
    CommandRule("громкость *", "set_volume", "{0}"),
    CommandRule("выключи компьютер", "system", "shutdown"),
]


def test_steps_are_routed():
    result = interpret(
        "открой твич и громкость 20",
        CHAIN_COMMANDS,
        _provider('{"steps": ["открой твич", "громкость 20"]}'),
    )
    assert [(s.action, s.argument) for s in result.steps] == [
        ("browser_open", "twitch.tv"),
        ("set_volume", "20"),
    ]


def test_unknown_step_rejects_whole_chain():
    result = interpret(
        "что-то",
        CHAIN_COMMANDS,
        _provider('{"steps": ["открой твич", "поговори со мной"]}'),
    )
    assert result.steps is None


def test_unsafe_step_rejects_whole_chain():
    result = interpret(
        "что-то",
        CHAIN_COMMANDS,
        _provider('{"steps": ["открой твич", "выключи компьютер"]}'),
    )
    assert result.steps is None


def test_too_many_steps_rejected():
    steps = ", ".join(['"открой твич"'] * 6)
    result = interpret("что-то", CHAIN_COMMANDS, _provider('{"steps": [' + steps + "]}"))
    assert result.steps is None


def test_punctuate_uses_first_provider_that_answers():
    result = punctuate("привет как дела я иду домой", _provider("Привет, как дела? Я иду домой."))
    assert result == "Привет, как дела? Я иду домой."


def test_punctuate_falls_back_to_original_text_when_providers_empty():
    # Диктовка не должна теряться из-за недоступности Groq/claude -p.
    result = punctuate("привет как дела", _provider(""))
    assert result == "привет как дела"


def test_punctuate_empty_text_skips_providers_entirely():
    calls = []
    providers = [("тест", lambda prompt: calls.append(prompt) or "неважно")]
    assert punctuate("", providers) == ""
    assert calls == []


def test_punctuate_strips_surrounding_quotes_from_answer():
    result = punctuate("привет как дела", _provider('"Привет, как дела?"'))
    assert result == "Привет, как дела?"


def test_punctuate_tries_next_provider_if_first_is_empty():
    providers = [("groq", lambda prompt: ""), ("claude", lambda prompt: "Привет, мир.")]
    assert punctuate("привет мир", providers) == "Привет, мир."


def test_memory_block_is_included_in_prompt():
    seen = {}

    def fake(prompt):
        seen["prompt"] = prompt
        return "ок"

    interpret(
        "что-то",
        CORRECTOR_COMMANDS,
        [("тест", fake)],
        memory_block="Известно о пользователе:\n- любит кофе без сахара",
    )
    assert "любит кофе без сахара" in seen["prompt"]


def test_empty_memory_block_still_produces_valid_prompt():
    # По умолчанию memory_block="" — прежнее поведение без памяти не должно
    # ломаться (interpret вызывается без этого аргумента в старых тестах).
    result = interpret("столица японии", CORRECTOR_COMMANDS, _provider("Токио, сэр."))
    assert result.reply == "Токио, сэр."


def test_prompt_has_no_example_that_can_be_parroted_as_an_answer():
    """Живой баг 2026-08-09: на «как у тебя дела» Джони отвечал
    «Не расслышал, повторите, пожалуйста».

    Причина была не в распознавании — в истории фраза записана верно
    («как дела [слитно/groq]»). В промпте эта фраза стояла ПРИМЕРОМ стиля
    обращения, и Llama 70B копировала её дословно как готовый ответ.

    Пример реплики годится в промпт, только если его нельзя выдать за ответ
    на произвольный вопрос: «открываю браузер» привязано к действию и
    попугаем не звучит, а «не расслышал, повторите» подходит куда угодно.
    """
    from johnny.brain import _PROMPT

    assert "не расслышал" not in _PROMPT.lower()
    assert "повторите" not in _PROMPT.lower()


def test_prompt_tells_the_model_to_answer_questions_directly():
    """Вторая половина того же бага: вместо ответа модель объясняла, что это
    не команда («вы просто спросили "как дела", но это не команда»)."""
    from johnny.brain import _PROMPT

    assert "ПРОСТО ОТВЕТЬ НА ВОПРОС" in _PROMPT


# ── Разговорная ветка через конвейер (interpret_streamed) ──────────────────


@pytest.fixture
def sample_commands():
    """Независимый от CORRECTOR_COMMANDS набор: важно только, что «громкость
    5» маршрутизируется в системное действие, а не какие у него аргументы."""
    return [CommandRule("громкость *", "system", "volume_{0}")]


def test_streamed_answer_is_marked_as_already_spoken():
    """Главное в интеграции: реплику, прозвучавшую по ходу потока, нельзя
    произнести второй раз — человек услышал бы её дважды."""
    def speak(chunks):
        return brain.say_stream.StreamResult(text="Дела отлично", spoken=True)

    result = brain.interpret_streamed("как дела", [], lambda prompt: iter([]), speak)
    assert result.reply == "Дела отлично"
    assert result.spoken is True


def test_streamed_json_is_parsed_exactly_like_the_ordinary_path(sample_commands):
    """Командная ветка не озвучивается и разбирается тем же кодом: разойдясь,
    два разбора начали бы понимать один и тот же JSON по-разному."""
    def speak(chunks):
        return brain.say_stream.StreamResult(
            text='{"command": "громкость 5"}', spoken=False
        )

    result = brain.interpret_streamed("громкость пять", sample_commands,
                                      lambda prompt: iter([]), speak)
    assert result.spoken is False
    assert result.routed is not None
    assert result.routed.action == "system"


def test_streamed_prose_before_json_reply_is_not_marked_spoken():
    """Модель иногда предваряет JSON прозой («Конечно, сейчас посмотрю.»,
    см. спеку). say_stream озвучивает эту вводную фразу и ставит
    result.spoken=True, но сам JSON (а значит и "reply" внутри него) он
    защёлкивает молча, встретив «{» — реального ответа человек ещё не
    слышал. Если result.spoken протащить в _parse как есть, reply из JSON
    унаследует чужое "прозвучало", _speak_reply в app.py его пропустит, и
    человек услышит только вводную фразу и тишину вместо настоящего
    ответа."""
    def speak(chunks):
        return brain.say_stream.StreamResult(
            text='Конечно, сейчас посмотрю. {"action": "answer", "reply": "Настоящий ответ"}',
            spoken=True,
        )

    result = brain.interpret_streamed("расскажи анекдот", [], lambda prompt: iter([]), speak)
    assert result.reply == "Настоящий ответ"
    assert result.spoken is False


def test_streamed_returns_none_when_the_pipeline_is_unavailable():
    """None от say_stream значит «конвейера нет» — зовущий обязан уйти на
    обычный interpret, а не замолчать."""
    result = brain.interpret_streamed("как дела", [], lambda prompt: iter([]),
                                      lambda chunks: None)
    assert result is None


def test_streamed_returns_none_when_nothing_was_said_and_stream_broke():
    """Оборвалось до первого слова — пусть отвечает следующий провайдер."""
    def speak(chunks):
        return brain.say_stream.StreamResult(text="", spoken=False, broken=True)

    assert brain.interpret_streamed("как дела", [], lambda p: iter([]), speak) is None


def test_ordinary_interpret_still_reports_not_spoken():
    """Старый путь обязан остаться прежним: его реплику озвучивает app."""
    result = brain.interpret("привет", [], [("тест", lambda prompt: "Здравствуйте")])
    assert result.spoken is False
