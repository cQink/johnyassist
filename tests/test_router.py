from johnny.config import CommandRule
from johnny.router import (
    route,
    route_exact,
    RoutedAction,
    contains_stop_word,
    is_stop_word,
    is_unsafe_action,
)

COMMANDS = [
    CommandRule("открой канал * на твиче", "open_url", "twitch.tv/{0}"),
    CommandRule("найди на ютубе *", "open_url", "youtube.com/results?search_query={0}"),
    CommandRule("открой ютуб", "open_url", "youtube.com"),
    CommandRule("запусти *", "launch_app", "{0}"),
    CommandRule("сделай громче", "system", "volume_up"),
]


def test_pattern_with_capture_fills_template():
    assert route("открой канал 9impulse на твиче", COMMANDS) == RoutedAction(
        "open_url", "twitch.tv/9impulse"
    )


def test_capture_can_span_several_words():
    assert route("найди на ютубе как варить борщ", COMMANDS) == RoutedAction(
        "open_url", "youtube.com/results?search_query=как варить борщ"
    )


def test_pattern_without_capture():
    assert route("открой ютуб", COMMANDS) == RoutedAction("open_url", "youtube.com")


def test_launch_app_passes_name_as_argument():
    assert route("запусти дота", COMMANDS) == RoutedAction("launch_app", "дота")


def test_normalizes_case_and_trailing_punctuation():
    assert route("Сделай громче!", COMMANDS) == RoutedAction("system", "volume_up")


def test_no_match_returns_none():
    assert route("расскажи анекдот", COMMANDS) is None


def test_normalizes_yo_to_ye():
    cmds = [CommandRule("сверни всё", "system", "minimize_all")]
    assert route("сверни все", cmds) == RoutedAction("system", "minimize_all")


def test_fuzzy_matches_near_miss():
    # «зделай» вместо «сделай» — ослышка Whisper, всё равно ловим команду
    assert route("зделай громче", COMMANDS) == RoutedAction("system", "volume_up")


def test_fuzzy_does_not_match_far_text():
    # непохожая фраза не должна цепляться к команде — уходит к Claude (None)
    assert route("расскажи анекдот пожалуйста", COMMANDS) is None


def test_fuzzy_skips_wildcard_patterns():
    # шаблонные команды в нечёткий матч не попадают (у них переменная часть)
    only_wildcard = [CommandRule("запусти *", "launch_app", "{0}")]
    assert route("запсти", only_wildcard) is None


UNSAFE_COMMANDS = [
    CommandRule("выключи компьютер", "system", "shutdown"),
    CommandRule("перезагрузи компьютер", "system", "restart"),
    CommandRule("усыпи компьютер", "system", "sleep"),
    CommandRule("отмени выключение", "system", "cancel_shutdown"),
]


def test_unsafe_command_needs_exact_match():
    # 0.97 похожести, но выключать компьютер по догадке нельзя.
    assert route("включи компьютер", UNSAFE_COMMANDS) is None


def test_unsafe_command_still_works_on_exact_match():
    assert route("выключи компьютер", UNSAFE_COMMANDS) == RoutedAction(
        "system", "shutdown"
    )


def test_cancel_shutdown_is_not_unsafe():
    # Отмена выключения безопасна — её угадывать можно.
    assert route("отмени выключения", UNSAFE_COMMANDS) == RoutedAction(
        "system", "cancel_shutdown"
    )


def test_is_unsafe_action_flags_destructive_system_commands():
    # Публичный хелпер — единственное определение «разрушительности»,
    # им же должен пользоваться brain, проверяя ответы модели.
    assert is_unsafe_action("system", "shutdown") is True
    assert is_unsafe_action("system", "restart") is True
    assert is_unsafe_action("system", "sleep") is True


def test_is_unsafe_action_allows_safe_system_commands():
    assert is_unsafe_action("system", "cancel_shutdown") is False
    assert is_unsafe_action("system", "lock") is False


def test_is_unsafe_action_allows_non_system_actions():
    assert is_unsafe_action("open_url", "shutdown") is False


def test_is_stop_word_matches_case_and_punctuation_insensitively():
    # Используется в app.py (idle-путь) — «стоп» не должно уходить в
    # route()/модель-корректор. Busy-путь (controller.py) с 2026-08-03
    # использует contains_stop_word (потоковая проверка, слово не обязано
    # быть всей фразой целиком) — см. тесты ниже.
    assert is_stop_word("стоп") is True
    assert is_stop_word("Стоп!") is True
    assert is_stop_word("  СТОП  ") is True


def test_is_stop_word_rejects_other_phrases():
    assert is_stop_word("останови") is False
    assert is_stop_word("стоп музыка") is False
    assert is_stop_word("") is False


def test_is_stop_word_accepts_latin_spelling():
    """ЖИВОЙ ЛОГ (2026-08-03): Whisper несколько раз распознал «стоп»
    латиницей ('stop'), и ни разу это не сработало со старой проверкой."""
    assert is_stop_word("stop") is True
    assert is_stop_word("Stop!") is True


def test_contains_stop_word_finds_it_alongside_other_words():
    """Для потоковой проверки, пока Джони занят, — «стоп» может быть НЕ
    единственным словом (рядом почти всегда имя «Джони»)."""
    assert contains_stop_word("джони стоп") is True
    assert contains_stop_word("джони стоп пожалуйста") is True
    assert contains_stop_word("джони") is False
    assert contains_stop_word("джони громкость пять") is False
    assert contains_stop_word("") is False


NUMBER_COMMANDS = [
    CommandRule("громкость *", "set_volume", "{0}"),
    CommandRule("включи первое видео", "browser_click", "1"),
    CommandRule("на второй монитор", "move_window", "2"),
]


def test_number_word_becomes_digit():
    assert route("громкость пять", NUMBER_COMMANDS) == RoutedAction("set_volume", "5")


def test_compound_number_word():
    assert route("громкость двадцать пять", NUMBER_COMMANDS) == RoutedAction(
        "set_volume", "25"
    )


def test_round_tens_number_word():
    assert route("громкость пятьдесят", NUMBER_COMMANDS) == RoutedAction(
        "set_volume", "50"
    )


def test_hundred_number_word():
    assert route("громкость сто", NUMBER_COMMANDS) == RoutedAction("set_volume", "100")


def test_ordinal_words_are_untouched():
    # «первое»/«второй» — порядковые, их перевод сломал бы эти команды.
    assert route("включи первое видео", NUMBER_COMMANDS) == RoutedAction(
        "browser_click", "1"
    )
    assert route("на второй монитор", NUMBER_COMMANDS) == RoutedAction(
        "move_window", "2"
    )


TEMPLATE_COMMANDS = [
    CommandRule("громкость *", "set_volume", "{0}"),
    CommandRule("открой канал * на твиче", "open_channel", "twitch|{0}"),
    CommandRule("перемотай на *", "browser_seek", "{0}"),
    CommandRule("выключи компьютер", "system", "shutdown"),
]


def test_misheard_head_still_matches_template():
    # Живой случай: Whisper услышал «Стронкость 5» вместо «громкость 5».
    assert route("стронкость 5", TEMPLATE_COMMANDS) == RoutedAction("set_volume", "5")


def test_misheard_head_with_tail():
    assert route("открой канал серега на твоче", TEMPLATE_COMMANDS) == RoutedAction(
        "open_channel", "twitch|серега"
    )


def test_template_without_argument_is_rejected():
    # «громкость» без числа не должна уехать в set_volume с мусором.
    assert route("стронкость", TEMPLATE_COMMANDS) is None


WIDE_GUESS_COMMANDS = TEMPLATE_COMMANDS + [
    CommandRule("найди на твиче *", "browser_open", "twitch.tv/search?term={0}"),
]


def test_ordinary_speech_does_not_route_to_unrelated_template():
    # «карте» вместо «твиче» — только 0.4 похожести, но взвешенное среднее
    # по всей голове («найди на карте» ≈ «найди на твиче» на 0.79) раньше
    # маскировало это и уводило обычную речь в твич-поиск.
    assert route("найди на карте аптеку", WIDE_GUESS_COMMANDS) is None


def test_single_word_head_still_matches_on_its_own_ratio():
    # «стронкость» ≈ «громкость» = 0.74 — единственное слово головы, порог
    # (0.72) должен по-прежнему проходить сам по себе.
    assert route("стронкость 5", WIDE_GUESS_COMMANDS) == RoutedAction("set_volume", "5")


def test_too_different_head_is_rejected():
    # 0.20 похожести — угадывать не по чему, уходит к Claude.
    assert route("академика с 5", TEMPLATE_COMMANDS) is None


def test_exact_match_still_wins():
    assert route("громкость 5", TEMPLATE_COMMANDS) == RoutedAction("set_volume", "5")


def test_multiword_argument_survives():
    assert route("перемотай на 13 42", TEMPLATE_COMMANDS) == RoutedAction(
        "browser_seek", "13 42"
    )


def test_exact_match_reports_via():
    assert route("открой ютуб", COMMANDS).via == "точно"


def test_fuzzy_match_reports_ratio():
    assert route("зделай громче", COMMANDS).via.startswith("похоже")


def test_via_does_not_break_equality():
    # Сравнение действий не должно зависеть от способа совпадения.
    assert route("зделай громче", COMMANDS) == RoutedAction("system", "volume_up")


def test_route_exact_matches_template():
    assert route_exact("запусти обс", COMMANDS) == RoutedAction("launch_app", "обс")


def test_route_exact_literal_only_skips_templates():
    assert route_exact("запусти обс", COMMANDS, literal_only=True) is None


def test_route_exact_literal_only_matches_fixed_phrase():
    assert route_exact("открой ютуб", COMMANDS, literal_only=True) == RoutedAction(
        "open_url", "youtube.com"
    )


def test_route_exact_does_not_guess():
    """Кривую фразу route() поймает нечётко, а route_exact обязана промолчать."""
    assert route("сделай громще", COMMANDS) is not None
    assert route_exact("сделай громще", COMMANDS) is None


def test_звонок_не_угадывается():
    from johnny.config import CommandRule
    from johnny.router import route

    commands = [CommandRule(pattern="позвони *", action="discord_call", template="{0}")]

    # Точно — работает
    assert route("позвони гоше", commands).action == "discord_call"
    # «позвонить гоше» похожа на «позвони *» на 0.88 — выше порога нечёткого
    # совпадения (0.72). Без защиты это дало бы RoutedAction(via="похоже").
    # Именно эта пара доказывает, что защита работает, а не то, что порог
    # случайно не дотягивается.
    assert route("позвонить гоше", commands) is None
    # Похоже — НЕ работает: звонок живому человеку угадывать нельзя
    assert route("покажи гошу", commands) is None


# --- Discord на НАСТОЯЩЕМ config/commands.yaml ---
# Всё, что ниже, проверяет живой конфиг, а не выдуманные правила: порядок
# правил в файле и целость фразы — это свойства именно файла, и подделка
# правил в тесте про них ничего не докажет.


def _shipped_commands():
    from johnny.config import load_config

    return load_config("config").commands


def _shipped_people():
    from johnny.config import load_config

    return load_config("config").people


def test_обе_формулировки_напиши_дают_того_же_адресата_и_тот_же_текст():
    """Порядок правил в commands.yaml: «напиши сообщение *» ПЕРЕД «напиши *».

    При обратном порядке жадный шаблон забирает фразу себе, слово «сообщение»
    уезжает в имя, split_message возвращает None — и вариант команды тихо
    умирает («Не понял, кому писать») без единого падающего теста.
    """
    from johnny import contacts

    commands = _shipped_commands()
    people = _shipped_people()
    for phrase in ("напиши гоше привет", "напиши сообщение гоше привет"):
        routed = route(phrase, commands)
        assert routed is not None and routed.action == "discord_message", phrase
        split = contacts.split_message(routed.argument, people)
        assert split is not None, f"«{phrase}»: адресат не выделился из {routed.argument!r}"
        person, text = split
        assert person["discord"] == "Гречка", phrase
        assert text == "привет", phrase


def test_продиктованный_текст_не_режется_цепочкой():
    """Фраза с союзом внутри текста разбирается целиком, а не режется.

    Проверяется на живом конфиге ровно так, как это происходит в app: сначала
    сплиттер цепочек, потом обычный разбор.
    """
    import johnny.chain as chain

    commands = _shipped_commands()
    for phrase, expected in (
        ("напиши гоше я приду потом включи музыку", "гоше я приду потом включи музыку"),
        ("напиши гоше привет и пока", "гоше привет и пока"),
    ):
        assert chain.split_local(phrase, commands) is None, f"«{phrase}» разрезали на цепочку"
        routed = route(phrase, commands)
        assert routed.action == "discord_message"


def test_app_focus_catchall_does_not_shadow_specific_open_phrases():
    """"открой *"/"фокус *" (app_focus) стоят в конце commands.yaml специально —
    более ранние конкретные "открой ютуб"/"открой браузер" и т.п. обязаны
    сработать первыми. Если кто-то переставит порядок в файле, этот тест
    должен упасть."""
    commands = _shipped_commands()
    for phrase, expected_action in (
        ("открой ютуб", "browser_open"),
        ("открой твич", "browser_open"),
        ("открой браузер", "browser_focus"),
        ("открой хром", "browser_focus"),
        ("открой на полный экран", "browser_fullscreen"),
        ("открой первое видео", "browser_click_result"),
        ("открой второе видео", "browser_click_result"),
        ("открой третье видео", "browser_click_result"),
    ):
        routed = route_exact(phrase, commands)
        assert routed is not None and routed.action == expected_action, phrase


def test_app_focus_catchall_catches_other_app_names():
    commands = _shipped_commands()
    for phrase in ("открой обс", "фокус обс", "открой повершелл", "фокус телеграм"):
        routed = route_exact(phrase, commands)
        assert routed is not None and routed.action == "app_focus", phrase


def test_обыденное_слово_не_считается_именем_человека():
    """«отправь тест на почту» не должно уходить сообщением живому аккаунту.

    У «твикса» был алиас «тест», и фраза отправляла на этот аккаунт текст
    «на почту». Алиас человека обязан быть именем, а не общим словом.
    """
    from johnny import contacts

    routed = route("отправь тест на почту", _shipped_commands())
    assert routed.action == "discord_message"
    assert contacts.split_message(routed.argument, _shipped_people()) is None


def test_напечатай_вводит_текст_а_напиши_остаётся_discord():
    """«напечатай» — отдельное слово для ввода текста (не «напиши»): то уже
    занято под Discord-сообщения («напиши Стасу привет»). Оба смысла —
    свободный текст после глагола, шаблонами неотличимы, поэтому слова
    обязаны быть разными."""
    commands = _shipped_commands()

    typed = route("напечатай привет как дела", commands)
    assert typed is not None and typed.action == "type_text"
    assert typed.argument == "привет как дела"

    messaged = route("напиши гоше привет", commands)
    assert messaged is not None and messaged.action == "discord_message"


def test_память_команды_резолвятся_на_реальном_конфиге():
    commands = _shipped_commands()

    remembered = route("запомни у меня стим аккаунт art_vol_teror", commands)
    assert remembered is not None and remembered.action == "remember"
    assert remembered.argument == "у меня стим аккаунт art_vol_teror"

    forgotten = route("забудь про стим аккаунт", commands)
    assert forgotten is not None and forgotten.action == "forget"

    listed = route("что ты помнишь", commands)
    assert listed is not None and listed.action == "list_memory"

    # «поправь X на Y» обязана дойти до update_fact на боевом конфиге: правила
    # стоят выше ловящего всё "открой */фокус *" в конце файла, иначе фраза
    # уехала бы в app_focus и Джони искал бы программу «стим-аккаунт».
    fixed = route("поправь стим-аккаунт на art_vol_teror", commands)
    assert fixed is not None and fixed.action == "update_fact"
    assert fixed.argument == "стим-аккаунт на art_vol_teror"

    corrected = route("исправь ник на flynes_", commands)
    assert corrected is not None and corrected.action == "update_fact"
