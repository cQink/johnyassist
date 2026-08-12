import johnny.actions as actions
from johnny import keyboard
from johnny.actions import execute, ActionResult
from johnny.actions import browser as actions_browser
from johnny.actions import system as actions_system
from johnny.actions import windows as actions_windows
from johnny.actions import steam as actions_steam
from johnny.actions import discord_action as actions_discord
from johnny.actions import keyboard_action as actions_keyboard
from johnny.actions import memory_action as actions_memory
from johnny.router import RoutedAction


def test_execute_open_url_calls_browser(monkeypatch):
    opened = {}
    monkeypatch.setattr(actions_browser, "webbrowser", type("M", (), {"open": staticmethod(lambda url: opened.setdefault("url", url))}))
    result = execute(RoutedAction("open_url", "twitch.tv/9impulse"), apps={})
    assert opened["url"] == "https://twitch.tv/9impulse"
    assert result.ok is True


def test_open_url_keeps_existing_scheme(monkeypatch):
    opened = {}
    monkeypatch.setattr(actions_browser, "webbrowser", type("M", (), {"open": staticmethod(lambda url: opened.setdefault("url", url))}))
    actions_browser.open_url("steam://rungameid/570")
    assert opened["url"] == "steam://rungameid/570"


def test_execute_launch_known_app(monkeypatch):
    launched = {}
    monkeypatch.setattr(actions_windows, "_start", lambda target: launched.setdefault("t", target))
    result = execute(RoutedAction("launch_app", "дота"), apps={"дота": "steam://rungameid/570"})
    assert launched["t"] == "steam://rungameid/570"
    assert result == ActionResult(True, "Запускаю дота")


def test_execute_launch_unknown_app_reports_failure():
    result = execute(RoutedAction("launch_app", "нечто"), apps={})
    assert result == ActionResult(False, "Не знаю такой программы")


def test_app_focus_unknown_app_returns_failure():
    result = actions_windows.action_app_focus("нечто", {"apps": {}})
    assert result.ok is False


def test_execute_app_focus_unknown_app_reports_failure():
    result = execute(RoutedAction("app_focus", "нечто"), apps={})
    assert result == ActionResult(False, "Не знаю такой программы")


def test_app_focus_focuses_existing_window_without_launching(monkeypatch):
    import johnny.discord_ui as discord_ui
    import johnny.windows as windows

    focused = []
    monkeypatch.setattr(windows, "find_window_by_exe", lambda exe: 42 if exe == "obs64.exe" else None)
    monkeypatch.setattr(discord_ui, "focus", lambda hwnd: focused.append(hwnd) or True)
    monkeypatch.setattr(actions_windows, "_start", lambda target: (_ for _ in ()).throw(AssertionError("не должны запускать")))

    apps = {"обс": '"C:/Program Files/obs-studio/bin/64bit/obs64.exe"'}
    result = actions_windows.action_app_focus("обс", {"apps": apps})
    assert result.ok is True
    assert focused == [42]


def test_app_focus_launches_when_not_running(monkeypatch):
    import johnny.windows as windows

    monkeypatch.setattr(windows, "find_window_by_exe", lambda exe: None)
    started = {}
    monkeypatch.setattr(actions_windows, "_start", lambda target: started.setdefault("t", target))

    apps = {"обс": '"C:/Program Files/obs-studio/bin/64bit/obs64.exe"'}
    result = actions_windows.action_app_focus("обс", {"apps": apps})
    assert result.ok is True
    assert started["t"] == apps["обс"]


def test_app_focus_skips_window_search_for_url_scheme(monkeypatch):
    """Steam-игры (steam://rungameid/...) не exe — не пытаемся искать окно."""
    import johnny.windows as windows

    monkeypatch.setattr(
        windows, "find_window_by_exe", lambda exe: (_ for _ in ()).throw(AssertionError("не звать"))
    )
    started = {}
    monkeypatch.setattr(actions_windows, "_start", lambda target: started.setdefault("t", target))

    apps = {"дота": "steam://rungameid/570"}
    result = actions_windows.action_app_focus("дота", {"apps": apps})
    assert result.ok is True
    assert started["t"] == "steam://rungameid/570"


def test_execute_system_calls_handler(monkeypatch):
    called = {}
    monkeypatch.setitem(actions_system.SYSTEM_HANDLERS, "volume_up", lambda: called.setdefault("v", True))
    result = execute(RoutedAction("system", "volume_up"), apps={})
    assert called.get("v") is True
    assert result.ok is True


def test_execute_unknown_system_command_reports_failure():
    result = execute(RoutedAction("system", "не_существует"), apps={})
    assert result.ok is False


def test_parse_volume():
    assert actions_system.parse_volume("5") == 50
    assert actions_system.parse_volume("2") == 20
    assert actions_system.parse_volume("10") == 100
    assert actions_system.parse_volume("50") == 50
    assert actions_system.parse_volume("80") == 80
    assert actions_system.parse_volume("громкость 3") == 30
    assert actions_system.parse_volume("нет числа") is None


def test_execute_set_volume(monkeypatch):
    scalars = []
    monkeypatch.setattr(actions_system, "_set_scalar", lambda v: scalars.append(v))
    result = execute(RoutedAction("set_volume", "5"), apps={})
    assert scalars == [0.5]
    assert result.ok is True


def test_execute_volume_delta_down(monkeypatch):
    scalars = []
    monkeypatch.setattr(actions_system, "_get_scalar", lambda: 0.7)
    monkeypatch.setattr(actions_system, "_set_scalar", lambda v: scalars.append(round(v, 2)))
    result = execute(RoutedAction("volume_delta", "-20 процентов"), apps={})
    assert scalars == [0.5]
    assert result.ok is True


def test_execute_volume_delta_up(monkeypatch):
    scalars = []
    monkeypatch.setattr(actions_system, "_get_scalar", lambda: 0.3)
    monkeypatch.setattr(actions_system, "_set_scalar", lambda v: scalars.append(round(v, 2)))
    execute(RoutedAction("volume_delta", "+30"), apps={})
    assert scalars == [0.6]


def test_execute_type_text_types_into_focused_field(monkeypatch):
    """«Джони, ввод/вводи/введи <текст>» печатает туда, где сейчас курсор
    ПОЛЬЗОВАТЕЛЯ — Джони ничего не ищет и не переключает (см. keyboard.py:
    SendInput несёт ввод в окно с системным клавиатурным фокусом)."""
    typed = []
    monkeypatch.setattr(keyboard, "type_text", lambda text: typed.append(text))
    result = execute(RoutedAction("type_text", "привет как дела"), apps={})
    assert typed == ["привет как дела"]
    assert result.ok is True


def test_execute_press_enter(monkeypatch):
    """Отдельная от «ввод» команда намеренно (см. commands.yaml) — распознавание
    иногда обрезает хвост фразы, объединять «ввод+Enter» пока не стали."""
    pressed = []
    monkeypatch.setattr(keyboard, "press_enter", lambda: pressed.append(True))
    result = execute(RoutedAction("press_enter", "-"), apps={})
    assert pressed == [True]
    assert result.ok is True


def test_duck_pulse_dips_immediately_and_restores_after_delay(monkeypatch):
    """Сигнал «услышал имя»: приглушает СРАЗУ, возврат — таймером позже,
    не блокируя вызывающего (см. controller.run_one_cycle)."""
    monkeypatch.setattr(actions_system, "_get_scalar", lambda: 0.6)
    scalars = []
    monkeypatch.setattr(actions_system, "_set_scalar", lambda v: scalars.append(round(v, 2)))
    timers = []

    class FakeTimer:
        def __init__(self, interval, function):
            self.interval = interval
            self.function = function

        def start(self):
            timers.append(self)

    monkeypatch.setattr(actions_system.threading, "Timer", FakeTimer)

    actions_system.duck_pulse(amount=0.3, duration=1.5)

    assert scalars == [0.3]  # мгновенное приглушение
    assert len(timers) == 1
    assert timers[0].interval == 1.5

    timers[0].function()  # таймер сработал
    assert scalars == [0.3, 0.6]  # возврат к исходному


def test_duck_pulse_ignores_overlapping_call_before_restore(monkeypatch):
    """Живой баг (2026-08-05): второй вызов ДО того, как первый восстановил
    громкость, читал уже приглушённый уровень как «исходный» и приглушал его
    ЕЩЁ раз — громкость ратчетом уходила вниз и не восстанавливалась
    полностью (два процесса Джони, услышавшие одно и то же имя, вызывали
    duck_pulse почти одновременно). Пока приглушение ещё не восстановлено,
    повторный вызов должен быть no-op: ни новой команды pycaw, ни второго
    таймера."""
    scalar = {"value": 0.6}
    monkeypatch.setattr(actions_system, "_get_scalar", lambda: scalar["value"])

    def fake_set(v):
        scalar["value"] = round(v, 2)

    monkeypatch.setattr(actions_system, "_set_scalar", fake_set)
    timers = []

    class FakeTimer:
        def __init__(self, interval, function):
            self.function = function

        def start(self):
            timers.append(self)

    monkeypatch.setattr(actions_system.threading, "Timer", FakeTimer)

    actions_system.duck_pulse(amount=0.3, duration=1.5)
    assert scalar["value"] == 0.3
    assert len(timers) == 1

    actions_system.duck_pulse(amount=0.3, duration=1.5)  # перекрывающийся вызов
    assert scalar["value"] == 0.3  # не приглушил ещё раз
    assert len(timers) == 1  # и не завёл второй таймер

    timers[0].function()  # восстановление
    assert scalar["value"] == 0.6

    actions_system.duck_pulse(amount=0.3, duration=1.5)  # новая пара после восстановления работает как обычно
    assert scalar["value"] == 0.3
    assert len(timers) == 2


def test_duck_pulse_swallows_pycaw_errors(monkeypatch):
    """Нет устройства вывода / COM-сбой — не должно ронять цикл прослушивания."""

    def boom():
        raise RuntimeError("нет устройства")

    monkeypatch.setattr(actions_system, "_get_scalar", boom)
    actions_system.duck_pulse()  # не бросает


def test_media_keys_registered():
    for key in ("play_pause", "next_track", "prev_track"):
        assert key in actions_system.SYSTEM_HANDLERS


def test_system_commands_registered():
    for key in (
        "screenshot",
        "minimize_all",
        "show_desktop",
        "sleep",
        "shutdown",
        "restart",
        "cancel_shutdown",
    ):
        assert key in actions_system.SYSTEM_HANDLERS


def test_execute_system_screenshot_calls_handler(monkeypatch):
    called = {}
    monkeypatch.setitem(actions_system.SYSTEM_HANDLERS, "screenshot", lambda: called.setdefault("hit", True))
    result = execute(RoutedAction("system", "screenshot"), apps={})
    assert called.get("hit") is True
    assert result.ok is True


def test_execute_system_play_pause_sends_media_key(monkeypatch):
    pressed = []
    monkeypatch.setattr(actions_system, "_media_key", lambda vk: pressed.append(vk))
    result = execute(RoutedAction("system", "play_pause"), apps={})
    assert pressed == [0xB3]
    assert result.ok is True


def test_execute_system_next_track_sends_media_key(monkeypatch):
    pressed = []
    monkeypatch.setattr(actions_system, "_media_key", lambda vk: pressed.append(vk))
    execute(RoutedAction("system", "next_track"), apps={})
    assert pressed == [0xB0]


def test_start_parses_quoted_path_with_spaces_and_args(monkeypatch):
    captured = {}

    def fake_popen(args, cwd=None):
        captured["args"] = args
        captured["cwd"] = cwd

    monkeypatch.setattr(actions_windows.subprocess, "Popen", fake_popen)
    actions_windows._start('"C:/Program Files/App/app.exe" --flag x')
    assert captured["args"] == ["C:/Program Files/App/app.exe", "--flag", "x"]
    assert captured["cwd"] == "C:/Program Files/App"


def test_execute_steam_login(monkeypatch):
    calls = []

    def fake_popen(args, cwd=None):
        calls.append(args)

    monkeypatch.setattr(actions_steam.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(actions_steam.time, "sleep", lambda s: None)
    apps = {"стим": '"C:/Program Files (x86)/Steam/steam.exe"'}
    result = execute(RoutedAction("steam_login", "art_vol_teror"), apps)
    exe = "C:/Program Files (x86)/Steam/steam.exe"
    # сначала закрыть Steam, потом войти под нужным аккаунтом
    assert calls == [[exe, "-shutdown"], [exe, "-login", "art_vol_teror"]]
    assert result.ok is True


def test_execute_steam_login_no_steam_configured():
    result = execute(RoutedAction("steam_login", "art_vol_teror"), apps={})
    assert result.ok is False


def test_execute_move_window(monkeypatch):
    import johnny.windows as windows

    monkeypatch.setattr(windows, "move_foreground_to_monitor", lambda idx: True)
    result = execute(RoutedAction("move_window", "2"), apps={})
    assert result.ok is True


def test_execute_launch_on_monitor(monkeypatch):
    import johnny.windows as w

    monkeypatch.setattr(actions_windows, "_start", lambda t: None)
    monkeypatch.setattr(w, "visible_windows", lambda: set())
    monkeypatch.setattr(w, "wait_for_new_window", lambda before, **k: 555)
    monkeypatch.setattr(w, "list_monitors", lambda: [(0, 0, 100, 100), (100, 0, 200, 100)])
    monkeypatch.setattr(w, "move_window_to_monitor", lambda hwnd, mons, idx: None)
    result = execute(RoutedAction("launch_on_monitor", "браузер|2"), apps={"браузер": "x"})
    assert result.ok is True


def test_launch_on_monitor_moves_new_window(monkeypatch):
    import johnny.windows as w

    seq = []
    monkeypatch.setattr(actions_windows, "_start", lambda target: seq.append(("launch", target)))
    monkeypatch.setattr(w, "visible_windows", lambda: set())
    monkeypatch.setattr(w, "wait_for_new_window", lambda before, **k: 555)
    monkeypatch.setattr(w, "list_monitors", lambda: [(0, 0, 100, 100), (100, 0, 200, 100)])
    monkeypatch.setattr(w, "move_window_to_monitor", lambda hwnd, mons, idx: seq.append(("move", hwnd, idx)) or True)
    result = actions_windows.action_launch_on_monitor("браузер|2", {"apps": {"браузер": "x"}})
    assert result.ok is True
    assert ("move", 555, 2) in seq


def test_launch_on_monitor_no_new_window_moves_nothing(monkeypatch):
    import johnny.windows as w

    monkeypatch.setattr(actions_windows, "_start", lambda target: None)
    monkeypatch.setattr(w, "visible_windows", lambda: {1, 2})
    monkeypatch.setattr(w, "wait_for_new_window", lambda before, **k: None)
    moved = []
    monkeypatch.setattr(w, "move_window_to_monitor", lambda *a: moved.append(a))
    result = actions_windows.action_launch_on_monitor("браузер|2", {"apps": {"браузер": "x"}})
    assert result.ok is True
    assert moved == []  # нового окна нет — ничего не двигаем


def test_launch_on_monitor_unknown_app_returns_failure(monkeypatch):
    result = actions_windows.action_launch_on_monitor("нечто|2", {"apps": {}})
    assert result.ok is False


def test_open_url_on_monitor_opens_new_window_and_moves_it(monkeypatch):
    import johnny.windows as w

    seq = []
    monkeypatch.setattr(actions_windows.subprocess, "Popen", lambda args, cwd=None: seq.append(("popen", args)))
    monkeypatch.setattr(w, "visible_windows", lambda: set())
    monkeypatch.setattr(w, "wait_for_new_window", lambda before, **k: 777)
    monkeypatch.setattr(w, "list_monitors", lambda: [(0, 0, 2560, 1440), (2560, 209, 4480, 1289)])
    monkeypatch.setattr(w, "move_window_to_monitor", lambda hwnd, mons, idx: seq.append(("move", hwnd, idx)))
    apps = {"хром": '"C:/chrome.exe"'}
    result = actions_windows.action_open_url_on_monitor("youtube.com/results?search_query=хомяки|2", {"apps": apps})
    assert result.ok is True
    popen_args = seq[0][1]
    assert popen_args[0] == "C:/chrome.exe"
    assert "--new-window" in popen_args
    assert popen_args[-1] == "https://youtube.com/results?search_query=хомяки"
    assert ("move", 777, 2) in seq


def test_open_url_on_monitor_no_chrome_falls_back(monkeypatch):
    opened = {}
    monkeypatch.setattr(actions_browser, "webbrowser", type("M", (), {"open": staticmethod(lambda url: opened.setdefault("url", url))}))
    result = actions_windows.action_open_url_on_monitor("youtube.com|2", {"apps": {}})
    assert result.ok is True
    # open_url добавляет https:// если нет схемы
    assert "youtube.com" in opened.get("url", "")


def test_execute_browser_open(monkeypatch):
    import johnny.browser as b

    opened = {}
    monkeypatch.setattr(b, "open", lambda url, new_tab=True: opened.setdefault("url", url))
    result = execute(RoutedAction("browser_open", "youtube.com"), apps={})
    assert opened["url"] == "youtube.com"
    assert result.ok is True


def test_browser_open_falls_back_to_default_on_error(monkeypatch):
    import johnny.browser as b

    def boom(url, new_tab=True):
        raise RuntimeError("no chrome")

    monkeypatch.setattr(b, "open", boom)
    fell_back = {}
    monkeypatch.setattr(actions_browser, "open_url", lambda url: fell_back.setdefault("url", url))
    actions_browser.action_browser_open("youtube.com", {})
    assert fell_back["url"] == "youtube.com"


def test_execute_browser_seek_absolute(monkeypatch):
    import johnny.browser as b

    sought = {}
    monkeypatch.setattr(b, "seek", lambda s: sought.setdefault("s", s))
    result = execute(RoutedAction("browser_seek", "13 42"), apps={})
    assert sought["s"] == 822
    assert result.ok is True


def test_browser_seek_forward_is_relative(monkeypatch):
    import johnny.browser as b

    rel = {}
    monkeypatch.setattr(b, "seek_relative", lambda s: rel.setdefault("s", s))
    result = actions_browser.action_browser_seek("5 минут вперёд", {})
    assert result.ok is True
    assert rel["s"] == 300


def test_browser_seek_backward_is_negative(monkeypatch):
    import johnny.browser as b

    rel = {}
    monkeypatch.setattr(b, "seek_relative", lambda s: rel.setdefault("s", s))
    actions_browser.action_browser_seek("30 секунд назад", {})
    assert rel["s"] == -30


def test_execute_browser_seek_bad_time_fails(monkeypatch):
    result = execute(RoutedAction("browser_seek", "абвгд"), apps={})
    assert result.ok is False


def test_execute_browser_click_result(monkeypatch):
    import johnny.browser as b

    clicked = {}
    monkeypatch.setattr(b, "click_result", lambda n: clicked.setdefault("n", n))
    result = execute(RoutedAction("browser_click_result", "2"), apps={})
    assert clicked["n"] == 2
    assert result.ok is True


def test_execute_browser_focus(monkeypatch):
    import johnny.browser as b

    called = {}
    monkeypatch.setattr(b, "bring_to_front", lambda: called.setdefault("hit", True))
    result = execute(RoutedAction("browser_focus", "-"), apps={})
    assert called.get("hit") is True
    assert result.ok is True


def test_browser_seek_hours_forward(monkeypatch):
    import johnny.browser as b

    rel = {}
    monkeypatch.setattr(b, "seek_relative", lambda s: rel.setdefault("s", s))
    actions_browser.action_browser_seek("1 час вперёд", {})
    assert rel["s"] == 3600


_CHANNELS = {
    "9impulse": {"twitch": "9impulse", "aliases": ["импульс", "кирчик", "9 импульс"]},
    "serega_pirat": {"twitch": "serega_pirat", "aliases": ["серёга", "серёга пират", "пират"]},
}


def test_resolve_channel_by_alias():
    assert actions_browser._resolve_channel("кирчик", "twitch", _CHANNELS) == "9impulse"


def test_resolve_channel_ignores_case_ending():
    # родительный падеж «импульса»/«серёги пирата» должен находиться
    assert actions_browser._resolve_channel("импульса", "twitch", _CHANNELS) == "9impulse"
    assert actions_browser._resolve_channel("серёги пирата", "twitch", _CHANNELS) == "serega_pirat"


def test_resolve_channel_unknown_returns_none():
    assert actions_browser._resolve_channel("незнакомец", "twitch", _CHANNELS) is None


def test_open_channel_uses_slug(monkeypatch):
    opened = {}
    monkeypatch.setattr(
        actions_browser, "action_browser_open", lambda url, ctx: opened.setdefault("url", url)
    )
    actions_browser.action_open_channel("twitch|серёги пирата", {"channels": _CHANNELS})
    assert opened["url"] == "twitch.tv/serega_pirat"


def test_open_channel_falls_back_to_literal(monkeypatch):
    opened = {}
    monkeypatch.setattr(
        actions_browser, "action_browser_open", lambda url, ctx: opened.setdefault("url", url)
    )
    actions_browser.action_open_channel("twitch|neznakomec", {"channels": _CHANNELS})
    assert opened["url"] == "twitch.tv/neznakomec"


def test_execute_open_channel(monkeypatch):
    import johnny.browser as b

    opened = {}
    monkeypatch.setattr(b, "open", lambda url, new_tab=True: opened.setdefault("url", url))
    result = execute(RoutedAction("open_channel", "twitch|импульс"), apps={}, channels=_CHANNELS)
    assert "9impulse" in opened.get("url", "")
    assert result.ok is True


def test_browser_click_result_failure_is_not_ok(monkeypatch):
    import johnny.browser as browser

    monkeypatch.setattr(browser, "click_result", lambda n: False)
    result = execute(RoutedAction("browser_click_result", "1"), {}, {})
    assert result.ok is False
    assert "видео" in result.message.lower()


def test_browser_click_result_success_is_ok(monkeypatch):
    import johnny.browser as browser

    monkeypatch.setattr(browser, "click_result", lambda n: True)
    assert execute(RoutedAction("browser_click_result", "1"), {}, {}).ok is True


def test_browser_fullscreen_failure_is_not_ok(monkeypatch):
    import johnny.browser as browser

    monkeypatch.setattr(browser, "fullscreen", lambda: False)
    assert execute(RoutedAction("browser_fullscreen", ""), {}, {}).ok is False


def test_execute_remember_saves_fact(monkeypatch):
    import johnny.memory as memory

    saved = []
    monkeypatch.setattr(memory, "remember", lambda text: saved.append(text))
    result = execute(RoutedAction("remember", "любит кофе без сахара"), apps={})
    assert saved == ["любит кофе без сахара"]
    assert result.ok is True


def test_execute_forget_reports_success_when_found(monkeypatch):
    import johnny.memory as memory

    monkeypatch.setattr(memory, "forget", lambda query: "любит кофе без сахара")
    result = execute(RoutedAction("forget", "кофе"), apps={})
    assert result.ok is True
    assert result.message == "Забыл: любит кофе без сахара", (
        "спокойное «Забыл» без текста факта не даёт заметить, что forget() "
        "мог удалить не тот факт (короткие/стоп-словные запросы)"
    )


def test_execute_forget_reports_failure_when_not_found(monkeypatch):
    import johnny.memory as memory

    monkeypatch.setattr(memory, "forget", lambda query: None)
    result = execute(RoutedAction("forget", "что угодно"), apps={})
    assert result.ok is False
    assert result.message == "Не нашёл такое в памяти"


def test_execute_list_memory_speaks_facts(monkeypatch):
    import johnny.memory as memory

    monkeypatch.setattr(memory, "list_facts", lambda: ["любит кофе", "играет за art_vol_teror"])
    result = execute(RoutedAction("list_memory", "-"), apps={})
    assert result.ok is True
    assert "любит кофе" in result.message
    assert "играет за art_vol_teror" in result.message


def test_execute_list_memory_when_nothing_remembered(monkeypatch):
    import johnny.memory as memory

    monkeypatch.setattr(memory, "list_facts", lambda: [])
    result = execute(RoutedAction("list_memory", "-"), apps={})
    assert result.ok is True
    assert result.message == "Я пока ничего не помню"


def test_execute_update_fact_reports_previous_text(monkeypatch):
    import johnny.memory as memory

    calls = []

    def fake_update(query, text):
        calls.append((query, text))
        return "стим-аккаунт old_nick"

    monkeypatch.setattr(memory, "update_fact", fake_update)
    result = execute(RoutedAction("update_fact", "стим-аккаунт на art_vol_teror"), apps={})
    assert calls == [("стим-аккаунт", "art_vol_teror")]
    assert result.ok is True
    assert result.message == "Поправил. Было: стим-аккаунт old_nick", (
        "прежний текст в ответе — единственный способ услышать, что "
        "update_fact зацепил не тот факт (поиск по пересечению слов)"
    )


def test_execute_update_fact_reports_failure_when_not_found(monkeypatch):
    import johnny.memory as memory

    monkeypatch.setattr(memory, "update_fact", lambda query, text: None)
    result = execute(RoutedAction("update_fact", "чего нет на новое"), apps={})
    assert result.ok is False
    assert result.message == "Не нашёл такое в памяти"


def test_execute_update_fact_without_separator_asks_for_form(monkeypatch):
    """Без « на » непонятно, что править и на что. Ничего не трогаем и
    подсказываем форму, а не выдумываем разбиение."""
    import johnny.memory as memory

    def fail(*args):
        raise AssertionError("update_fact не должен вызываться на нераспознанной фразе")

    monkeypatch.setattr(memory, "update_fact", fail)
    result = execute(RoutedAction("update_fact", "стим-аккаунт"), apps={})
    assert result.ok is False
    assert "поправь X на Y" in result.message


def test_execute_update_fact_splits_by_last_separator(monkeypatch):
    """« на » встречается и внутри самого факта — новое значение стоит в
    конце, поэтому режем по ПОСЛЕДНЕМУ разделителю."""
    import johnny.memory as memory

    calls = []
    monkeypatch.setattr(
        memory, "update_fact", lambda query, text: calls.append((query, text)) or "было"
    )
    execute(RoutedAction("update_fact", "ник на твиче на flynes_"), apps={})
    assert calls == [("ник на твиче", "flynes_")]
