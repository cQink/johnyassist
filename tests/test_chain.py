from dataclasses import dataclass, field

import johnny.actions as actions
import johnny.chain as chain
from johnny.actions import ActionResult
from johnny.config import CommandRule
from johnny.router import RoutedAction

COMMANDS = [
    CommandRule("запусти *", "launch_app", "{0}"),
    CommandRule("громкость *", "set_volume", "{0}"),
    CommandRule("открой твич", "browser_open", "twitch.tv"),
    CommandRule("найди на ютубе *", "browser_open", "yt?q={0}"),
    CommandRule("включи музыку", "system", "play_pause"),
    CommandRule("включи первое видео", "browser_click_result", "1"),
    CommandRule("на полный экран", "browser_fullscreen", "-"),
    CommandRule("сверни все", "system", "minimize_all"),
    CommandRule("выключи компьютер", "system", "shutdown"),
    CommandRule("режим стрима", "scenario", "режим стрима"),
    CommandRule("напиши *", "discord_message", "{0}"),
    CommandRule("позвони *", "discord_call", "{0}"),
]


def test_сообщение_не_собирается_в_цепочку():
    """Текст сообщения — свободная речь, и союзы в ней неизбежны.

    Живой случай: «напиши гоше я приду потом включи музыку» разрезалось на
    discord_message «гоше я приду» + паузу — Гоше уходило обрезанное
    сообщение, да ещё и музыка вставала. Цепочка обязана не собраться, чтобы
    фраза ушла в обычный разбор целиком.
    """
    assert chain.split_local("напиши гоше я приду потом включи музыку", COMMANDS) is None


def test_сообщение_не_собирается_в_цепочку_по_границе_команды():
    """И без союза тоже: «включи музыку» в хвосте — часть текста, а не команда."""
    assert chain.split_local("напиши гоше я приду включи музыку", COMMANDS) is None


def test_звонок_не_собирается_в_цепочку():
    """Звонок необратим, поэтому в цепочку не идёт (см. is_unsafe_action)."""
    assert chain.split_local("позвони гоше потом включи музыку", COMMANDS) is None


def test_split_two_commands():
    steps = chain.split_local("запусти обс и громкость 20", COMMANDS)
    assert [(s.action, s.argument) for s in steps] == [
        ("launch_app", "обс"),
        ("set_volume", "20"),
    ]


def test_split_rejected_when_part_is_not_a_command():
    """«рок и ролл» — это аргумент, а не цепочка: «ролл» ни с чем не совпадает."""
    assert chain.split_local("найди на ютубе рок и ролл", COMMANDS) is None


def test_single_part_is_not_a_chain():
    assert chain.split_local("открой твич", COMMANDS) is None


def test_split_rejects_more_than_max_steps():
    text = " и ".join(["открой твич"] * (chain.MAX_STEPS + 1))
    assert chain.split_local(text, COMMANDS) is None


def test_split_rejects_unsafe_step():
    assert chain.split_local("сверни всё и выключи компьютер", COMMANDS) is None


def test_split_separators():
    for separator in ("потом", "затем", "а потом", "а также", "плюс"):
        text = f"открой твич {separator} включи музыку"
        assert chain.split_local(text, COMMANDS) is not None, separator


def test_and_inside_word_does_not_split():
    """«и» режет только как отдельное слово: «игра», «или», «история» целы."""
    commands = COMMANDS + [CommandRule("открой историю", "open_url", "history")]
    assert chain.split_local("открой историю", commands) is None


def test_resolve_rejects_nested_scenario():
    assert chain.resolve(["режим стрима", "открой твич"], COMMANDS) is None


def test_resolve_single_phrase_is_allowed():
    """Сценарий из одного шага законен; ограничение «≥2» только у сплиттера."""
    assert len(chain.resolve(["открой твич"], COMMANDS)) == 1


def test_resolve_empty_is_none():
    assert chain.resolve([], COMMANDS) is None


@dataclass
class FakeSpeaker:
    said: list = field(default_factory=list)
    answers: int = 0

    def say(self, text):
        self.said.append(text)

    def play_answer(self):
        self.answers += 1
        return True


@dataclass
class FakeConfig:
    apps: dict = field(default_factory=dict)
    channels: dict = field(default_factory=dict)
    people: dict = field(default_factory=dict)


def _steps(n):
    return [RoutedAction("system", f"step{i}") for i in range(n)]


def test_run_all_steps_one_sound(monkeypatch):
    done = []
    monkeypatch.setattr(
        actions,
        "execute",
        lambda routed, apps, channels, new_tab=True, people=None, config=None: done.append(routed)
        or ActionResult(True, "Готово"),
    )
    speaker = FakeSpeaker()
    result = chain.run(_steps(3), FakeConfig(), speaker)
    assert len(done) == 3
    assert result.done == 3 and result.failure is None
    assert speaker.answers == 1, "звук успеха должен прозвучать ровно один раз"
    assert speaker.said == []


def test_run_stops_on_first_failure(monkeypatch):
    done = []

    def fake_execute(routed, apps, channels, new_tab=True, people=None, config=None):
        done.append(routed)
        if len(done) == 2:
            return ActionResult(False, "Не нашёл видео на странице")
        return ActionResult(True, "Готово")

    monkeypatch.setattr(actions, "execute", fake_execute)
    speaker = FakeSpeaker()
    result = chain.run(_steps(3), FakeConfig(), speaker)
    assert len(done) == 2, "третий шаг выполняться не должен"
    assert result.done == 1 and result.total == 3
    assert result.failure == "Не нашёл видео на странице"
    assert speaker.answers == 0
    assert "1 из 3" in speaker.said[0]
    assert "Не нашёл видео" in speaker.said[0]


def test_run_stops_between_steps_when_cancelled(monkeypatch):
    """«Джони, стоп» посреди цепочки: уже начатый шаг доигрывает (это делает
    controller.py через sounds.stop_all — сюда не относится), но следующий
    шаг не запускается, и финальное «Готово» не звучит — человек уже не
    ждёт отчёта."""
    import threading

    done = []
    monkeypatch.setattr(
        actions,
        "execute",
        lambda routed, apps, channels, new_tab=True, people=None, config=None: done.append(routed)
        or ActionResult(True, "Готово"),
    )
    speaker = FakeSpeaker()
    cancel = threading.Event()
    cancel.set()
    result = chain.run(_steps(3), FakeConfig(), speaker, cancel=cancel)
    assert done == [], "ни один шаг не должен запуститься, если отмена уже стоит"
    assert result.done == 0 and result.failure == "прервано"
    assert speaker.said == [] and speaker.answers == 0


def test_run_finishes_silently_when_cancelled_after_last_step(monkeypatch):
    """Отмена пришла уже ПОСЛЕ последнего шага — финальное «Готово» всё равно
    не озвучивается: «стоп» уже сказан, отчитываться не о чем."""
    import threading

    monkeypatch.setattr(
        actions,
        "execute",
        lambda routed, apps, channels, new_tab=True, people=None, config=None: ActionResult(True, "Готово"),
    )
    speaker = FakeSpeaker()
    cancel = threading.Event()
    steps = _steps(2)

    calls = {"n": 0}
    real_execute = actions.execute

    def counting_execute(*a, **kw):
        calls["n"] += 1
        if calls["n"] == len(steps):
            cancel.set()  # отмена приходит сразу после последнего шага
        return real_execute(*a, **kw)

    monkeypatch.setattr(actions, "execute", counting_execute)
    result = chain.run(steps, FakeConfig(), speaker, cancel=cancel)
    assert result.done == 2 and result.failure == "прервано"
    assert speaker.said == [] and speaker.answers == 0


def test_run_without_cancel_behaves_as_before(monkeypatch):
    """cancel=None (значение по умолчанию) не должен ничего менять."""
    monkeypatch.setattr(
        actions,
        "execute",
        lambda routed, apps, channels, new_tab=True, people=None, config=None: ActionResult(True, "Готово"),
    )
    speaker = FakeSpeaker()
    result = chain.run(_steps(2), FakeConfig(), speaker, cancel=None)
    assert result.done == 2 and result.failure is None
    assert speaker.answers == 1


def test_command_boundary_split_inside_a_part():
    """Живой случай: союз в фразе один, а команд три.

    «включи первое видео на полный экран» — цельного такого правила нет, но
    это начало-команда плюс остаток-команда. Без разреза по границе весь
    хвост уезжал аргументом в поиск YouTube.
    """
    steps = chain.split_local(
        "найди на ютубе exile show и включи первое видео на полный экран", COMMANDS
    )
    assert [(s.action, s.argument) for s in steps] == [
        ("browser_open", "yt?q=exile show"),
        ("browser_click_result", "1"),
        ("browser_fullscreen", "-"),
    ]


def test_greedy_app_focus_catchall_does_not_steal_the_cut():
    """Живой баг 2026-08-04: «открой первое видео на полный экран».

    «открой *» (app_focus) — генерический ловящий-всё шаблон и совпадает
    почти с любым началом фразы («открой первое видео на» тоже «команда»).
    Без литерального прохода первым разрез уезжал на app_focus("первое видео
    на") + browser_fullscreen("полный экран") вместо настоящей пары
    browser_click_result + browser_fullscreen.
    """
    commands = COMMANDS + [
        CommandRule("открой первое видео", "browser_click_result", "1"),
        CommandRule("открой *", "app_focus", "{0}"),
    ]
    steps = chain.split_local("открой первое видео на полный экран", commands)
    assert [(s.action, s.argument) for s in steps] == [
        ("browser_click_result", "1"),
        ("browser_fullscreen", "-"),
    ]


def test_chain_without_any_separator():
    """Две команды подряд без союза — тоже цепочка."""
    steps = chain.split_local("открой твич включи музыку", COMMANDS)
    assert [s.action for s in steps] == ["browser_open", "system"]


def test_boundary_split_needs_both_halves():
    """Начало — команда, остаток — мусор: разбиения нет."""
    assert chain.split_local("открой твич бла бла бла", COMMANDS) is None


def test_split_rejects_empty_resolved_part(monkeypatch):
    """Если разрез по границе команды не даёт валидных шагов, цепочка отвергается."""

    def fake_resolve(phrase, commands):
        if phrase == "включи музыку":
            return []
        return None

    monkeypatch.setattr(chain, "_resolve_phrase", fake_resolve)
    assert chain.split_local("открой твич и включи музыку", COMMANDS) is None


def test_whole_phrase_match_wins_over_split():
    """Фраза, совпавшая целиком, не режется: жадный шаблон забирает аргумент."""
    assert chain.split_local("найди на ютубе включи музыку", COMMANDS) is None


def test_max_steps_counts_total_not_parts():
    """Потолок считает ШАГИ: два куска могут развернуться в шесть действий."""
    long_part = "включи первое видео на полный экран"
    text = " и ".join([long_part] * 3)  # 3 куска -> 6 шагов
    assert chain.split_local(text, COMMANDS) is None


def test_understands_exact_command():
    assert chain.understands("открой твич", COMMANDS) is True


def test_understands_chain():
    assert chain.understands("открой твич и включи музыку", COMMANDS) is True


def test_does_not_understand_garbage():
    assert chain.understands("бла бла бла", COMMANDS) is False


def test_understands_ignores_fuzzy():
    """Нечёткое совпадение не в счёт.

    На этой проверке держится восстановление ослышанного имени: если пустить
    сюда догадки, Джони начнёт «узнавать» команду в любой фразе и молча
    выбрасывать её первое слово.
    """
    assert chain.understands("открой твоч", COMMANDS) is False


def test_справочник_людей_доезжает_до_шага_цепочки(monkeypatch):
    """people обязан доехать из конфига в execute каждого шага.

    people здесь — обязательный ключевой параметр специально: у дублёров
    execute он объявлен со значением по умолчанию, поэтому потеря
    people=config.people в chain.run не уронила бы ни одного теста, а Discord
    в цепочке перестал бы узнавать людей.
    """
    people = {"гоша": {"discord": "Гречка", "aliases": ["гоша"]}}
    seen = {}

    def fake_execute(routed, apps, channels, new_tab=True, *, people, config=None):
        seen["people"] = people
        return ActionResult(True, "Готово")

    monkeypatch.setattr(actions, "execute", fake_execute)
    chain.run(_steps(1), FakeConfig(people=people), FakeSpeaker())

    assert seen["people"] == people
