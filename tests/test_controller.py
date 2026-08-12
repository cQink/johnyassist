import threading

import pytest

import johnny.actions as actions
from johnny.audio import BLOCK_FRAMES, BlockBuffer
from johnny.config import CommandRule, Config, Settings
from johnny.controller import AssistantController

_LOUD = 8000


@pytest.fixture(autouse=True)
def _no_real_duck_pulse(monkeypatch):
    """run_one_cycle зовёт actions.duck_pulse() на каждое подтверждение
    имени — без заглушки тесты лезли бы в реальный pycaw/COM (недоступен в
    тестовом окружении) и плодили настоящие фоновые threading.Timer."""
    monkeypatch.setattr(actions, "duck_pulse", lambda *a, **k: None)


class FakeMic(BlockBuffer):
    """Микрофон по сценарию: список блоков (0 — тишина, _LOUD — речь), а
    после его конца — вечная тишина.

    Бесконечный хвост принципиален: контроллер вызывает flush() после
    звука-подтверждения, и микрофон на конечной очереди после этого «умирал»
    бы с MicrophoneError. В жизни звук после флаша продолжает идти, фейк
    обязан вести себя так же. По той же причине flush() не трогает сценарий —
    человек говорит команду уже ПОСЛЕ того, как буфер сброшен.
    """

    def __init__(self, script=()):
        super().__init__()
        self._script = list(script)

    def read_block(self, timeout=None) -> bytes:
        value = self._script.pop(0) if self._script else 0
        raw = value.to_bytes(2, "little", signed=True) * BLOCK_FRAMES
        self._ring.append(raw)
        return raw


def silent_mic():
    """Позвал и замолчал, потом сказал команду → ветка с паузой.

    Три тихих блока — ровно окно _CONTINUE_WINDOW (0.75с), после них
    контроллер решает, что была пауза.
    """
    return FakeMic([0] * 3 + [_LOUD] * 3 + [0] * 8)


def talking_mic():
    """Речь пошла сразу после имени → слитная ветка."""
    return FakeMic([_LOUD] * 3 + [0] * 8)


class FakeListener:
    wake_words = ["джони"]

    def __init__(self, recognize_texts=()):
        self._recognize_texts = list(recognize_texts)
        self.reset_calls = 0

    def wait_for_wake_word(self, mic):
        return None

    def recognize(self, data):
        """Заглушка Vosk.recognize: следующий текст из сценария (см.
        _check_for_stop_while_busy — берёт по одному тексту на блок)."""
        return self._recognize_texts.pop(0) if self._recognize_texts else ""

    def reset(self):
        self.reset_calls += 1


class FakeRecognizer:
    def __init__(self, *texts):
        self.texts = list(texts)
        self.calls = 0
        self.last_confidence = 0.0

    def transcribe(self, audio):
        self.calls += 1
        return self.texts.pop(0) if self.texts else ""


class SpySpeaker:
    mode = "off"

    def __init__(self):
        self.acks = 0
        self.said = []

    def acknowledge(self):
        self.acks += 1

    def say(self, text):
        self.said.append(text)


def _config():
    return Config(
        apps={"дота": "steam://rungameid/570"},
        commands=[CommandRule("запусти *", "launch_app", "{0}")],
        settings=Settings("джони", "models/vosk", "off", "medium", "cuda"),
    )


def _join_worker(ctrl, timeout=2.0) -> None:
    """Дождаться фонового потока команды (см. AssistantController.busy).

    Выполнение команды теперь уходит в отдельный поток (ради «Джони, стоп»,
    который должен уметь перебить его на лету) — тестам, проверяющим итог
    (историю, вызовы handle_command), нужно явно дождаться его завершения,
    иначе они читают состояние ДО того, как поток его записал. Без join
    поток мог бы вдобавок дожить до следующего теста и дёрнуть уже
    отменённый monkeypatch.
    """
    if ctrl._worker is not None:
        ctrl._worker.join(timeout=timeout)


def test_active_cycle_handles_command(monkeypatch):
    import johnny.controller as c

    handled = {}
    monkeypatch.setattr(c.history, "add", lambda text, via=None: None)
    monkeypatch.setattr(
        c, "handle_command",
        lambda text, cfg, sp, **kw: (handled.setdefault("text", text), c.Outcome("точно"))[1],
    )
    ctrl = AssistantController(_config(), SpySpeaker())
    # "джони" — транскрипция preroll+tail (позвал и замолчал), вторая строка —
    # ответ на отдельный вызов transcribe уже внутри _summoned_turn.
    ctrl.run_one_cycle(FakeListener(), FakeRecognizer("джони", "запусти дота"), silent_mic())
    _join_worker(ctrl)
    assert handled["text"] == "запусти дота"
    assert ctrl.last_command == "запусти дота"


def test_history_written_even_if_handle_command_raises(monkeypatch):
    """Команда выполняется в фоновом потоке — исключение в ней больше не
    роняет run_one_cycle (поток слушания это не должно затрагивать вовсе),
    но строка истории с меткой «ошибка» обязана появиться всё равно."""
    import johnny.controller as c

    calls = {}

    def fake_add(text, via=None):
        calls["text"] = text
        calls["via"] = via

    def boom(text, cfg, sp, **kw):
        raise RuntimeError("бум")

    monkeypatch.setattr(c.history, "add", fake_add)
    monkeypatch.setattr(c, "handle_command", boom)
    ctrl = AssistantController(_config(), SpySpeaker())
    ctrl.run_one_cycle(FakeListener(), FakeRecognizer("джони", "запусти дота"), silent_mic())
    _join_worker(ctrl)
    assert calls.get("text") == "запусти дота"
    # Метка «ошибка» — весь смысл фикса: строка истории должна не только
    # существовать, но и честно показывать, что обработка упала.
    assert calls.get("via") == "ошибка[0.00]"


def test_paused_cycle_skips_handling(monkeypatch):
    import johnny.controller as c

    handled = {}
    monkeypatch.setattr(c, "handle_command", lambda *a, **kw: handled.setdefault("called", True))
    sp = SpySpeaker()
    ctrl = AssistantController(_config(), sp)
    ctrl.pause()
    ctrl.run_one_cycle(FakeListener(), FakeRecognizer("запусти дота"), silent_mic())
    assert "called" not in handled
    assert sp.acks == 0  # на паузе даже не подтверждаем


def test_pause_resume_stop_flags():
    ctrl = AssistantController(_config(), SpySpeaker())
    assert ctrl.paused is False and ctrl.stopped is False
    ctrl.pause()
    assert ctrl.paused is True
    ctrl.resume()
    assert ctrl.paused is False
    ctrl.stop()
    assert ctrl.stopped is True


# --- фазы для кружка в панели ---------------------------------------------
#
# Требование пользователя дословно: «кружок должен задействоваться когда
# срабатывает вейкворд, и пульсировать при разговорах». Проверяем здесь то,
# что кружку сообщает контроллер; сам кружок — в test_visualizer.py.


@pytest.fixture
def phases(monkeypatch):
    """Записывает все set_phase по порядку и держит доску в чистоте.

    activity — модульное состояние, общее на процесс: без сброса один тест
    оставил бы фазу следующему, и падало бы через раз в зависимости от порядка.
    """
    import johnny.activity as activity

    activity.reset()
    seen = []
    real = activity.set_phase

    def spy(phase):
        seen.append(phase)
        real(phase)

    monkeypatch.setattr(activity, "set_phase", spy)
    yield seen
    activity.reset()


def test_wake_word_lights_the_circle(monkeypatch, phases):
    """Ради этого фазы вообще появились: имя подтвердилось — кружок горит."""
    import johnny.activity as activity
    import johnny.controller as c

    monkeypatch.setattr(c, "handle_command", lambda *a, **kw: c.Outcome("точно"))
    ctrl = AssistantController(_config(), SpySpeaker())
    ctrl.run_one_cycle(FakeListener(), FakeRecognizer("джони", "запусти дота"), talking_mic())
    _join_worker(ctrl)
    assert activity.WAKE in phases
    # Порядок важен: «горит» должно наступить ПОСЛЕ ожидания имени, а не
    # с самого начала цикла — иначе кружок горел бы всегда и не значил ничего.
    assert phases.index(activity.IDLE) < phases.index(activity.WAKE)


def test_speech_pulses_the_circle(monkeypatch, phases):
    """Пульсация: уровень с микрофона доходит до доски через record_until_silence."""
    import johnny.activity as activity
    import johnny.controller as c

    monkeypatch.setattr(c, "handle_command", lambda *a, **kw: c.Outcome("точно"))
    ctrl = AssistantController(_config(), SpySpeaker())
    ctrl.run_one_cycle(FakeListener(), FakeRecognizer("джони", "запусти дота"), talking_mic())
    _join_worker(ctrl)
    # Блоки _LOUD прошли через record_until_silence — доска обязана их увидеть.
    # Уровень уже затухает, поэтому сравниваем с нулём, а не с константой.
    assert activity.snapshot().level > 0.0


def test_circle_goes_dark_after_the_command_is_done(monkeypatch, phases):
    """Фоновый поток живёт дольше цикла — гасить кружок обязан он сам."""
    import johnny.activity as activity
    import johnny.controller as c

    monkeypatch.setattr(c, "handle_command", lambda *a, **kw: c.Outcome("точно"))
    ctrl = AssistantController(_config(), SpySpeaker())
    ctrl.run_one_cycle(FakeListener(), FakeRecognizer("джони", "запусти дота"), talking_mic())
    _join_worker(ctrl)
    assert activity.snapshot().phase == activity.IDLE


def test_failed_command_does_not_leave_the_circle_lit(monkeypatch, phases):
    """У ветки с ошибкой в _spawn_worker свой return — без finally упавшая
    команда оставила бы кружок гореть до перезапуска Джони."""
    import johnny.activity as activity
    import johnny.controller as c

    def boom(text, cfg, sp, **kw):
        raise RuntimeError("бум")

    monkeypatch.setattr(c, "handle_command", boom)
    monkeypatch.setattr(c.history, "add", lambda *a, **kw: None)
    ctrl = AssistantController(_config(), SpySpeaker())
    ctrl.run_one_cycle(FakeListener(), FakeRecognizer("джони", "запусти дота"), talking_mic())
    _join_worker(ctrl)
    assert activity.snapshot().phase == activity.IDLE


def test_pause_darkens_the_circle_immediately(phases):
    """Не дожидаясь цикла: в момент паузы он обычно ЖДЁТ имя внутри
    wait_for_wake_word и до проверки флага дойдёт неизвестно когда."""
    import johnny.activity as activity

    ctrl = AssistantController(_config(), SpySpeaker())
    ctrl.pause()
    assert activity.snapshot().phase == activity.PAUSED


def test_resume_restores_the_circle_immediately(phases):
    import johnny.activity as activity

    ctrl = AssistantController(_config(), SpySpeaker())
    ctrl.pause()
    ctrl.resume()
    assert activity.snapshot().phase == activity.IDLE


def test_run_continues_when_run_one_cycle_raises(monkeypatch):
    # TTS-сбой (или любая другая ошибка) внутри run_one_cycle не должен
    # убивать поток прослушивания — цикл обязан продолжаться. Пустой текст
    # после фильтра эха теперь рутинный случай, поэтому такое может
    # случаться регулярно, а не только в редких авариях.
    import johnny.controller as c

    monkeypatch.setattr(c, "_RETRY_PAUSE_SECONDS", 0)
    ctrl = AssistantController(_config(), SpySpeaker())
    calls = {"n": 0}

    def boom(listener, recognizer, mic):
        calls["n"] += 1
        if calls["n"] >= 3:
            ctrl.stop()
        raise RuntimeError("бум")

    ctrl.run_one_cycle = boom
    ctrl.run(FakeListener(), FakeRecognizer("запусти дота"), silent_mic())
    assert calls["n"] == 3


def test_run_stops_itself_after_too_many_consecutive_failures(monkeypatch):
    # Если run_one_cycle падает КАЖДЫЙ раз (например, микрофон отключён и
    # конструктор потока падает на wait_for_wake_word), цикл не должен
    # крутиться вечно на полной скорости CPU. После _MAX_CONSECUTIVE_FAILURES
    # подряд сбоев run() обязан сам себя остановить (ctrl.stop()).
    import johnny.controller as c

    monkeypatch.setattr(c, "_RETRY_PAUSE_SECONDS", 0)
    monkeypatch.setattr(c, "_MAX_CONSECUTIVE_FAILURES", 3)
    ctrl = AssistantController(_config(), SpySpeaker())
    calls = {"n": 0}

    def boom(listener, recognizer, mic):
        calls["n"] += 1
        raise RuntimeError("бум")

    ctrl.run_one_cycle = boom
    ctrl.run(FakeListener(), FakeRecognizer("запусти дота"), silent_mic())
    assert calls["n"] == 3
    assert ctrl.stopped is True


def test_transient_failure_resets_consecutive_counter(monkeypatch):
    # Один сбой, за которым следует успех, не должен накапливаться со
    # следующей серией сбоев — счётчик обязан сброситься на успешном цикле.
    # Если бы сброса не было, третий по счёту вызов (fail) досчитал бы общий
    # счётчик сбоев до порога и остановил бы цикл раньше, чем дойдёт до
    # пятого вызова, которым тест останавливает цикл сам.
    import johnny.controller as c

    monkeypatch.setattr(c, "_RETRY_PAUSE_SECONDS", 0)
    monkeypatch.setattr(c, "_MAX_CONSECUTIVE_FAILURES", 3)
    ctrl = AssistantController(_config(), SpySpeaker())
    calls = {"n": 0}
    # fail, fail, ok (сброс!), fail, stop — при правильном сбросе счётчика
    # общее число сбоев (3) никогда не окажется подряд.
    plan = ["fail", "fail", "ok", "fail", "stop"]

    def cycle(listener, recognizer, mic):
        calls["n"] += 1
        step = plan[calls["n"] - 1]
        if step == "stop":
            ctrl.stop()
            return
        if step == "fail":
            raise RuntimeError("бум")
        # "ok" — успешный цикл, ничего не делает

    ctrl.run_one_cycle = cycle
    ctrl.run(FakeListener(), FakeRecognizer("запусти дота"), silent_mic())
    assert calls["n"] == 5
    assert ctrl.stopped is True


def test_joined_phrase_does_not_acknowledge(monkeypatch):
    # Главное поведение: сказал слитно — звука-подтверждения быть не должно.
    import johnny.controller as c

    monkeypatch.setattr(c.history, "add", lambda text, via=None: None)
    monkeypatch.setattr(c, "handle_command", lambda text, cfg, sp, **kw: c.Outcome("точно"))
    sp = SpySpeaker()
    ctrl = AssistantController(_config(), sp)
    ctrl.run_one_cycle(FakeListener(), FakeRecognizer("джони запусти дота"), talking_mic())
    _join_worker(ctrl)
    assert sp.acks == 0


def test_joined_phrase_strips_name_before_routing(monkeypatch):
    import johnny.controller as c

    seen = {}
    monkeypatch.setattr(c.history, "add", lambda text, via=None: None)
    monkeypatch.setattr(
        c, "handle_command",
        lambda text, cfg, sp, **kw: (seen.setdefault("text", text), c.Outcome("точно"))[1],
    )
    ctrl = AssistantController(_config(), SpySpeaker())
    ctrl.run_one_cycle(FakeListener(), FakeRecognizer("джони запусти дота"), talking_mic())
    _join_worker(ctrl)
    assert seen["text"] == "запусти дота"


def test_joined_command_fully_in_preroll_is_not_treated_as_pause(monkeypatch):
    """ЖИВОЙ БАГ (2026-08-03): после перехода на полную модель Vosk
    подтверждение имени стало занимать заметно больше времени, и короткая
    слитная команда («полный экран») успевала прозвучать и закончиться ДО
    того, как record_until_silence начинал слушать «новую» речь — таймер
    тишины видел только тишину (started=False) и ВСЕГДА уходил в «позвал и
    ждёт», хотя вся фраза целиком уже лежала в preroll. Джони переспрашивал
    «сэр?» на каждую слитную команду. Имитируем: микрофон не даёт ни одного
    громкого блока (started всегда False), но распознавание всё равно
    возвращает полную фразу — команда обязана выполниться СРАЗУ, без ack."""
    import johnny.controller as c

    monkeypatch.setattr(c.history, "add", lambda text, via=None: None)
    handled = {}
    monkeypatch.setattr(
        c, "handle_command",
        lambda text, cfg, sp, **kw: (handled.setdefault("text", text), c.Outcome("точно"))[1],
    )
    sp = SpySpeaker()
    ctrl = AssistantController(_config(), sp)
    # FakeMic() без сценария — вечная тишина, точная имитация started=False.
    ctrl.run_one_cycle(FakeListener(), FakeRecognizer("джони запусти дота"), FakeMic())
    _join_worker(ctrl)
    assert sp.acks == 0, "не должен переспрашивать — команда уже была в preroll"
    assert handled.get("text") == "запусти дота"


def test_pause_branch_stays_silent_before_command(monkeypatch):
    # Позвал и замолчал — до реальной команды Джони не должен ни
    # приглушать звук, ни играть собственный сигнал подтверждения.
    import johnny.controller as c

    monkeypatch.setattr(c.history, "add", lambda text, via=None: None)
    monkeypatch.setattr(c, "handle_command", lambda text, cfg, sp, **kw: c.Outcome("точно"))
    sp = SpySpeaker()
    ctrl = AssistantController(_config(), sp)
    ctrl.run_one_cycle(FakeListener(), FakeRecognizer("джони", "запусти дота"), silent_mic())
    _join_worker(ctrl)
    assert sp.acks == 0


def test_wake_word_does_not_duck_or_acknowledge_before_command(monkeypatch):
    import johnny.controller as c

    calls = []
    monkeypatch.setattr(c.actions, "duck_pulse", lambda *a, **kw: calls.append("duck"))
    monkeypatch.setattr(c.history, "add", lambda text, via=None: None)
    monkeypatch.setattr(c, "handle_command", lambda text, cfg, sp, **kw: c.Outcome("точно"))
    sp = SpySpeaker()
    ctrl = AssistantController(_config(), sp)
    ctrl.run_one_cycle(FakeListener(), FakeRecognizer("джони", "запусти дота"), silent_mic())
    _join_worker(ctrl)
    assert calls == []
    assert sp.acks == 0


def test_joined_without_name_is_silent_and_logged(monkeypatch):
    # Имени в расшифровке нет → похоже на ложное срабатывание Vosk. Джони
    # молчит, но строка в истории остаётся — чтобы потом видеть, как часто
    # он ловит своё имя зря. Настоящий handle_command, без подмены: с
    # use_brain=False он не зовёт ни execute, ни модель, так что сквозной
    # прогон безопасен — и только он проверяет НАСТОЯЩИЙ via.
    import johnny.controller as c

    rows = []
    monkeypatch.setattr(c.history, "add", lambda text, via=None: rows.append((text, via)))
    sp = SpySpeaker()
    ctrl = AssistantController(_config(), sp)
    ctrl.run_one_cycle(FakeListener(), FakeRecognizer("да я говорю ему"), talking_mic())
    assert sp.acks == 0
    assert sp.said == []
    assert rows == [("да я говорю ему", "слитно(без имени)[0.00]/мимо")]


def test_joined_without_name_and_empty_text_is_marked_empty(monkeypatch):
    # Пустая расшифровка — рутинный случай: так выглядит сработавший фильтр
    # эха подсказки. Раньше пометка «мимо» была захардкожена, и этот случай
    # неотличимо сливался с «услышал слова, но они ни во что не сошлись» —
    # а это разные решения при настройке порогов.
    import johnny.controller as c

    rows = []
    monkeypatch.setattr(c.history, "add", lambda text, via=None: rows.append((text, via)))
    sp = SpySpeaker()
    ctrl = AssistantController(_config(), sp)
    ctrl.run_one_cycle(FakeListener(), FakeRecognizer(""), talking_mic())
    assert sp.said == []
    assert rows == [("", "слитно(без имени)[0.00]/пусто")]


def test_joined_without_name_does_not_ask_model(monkeypatch):
    import johnny.controller as c

    flags = {}
    monkeypatch.setattr(c.history, "add", lambda text, via=None: None)
    monkeypatch.setattr(
        c, "handle_command",
        lambda text, cfg, sp, **kw: (flags.update(kw), c.Outcome("мимо", handled=False))[1],
    )
    ctrl = AssistantController(_config(), SpySpeaker())
    ctrl.run_one_cycle(FakeListener(), FakeRecognizer("да я говорю ему"), talking_mic())
    assert flags["use_brain"] is False
    assert flags["speak_failures"] is False


def test_joined_with_name_but_no_command_reports_failure_without_retry(monkeypatch):
    # Имя подтверждено двумя движками — человек точно звал, но команда не
    # сошлась. РАНЬШЕ это само переходило в обычную ветку «позвал и ждёт»
    # (звук + повторный вопрос) — эту синхронную подстраховку пришлось
    # убрать: выполнение теперь идёт в фоновом потоке (ради «Джони, стоп»),
    # а второй поток не может одновременно с главным читать общий микрофон.
    # Теперь непонятая слитная команда просто отвечает «не понял»
    # (handle_command, а не заглушка) и ничего не переспрашивает сама.
    import johnny.controller as c

    rows = []
    monkeypatch.setattr(c.history, "add", lambda text, via=None: rows.append((text, via)))
    monkeypatch.setattr(c, "handle_command", lambda text, cfg, sp, **kw: c.Outcome("мимо", handled=False))
    sp = SpySpeaker()
    ctrl = AssistantController(_config(), sp)
    rec = FakeRecognizer("джони бубубу")
    ctrl.run_one_cycle(FakeListener(), rec, talking_mic())
    _join_worker(ctrl)
    assert sp.acks == 0           # не переходим в режим «позвал и ждёт»
    assert rec.calls == 1         # распознали один раз, повторно не звали
    assert rows == [("бубубу", "слитно[0.00]/мимо")]


def test_joined_success_is_marked_in_history(monkeypatch):
    import johnny.controller as c

    rows = []
    monkeypatch.setattr(c.history, "add", lambda text, via=None: rows.append((text, via)))
    monkeypatch.setattr(c, "handle_command", lambda text, cfg, sp, **kw: c.Outcome("точно"))
    ctrl = AssistantController(_config(), SpySpeaker())
    ctrl.run_one_cycle(FakeListener(), FakeRecognizer("джони запусти дота"), talking_mic())
    _join_worker(ctrl)
    assert rows == [("запусти дота", "слитно[0.00]/точно")]


def test_cycle_flushes_microphone_at_the_end(monkeypatch):
    # Без флаша Джони, произнеся вслух ответ со словом «Джони», разбудил бы
    # сам себя: поток теперь открыт всегда.
    import johnny.controller as c

    monkeypatch.setattr(c.history, "add", lambda text, via=None: None)
    monkeypatch.setattr(c, "handle_command", lambda text, cfg, sp, **kw: c.Outcome("точно"))
    ctrl = AssistantController(_config(), SpySpeaker())
    mic = talking_mic()
    ctrl.run_one_cycle(FakeListener(), FakeRecognizer("джони запусти дота"), mic)
    _join_worker(ctrl)
    assert mic.preroll() == b""


def _config_recovery():
    return Config(
        apps={},
        commands=[CommandRule("сделай громче", "system", "volume_up")],
        settings=Settings("джони", "models/vosk", "off", "medium", "cuda"),
    )


def test_misheard_name_is_recovered_and_trusted(monkeypatch):
    """Живой случай: Whisper услышал «Джони» как «не».

    Раньше якорь имени не находился, Джони уходил в тихий режим
    (use_brain=False, провалы молча) — и фраза, которой нужна модель,
    утопала в тишине без всякого следа для человека.
    """
    import johnny.controller as c

    seen = {}
    monkeypatch.setattr(c.history, "add", lambda text, via=None: seen.setdefault("via", via))
    monkeypatch.setattr(
        c,
        "handle_command",
        lambda text, cfg, sp, **kw: (
            seen.update(text=text, use_brain=kw.get("use_brain")),
            c.Outcome("точно"),
        )[1],
    )
    ctrl = AssistantController(_config_recovery(), SpySpeaker())
    ctrl.run_one_cycle(FakeListener(), FakeRecognizer("не сделай громче"), talking_mic())
    _join_worker(ctrl)
    assert seen["text"] == "сделай громче", "лишнее слово должно быть срезано"
    assert seen["use_brain"] is True, "человек звал — модель спрашивать можно"
    assert seen["via"] == "слитно(имя восстановлено)[0.00]/точно"


def test_latin_prefix_is_recovered_and_trusted(monkeypatch):
    """Живой баг (2026-08-06): Whisper услышал активатор «Джони» как «vd».

    recover_misheard_name тут бессилен (остаток — не команда, а обычный
    разговор), поэтому recover_latin_prefix — второй, более широкий рубеж.
    """
    import johnny.controller as c

    seen = {}
    monkeypatch.setattr(c.history, "add", lambda text, via=None: seen.setdefault("via", via))
    monkeypatch.setattr(
        c,
        "handle_command",
        lambda text, cfg, sp, **kw: (
            seen.update(text=text, use_brain=kw.get("use_brain")),
            c.Outcome("точно"),
        )[1],
    )
    ctrl = AssistantController(_config_recovery(), SpySpeaker())
    ctrl.run_one_cycle(FakeListener(), FakeRecognizer("vd а вообще всё нормально"), talking_mic())
    _join_worker(ctrl)
    assert seen["text"] == "а вообще все нормально"  # ё→е — общая нормализация _words
    assert seen["use_brain"] is True, "человек звал — модель спрашивать можно"
    assert seen["via"] == "слитно(имя восстановлено: латиница)[0.00]/точно"


# --- «Джони, стоп»: барж-ин, пока Джони занят предыдущей командой ---


def _busy_worker():
    """Настоящий работающий поток (не мок) — делает ctrl.busy правдиво True,
    без гонок и таймингов реального handle_command."""
    release = threading.Event()
    thread = threading.Thread(target=release.wait, daemon=True)
    thread.start()
    return thread, release


def _forbid_history(monkeypatch, c):
    monkeypatch.setattr(
        c.history, "add", lambda text, via=None: pytest.fail(f"не должно логировать: {text!r}/{via!r}")
    )


def test_stop_word_with_name_in_same_block_cancels_and_stops_sound(monkeypatch):
    """Основной случай: «Джони, стоп» сказано одним махом, оба слова попали
    в один и тот же частичный результат Vosk."""
    import johnny.controller as c

    rows = []
    monkeypatch.setattr(c.history, "add", lambda text, via=None: rows.append((text, via)))
    stopped = []
    monkeypatch.setattr(c.sounds, "stop_all", lambda: stopped.append(True))

    ctrl = AssistantController(_config(), SpySpeaker())
    worker, release = _busy_worker()
    ctrl._worker = worker
    assert ctrl.busy is True

    listener = FakeListener(recognize_texts=["джони стоп"])
    ctrl.run_one_cycle(listener, FakeRecognizer(), FakeMic())

    assert ctrl._cancel.is_set()
    assert stopped == [True]
    assert rows == [("джони стоп", "стоп(перебил)")]
    assert listener.reset_calls == 1
    release.set()
    worker.join(timeout=1)


def test_stop_word_after_pause_across_two_blocks_still_cancels(monkeypatch):
    """ГЛАВНАЯ ПРИЧИНА ПЕРЕДЕЛКИ (2026-08-03): «Джони» и «стоп» иногда
    приходят в РАЗНЫХ частичных результатах Vosk (естественная пауза после
    имени, или Vosk сам разбивает высказывания) — раньше механика на записи
    ломалась именно на этом. Память о недавно услышанном имени должна
    пережить границу между отдельными вызовами run_one_cycle."""
    import johnny.controller as c

    rows = []
    monkeypatch.setattr(c.history, "add", lambda text, via=None: rows.append((text, via)))
    stopped = []
    monkeypatch.setattr(c.sounds, "stop_all", lambda: stopped.append(True))

    ctrl = AssistantController(_config(), SpySpeaker())
    worker, release = _busy_worker()
    ctrl._worker = worker

    listener = FakeListener(recognize_texts=["джони", "стоп"])
    ctrl.run_one_cycle(listener, FakeRecognizer(), FakeMic())  # только имя
    assert not ctrl._cancel.is_set(), "одного имени без «стоп» недостаточно"
    ctrl.run_one_cycle(listener, FakeRecognizer(), FakeMic())  # «стоп» позже, отдельным блоком

    assert ctrl._cancel.is_set()
    assert stopped == [True]
    assert rows == [("стоп", "стоп(перебил)")]
    release.set()
    worker.join(timeout=1)


def test_name_memory_expires_after_window(monkeypatch):
    """Если «стоп» пришёл СЛИШКОМ ПОЗДНО после имени (за пределами
    _NAME_MEMORY_SECONDS) — это уже не то же самое обращение, реагировать
    нельзя."""
    import johnny.controller as c

    _forbid_history(monkeypatch, c)
    stopped = []
    monkeypatch.setattr(c.sounds, "stop_all", lambda: stopped.append(True))
    clock = {"t": 1000.0}
    monkeypatch.setattr(c.time, "monotonic", lambda: clock["t"])

    ctrl = AssistantController(_config(), SpySpeaker())
    worker, release = _busy_worker()
    ctrl._worker = worker

    listener = FakeListener(recognize_texts=["джони", "стоп"])
    ctrl.run_one_cycle(listener, FakeRecognizer(), FakeMic())
    clock["t"] += c._NAME_MEMORY_SECONDS + 1
    ctrl.run_one_cycle(listener, FakeRecognizer(), FakeMic())

    assert not ctrl._cancel.is_set()
    assert stopped == []
    release.set()
    worker.join(timeout=1)


def test_non_stop_word_while_busy_is_ignored(monkeypatch):
    """Пока Джони занят, новую команду брать нельзя — она либо перепутается с
    текущей, либо это вообще эхо его собственного голоса."""
    import johnny.controller as c

    _forbid_history(monkeypatch, c)
    monkeypatch.setattr(
        c, "handle_command", lambda *a, **kw: pytest.fail("не должно выполняться, пока занят")
    )
    stopped = []
    monkeypatch.setattr(c.sounds, "stop_all", lambda: stopped.append(True))

    ctrl = AssistantController(_config(), SpySpeaker())
    worker, release = _busy_worker()
    ctrl._worker = worker

    listener = FakeListener(recognize_texts=["джони громкость 5"])
    ctrl.run_one_cycle(listener, FakeRecognizer(), FakeMic())

    assert not ctrl._cancel.is_set()
    assert stopped == []
    release.set()
    worker.join(timeout=1)


def test_stop_word_without_name_is_ignored_while_busy(monkeypatch):
    """«Стоп» без имени рядом (даже недавно) — не наше дело, могло донестись
    откуда угодно (телевизор, разговор в комнате)."""
    import johnny.controller as c

    _forbid_history(monkeypatch, c)
    stopped = []
    monkeypatch.setattr(c.sounds, "stop_all", lambda: stopped.append(True))

    ctrl = AssistantController(_config(), SpySpeaker())
    worker, release = _busy_worker()
    ctrl._worker = worker

    listener = FakeListener(recognize_texts=["стоп"])  # без имени
    ctrl.run_one_cycle(listener, FakeRecognizer(), FakeMic())

    assert not ctrl._cancel.is_set()
    assert stopped == []
    release.set()
    worker.join(timeout=1)


def test_busy_poll_does_not_flush_microphone(monkeypatch):
    """Флаш стёр бы кольцо/очередь микрофона — на busy-пути это не «конец
    хода», а один блок непрерывного потока, флашить его нельзя."""
    import johnny.controller as c

    ctrl = AssistantController(_config(), SpySpeaker())
    worker, release = _busy_worker()
    ctrl._worker = worker

    mic = FakeMic([_LOUD])
    listener = FakeListener(recognize_texts=["джони громкость 5"])
    ctrl.run_one_cycle(listener, FakeRecognizer(), mic)

    assert mic.preroll() != b""
    release.set()
    worker.join(timeout=1)


def test_idle_cycle_keeps_default_silence_window(monkeypatch):
    """Обычная (не busy) команда может быть многословной — сокращать ей
    хвост тишины нельзя, иначе Whisper начнёт терять конец фразы."""
    import johnny.controller as c

    seen = {}

    def fake_record(mic, **kwargs):
        seen.update(kwargs)
        return b"", False

    monkeypatch.setattr(c.audio, "record_until_silence", fake_record)
    ctrl = AssistantController(_config(), SpySpeaker())

    ctrl.run_one_cycle(FakeListener(), FakeRecognizer(), FakeMic())

    assert "silence_seconds" not in seen


def test_joined_turn_dispatches_without_waiting_for_handle_command(monkeypatch):
    """Сквозная проверка: run_one_cycle возвращается, НЕ дожидаясь конца
    handle_command — именно это даёт «Джони, стоп» шанс перебить его,
    пока предыдущая команда ещё говорит/делает что-то."""
    import johnny.controller as c

    started = threading.Event()
    release = threading.Event()

    def slow_handle_command(text, cfg, sp, **kw):
        started.set()
        release.wait(timeout=2)
        return c.Outcome("точно")

    monkeypatch.setattr(c.history, "add", lambda text, via=None: None)
    monkeypatch.setattr(c, "handle_command", slow_handle_command)
    ctrl = AssistantController(_config(), SpySpeaker())

    ctrl.run_one_cycle(FakeListener(), FakeRecognizer("джони запусти дота"), talking_mic())

    assert started.wait(timeout=1), "фоновый поток обязан запуститься"
    assert ctrl.busy is True  # run_one_cycle не дождался handle_command
    release.set()
    ctrl._worker.join(timeout=1)
    assert ctrl.busy is False
