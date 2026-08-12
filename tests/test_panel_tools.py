"""Тулкиты в панели: что уходит в действие и что показывается в строке готовности.

Tk здесь не поднимается вовсе — в этом и смысл выноса логики в panel_tools.py.
Проверяем три вещи: кнопка идёт тем же путём, что и голос (через registry),
человек видит причину, по которой тулкит не готов, а не пустое поле, и очередь
ответов из фоновых потоков не встаёт от одного упавшего задания.
"""

import queue

import pytest

import johnny.panel_tools as panel_tools
from johnny.actions.registry import ActionResult, registry


@pytest.fixture
def tool():
    return panel_tools.Tool(
        action="fake_tool",
        connector="fake",
        title="Тест",
        hint="что-нибудь",
        button="Жми",
    )


@pytest.fixture
def executed(monkeypatch):
    """Подменяет registry.execute и запоминает вызовы."""
    calls = []

    def fake_execute(action, argument, **kwargs):
        calls.append((action, argument, kwargs))
        return ActionResult(True, "Готово")

    monkeypatch.setattr(panel_tools.registry, "execute", fake_execute)
    return calls


# --- run() ---------------------------------------------------------------


def test_value_goes_to_the_registry_action_not_to_the_connector(tool, executed):
    """Ключевое: кнопка использует то же действие, что и голосовая команда.

    Если это сломается, разбор языка и текст отказов разъедутся между кнопкой и
    голосом — ровно то, ради чего логика тут, а не в panel.py.
    """
    ok, message = panel_tools.run(tool, "привет на английский", {"cfg": 1})
    assert (ok, message) == (True, "Готово")
    assert executed == [("fake_tool", "привет на английский", {"config": {"cfg": 1}})]


def test_empty_field_is_refused_before_the_action(tool, executed):
    ok, message = panel_tools.run(tool, "   ", {})
    assert not ok
    # Подсказка поля в тексте: «заполните поле» без указания какого — бесполезно.
    assert tool.hint in message
    assert executed == []


def test_action_refusal_reaches_the_button(tool, monkeypatch):
    monkeypatch.setattr(
        panel_tools.registry,
        "execute",
        lambda *a, **k: ActionResult(False, "Нужен ключ в secrets.yaml"),
    )
    ok, message = panel_tools.run(tool, "что-то", {})
    assert not ok and message == "Нужен ключ в secrets.yaml"


def test_exception_inside_the_tool_does_not_escape(tool, monkeypatch):
    """Панель не должна падать от кнопки: человек нажал кнопку, а не согласился
    перезапускать Джони."""

    def boom(*a, **k):
        raise RuntimeError("внутри всё плохо")

    monkeypatch.setattr(panel_tools.registry, "execute", boom)
    ok, message = panel_tools.run(tool, "что-то", {})
    assert not ok and "johnny.log" in message


def test_exception_text_does_not_reach_the_screen(tool, monkeypatch):
    """Внутренности исключения могут содержать что угодно, вплоть до ключа в URL."""

    def boom(*a, **k):
        raise RuntimeError("token=SECRET123")

    monkeypatch.setattr(panel_tools.registry, "execute", boom)
    _, message = panel_tools.run(tool, "что-то", {})
    assert "SECRET123" not in message


# --- readiness() / _one() ------------------------------------------------


class FakeConnector:
    requires_consent = False
    _consent = True

    def __init__(self, answer=(True, "")):
        self._answer = answer

    def available(self):
        return self._answer


def test_readiness_covers_every_tool(monkeypatch):
    """Ключ — action, а не коннектор: на azure-vision и на faceplusplus висит по
    две карточки, и по имени коннектора вторая затирала бы первую."""
    monkeypatch.setattr(panel_tools.factory, "build", lambda *a, **k: FakeConnector())
    state = panel_tools.readiness({})
    assert set(state) == {t.action for t in panel_tools.TOOLS}
    assert len(state) == len(panel_tools.TOOLS)
    assert all(ok for ok, _ in state.values())


def test_missing_config_is_a_reason_not_a_crash(tool):
    """Панель обязана открыться и без конфига — иначе конфиг через неё не починить."""
    ok, reason = panel_tools._one(tool, None)
    assert not ok and reason


def test_unknown_connector_is_a_reason(tool, monkeypatch):
    monkeypatch.setattr(panel_tools.factory, "build", lambda *a, **k: None)
    ok, reason = panel_tools._one(tool, {})
    assert not ok and reason


def test_missing_consent_names_consent_not_the_key(tool, monkeypatch):
    """Ключ на месте, а согласия нет — это разные причины.

    Скажи «нужен ключ» — человек полезет в secrets.yaml и не найдёт там ничего
    исправлять.
    """

    class NeedsConsent(FakeConnector):
        requires_consent = True
        _consent = False

    monkeypatch.setattr(panel_tools.factory, "build", lambda *a, **k: NeedsConsent())
    ok, reason = panel_tools._one(tool, {})
    assert not ok
    assert "согласие" in reason.lower() and "settings.yaml" in reason


def test_available_reason_is_passed_through_as_is(tool, monkeypatch):
    monkeypatch.setattr(
        panel_tools.factory,
        "build",
        lambda *a, **k: FakeConnector((False, "pip install argostranslate")),
    )
    assert panel_tools._one(tool, {}) == (False, "pip install argostranslate")


def test_broken_factory_does_not_break_the_whole_row(tool, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("нет такого ключа в конфиге")

    monkeypatch.setattr(panel_tools.factory, "build", boom)
    ok, reason = panel_tools._one(tool, {})
    assert not ok and "johnny.log" in reason


def test_broken_available_does_not_break_the_whole_row(tool, monkeypatch):
    class Broken(FakeConnector):
        def available(self):
            raise RuntimeError("пакет наполовину установлен")

    monkeypatch.setattr(panel_tools.factory, "build", lambda *a, **k: Broken())
    ok, reason = panel_tools._one(tool, {})
    assert not ok and "johnny.log" in reason


def test_one_broken_tool_does_not_hide_the_others(monkeypatch):
    """Строка готовности собирается по всем: сломанный social-analyzer не должен
    оставить остальные карточки без состояния."""

    def build(name, config, **kwargs):
        if name == "social-analyzer":
            raise RuntimeError("пакет не встал")
        return FakeConnector()

    monkeypatch.setattr(panel_tools.factory, "build", build)
    state = panel_tools.readiness({})
    assert set(state) == {t.action for t in panel_tools.TOOLS}
    assert not state["find_profiles"][0]
    assert state["translate"][0]


# --- таблица TOOLS -------------------------------------------------------
def test_every_tool_points_at_a_registered_action():
    """Опечатка в имени действия иначе всплывёт только при клике по кнопке."""
    known = set(registry.registered_actions())
    assert {t.action for t in panel_tools.TOOLS} <= known


def test_every_tool_points_at_a_known_connector():
    from johnny.connectors import factory

    known = set(factory.available_names())
    assert {t.connector for t in panel_tools.TOOLS} <= known


def test_file_tools_are_marked_as_such():
    """kind="file" рисует кнопку «Обзор». Все карточки про картинки работают с
    локальным файлом, и заставлять человека печатать путь руками — верный путь к
    опечатке. Ник и текст перевода печатаются, там «Обзор» ни к чему.

    kind="files" — отдельный случай: сравнение лиц требует ДВУХ снимков, и
    «Обзор» там дописывает второй к первому, а не затирает его.
    """
    by_action = {t.action: t for t in panel_tools.TOOLS}
    assert by_action["describe_image"].kind == "file"
    assert by_action["read_image_text"].kind == "file"
    assert by_action["analyze_face"].kind == "file"
    assert by_action["compare_faces"].kind == "files"
    assert by_action["find_profiles"].kind == "text"
    assert by_action["translate"].kind == "text"


def test_every_tool_has_a_voice_phrase():
    """Подсказка «как это же сказать голосом» — не украшение: панель тут заодно
    и способ узнать, что Джони вообще умеет."""
    assert all(t.phrase for t in panel_tools.TOOLS)


# --- drain(): ответы из фоновых потоков ----------------------------------


class Closed(Exception):
    """Заменяет tk.TclError: сам Tk тут не нужен, нужно только его поведение."""


def test_drain_runs_everything_in_order():
    done = []
    pending = queue.Queue()
    for i in range(3):
        pending.put(lambda i=i: done.append(i))
    assert panel_tools.drain(pending) is True
    assert done == [0, 1, 2]


def test_empty_queue_asks_for_another_tick():
    """Пустая очередь — это норма, а не сигнал остановиться: таймер тикает
    постоянно, а тулкиты нажимают редко."""
    assert panel_tools.drain(queue.Queue()) is True


def test_one_failed_job_does_not_freeze_the_queue():
    """Живой баг в зачатке: если очередь встаёт на первой ошибке, то все тулкиты
    замолкают навсегда, и выглядит это как «панель сломалась»."""
    done = []
    pending = queue.Queue()

    def boom():
        raise RuntimeError("виджет уже другой")

    pending.put(boom)
    pending.put(lambda: done.append("после"))
    assert panel_tools.drain(pending) is True
    assert done == ["после"]


def test_closed_panel_stops_the_pump():
    """Панель закрыли — показывать ответ некуда, и просить следующий тик не у
    кого: after на разрушенном виджете сам бросит ошибку."""
    pending = queue.Queue()
    pending.put(lambda: (_ for _ in ()).throw(Closed()))
    pending.put(lambda: pytest.fail("после закрытия панели ничего выполнять нельзя"))
    assert panel_tools.drain(pending, stop_on=(Closed,)) is False


def test_jobs_added_while_draining_are_picked_up():
    """Два тулкита могут ответить одновременно: второй ответ приходит, пока
    разбирается первый, и ждать до следующего тика ему незачем."""
    done = []
    pending = queue.Queue()

    def first():
        done.append("первый")
        pending.put(lambda: done.append("второй"))

    pending.put(first)
    panel_tools.drain(pending)
    assert done == ["первый", "второй"]


# --- status(): состояние движков ------------------------------------------


def test_both_engines_up_is_listening():
    state = panel_tools.status(listener=True, recognizer=True)
    assert state.tone == "ok" and state.listening


def test_paused_is_not_listening():
    state = panel_tools.status(listener=True, recognizer=True, paused=True)
    assert state.tone == "warn" and not state.listening


def test_no_engines_at_all_is_the_worst_case():
    state = panel_tools.status(listener=False, recognizer=False)
    assert state.tone == "bad" and not state.listening


def test_broken_engines_outrank_pause():
    """Пауза снимается одной кнопкой, отсутствие движков — нет.

    Показать «на паузе» вместо «не слышит» — значит отправить человека жать
    кнопку, которая ничего не починит.
    """
    state = panel_tools.status(listener=False, recognizer=False, paused=True)
    assert state.tone == "bad"


def test_missing_whisper_still_hears_the_name():
    state = panel_tools.status(listener=True, recognizer=False)
    assert state.tone == "warn" and state.listening


def test_missing_vosk_listens_without_the_wake_word():
    state = panel_tools.status(listener=False, recognizer=True)
    assert state.tone == "warn" and state.listening


def test_every_combination_says_something_different():
    """Четыре состояния — четыре разные фразы. Одинаковый текст на двух разных
    поломках отправляет чинить не то."""
    texts = {
        panel_tools.status(listener=a, recognizer=b).text
        for a in (True, False)
        for b in (True, False)
    }
    assert len(texts) == 4


def test_tone_is_always_one_the_panel_knows():
    for a in (True, False):
        for b in (True, False):
            for paused in (True, False):
                state = panel_tools.status(listener=a, recognizer=b, paused=paused)
                assert state.tone in {"ok", "warn", "bad"}


# ── карточки захвата ─────────────────────────────────────────────────────────

def _capture_tools():
    return [t for t in panel_tools.TOOLS if t.kind == "pick"]


def test_capture_cards_exist_for_screen_and_ocr():
    """Мышью то же, что голосом: «что на экране» и «прочитай, что на экране»."""
    actions = {t.action for t in _capture_tools()}
    assert actions == {"describe_capture", "read_capture"}


def test_capture_cards_offer_every_source():
    for tool in _capture_tools():
        assert [value for value, _ in tool.options] == [
            "screen", "region", "window", "camera",
        ]


def test_capture_cards_offer_exactly_what_the_action_knows():
    """Список в панели и _SOURCES в действии обязаны совпадать.

    Разъехавшись, они дают кнопку, которая отвечает «Не знаю, откуда взять
    картинку» — при том что источник человек выбрал из предложенного списка.
    """
    from johnny.actions import connector_action

    known = set(connector_action._SOURCES)
    for tool in _capture_tools():
        assert {value for value, _ in tool.options} == known, tool.action


def test_both_capture_cards_share_one_list():
    """Один кортеж на обе карточки: разошедшиеся копии — это «область» в одной
    карточке и её отсутствие во второй, причём молча."""
    describe, read = sorted(_capture_tools(), key=lambda t: t.action)
    assert describe.options is read.options


def test_capture_cards_have_a_default_value():
    """Карточка отказывает на пустом поле (см. run()), а вписать сюда нечего:
    файла ещё нет. Поэтому у списка обязано быть значение с самого начала."""
    for tool in _capture_tools():
        assert tool.options and tool.options[0][0]


def test_only_capture_cards_are_pick():
    """У остальных тулкитов человек называет файл или ник — там нужно поле."""
    for tool in panel_tools.TOOLS:
        if tool.action not in ("describe_capture", "read_capture"):
            assert tool.kind in ("text", "file", "files"), tool.action


def test_every_tool_action_is_registered():
    """Карточка без действия — кнопка, которая молча ничего не делает."""
    import johnny.actions  # noqa: F401  — регистрация всех действий

    for tool in panel_tools.TOOLS:
        assert tool.action in registry, tool.action
