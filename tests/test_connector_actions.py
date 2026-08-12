"""Голосовые команды к внешним тулзам: разбор фразы и путь до коннектора.

Коннекторы подменяются целиком: их собственное поведение проверено в
test_azure_vision/faceplusplus/social_analyzer_connector.py, здесь важно другое
— что фраза доходит до нужного коннектора с нужным аргументом, а отсутствие
конфига не роняет разговор.
"""

import pytest

import johnny.actions.connector_action as connector_action
from johnny.actions.registry import registry
from johnny.connectors.base import ConnectorResult


class FakeConnector:
    def __init__(self, result=None):
        self.calls = []
        self._result = result or ConnectorResult(True, "Нашёл совпадений: 3")

    def call(self, **params):
        self.calls.append(params)
        return self._result


@pytest.fixture
def built(monkeypatch):
    """Подменяет фабрику и запоминает, какой коннектор просили собрать."""
    made = {}

    def fake_build(name, config, **kwargs):
        made.setdefault(name, FakeConnector())
        made["последний"] = name
        return made[name]

    monkeypatch.setattr(connector_action.factory, "build", fake_build)
    return made


def _run(action, argument, config=object()):
    return registry.execute(action, argument, config=config)


def test_describe_image_takes_a_url(built):
    result = _run("describe_image", "https://site/pic.jpg")
    assert result.ok
    assert built["azure-vision"].calls == [
        {"source": "https://site/pic.jpg", "features": connector_action.DESCRIBE_FEATURES}
    ]


def test_describe_image_finds_url_inside_a_phrase(built):
    """Распознавание отдаёт фразу целиком, ссылка в ней — посередине."""
    _run("describe_image", "вот эта https://site/pic.jpg посмотри")
    assert built["azure-vision"].calls[0]["source"] == "https://site/pic.jpg"


def test_describe_image_takes_a_local_path(built):
    """Одна команда и на ссылку, и на файл: Azure принимает оба, разводить их
    значило бы заставлять человека помнить, какую фразу произносить."""
    _run("describe_image", '"D:/скрины/screenshot.png"')
    assert built["azure-vision"].calls[0]["source"] == "D:/скрины/screenshot.png"


def test_describe_image_without_source_asks_for_one(built):
    result = _run("describe_image", "   ")
    assert not result.ok and "картинка" in result.message
    assert built == {}


def test_read_image_text_asks_for_ocr_features(built):
    """features входят и в цену вызова, и в ключ кеша: «прочитай текст» обязан
    просить read, а не описание сцены."""
    _run("read_image_text", "D:/скрины/чек.png")
    assert built["azure-vision"].calls == [
        {"source": "D:/скрины/чек.png", "features": connector_action.READ_FEATURES}
    ]


def test_compare_faces_splits_two_paths(built):
    result = _run("compare_faces", '"D:/фото/один.jpg" и "D:/фото/два.jpg"')
    assert result.ok
    assert built["faceplusplus"].calls == [
        {"mode": "compare", "first": "D:/фото/один.jpg", "second": "D:/фото/два.jpg"}
    ]


def test_compare_faces_splits_without_quotes(built):
    _run("compare_faces", "D:/один.jpg и D:/два.jpg")
    call = built["faceplusplus"].calls[0]
    assert (call["first"], call["second"]) == ("D:/один.jpg", "D:/два.jpg")


def test_compare_faces_keeps_and_inside_a_quoted_path(built):
    """«и» встречается внутри самих путей («D:/фото/Ира/…»), поэтому кавычки
    сильнее разделителя — иначе одна фраза разъезжается на четыре куска."""
    _run("compare_faces", '"D:/фото/Ира и Оля/1.jpg" и "D:/фото/2.jpg"')
    assert built["faceplusplus"].calls[0]["first"] == "D:/фото/Ира и Оля/1.jpg"


def test_compare_faces_with_one_path_refuses(built):
    result = _run("compare_faces", '"D:/фото/один.jpg"')
    assert not result.ok and "два файла" in result.message
    assert built == {}


def test_analyze_face_passes_the_path(built):
    result = _run("analyze_face", '"D:/фото/лицо.jpg"')
    assert result.ok
    assert built["faceplusplus"].calls == [{"mode": "detect", "path": "D:/фото/лицо.jpg"}]


def test_analyze_face_without_path_refuses(built):
    result = _run("analyze_face", "   ")
    assert not result.ok and built == {}


def test_find_profiles_passes_the_username(built):
    result = _run("find_profiles", " durov ")
    assert result.ok
    assert built["social-analyzer"].calls == [{"username": "durov"}]


def test_find_profiles_without_username_refuses(built):
    result = _run("find_profiles", "")
    assert not result.ok and built == {}


def test_missing_config_is_spoken_refusal_not_crash():
    """Старый вызов execute() без config, плагин, тест — команда есть, и
    человек должен услышать почему она не сработала."""
    result = registry.execute("describe_image", "https://site/pic.jpg")
    assert not result.ok and "не настроены" in result.message


def test_unknown_connector_name_is_refusal(monkeypatch):
    monkeypatch.setattr(connector_action.factory, "build", lambda name, config, **kw: None)
    result = _run("describe_image", "https://site/pic.jpg")
    assert not result.ok


def test_connector_refusal_reaches_the_human(monkeypatch):
    """Отказ коннектора (нет ключа, кончилась квота) произносится как есть:
    ради этого текста available() и пишет причину по-русски."""
    refusing = FakeConnector(
        ConnectorResult(False, "Суточный лимит azure-vision исчерпан: 160 из 160", reason="quota")
    )
    monkeypatch.setattr(connector_action.factory, "build", lambda name, config, **kw: refusing)
    result = _run("describe_image", "https://site/pic.jpg")
    assert not result.ok and "лимит" in result.message


def test_cached_answer_is_spoken_like_a_fresh_one(monkeypatch):
    """Человеку незачем знать, что ответ из кеша: это офлайн-режим, а не сбой."""
    cached = FakeConnector(ConnectorResult(True, "На картинке: a cat", cached=True))
    monkeypatch.setattr(connector_action.factory, "build", lambda name, config, **kw: cached)
    result = _run("describe_image", "https://site/pic.jpg")
    assert result.ok and result.message == "На картинке: a cat"


def test_every_connector_action_is_registered():
    for action in (
        "describe_image",
        "read_image_text",
        "compare_faces",
        "analyze_face",
        "search_face_archive",
        "find_profiles",
        "translate",
    ):
        assert action in registry


# --- Архив лиц: когда зовём Face++ вторым мнением ----------------------------


def _archive_answer(matches, message="ответ архива"):
    return ConnectorResult(True, message, data={"faces": 1, "matches": matches, "searched": 9})


@pytest.fixture
def two_stages(monkeypatch):
    """Подменяет оба коннектора и даёт задать ответ каждому.

    Именно связка тут и проверяется: локальный отбор дешёвый, а вердикт Face++
    стоит денег и десяти секунд паузы (min_interval_seconds), поэтому вопрос
    «на кого его звать» — решение, а не деталь.
    """
    made = {"face-index": FakeConnector(), "faceplusplus": FakeConnector()}
    monkeypatch.setattr(
        connector_action.factory, "build", lambda name, config, **kw: made.get(name)
    )
    return made


def test_search_face_archive_needs_a_photo(two_stages):
    result = _run("search_face_archive", "  ")
    assert not result.ok
    assert two_stages["face-index"].calls == []


def test_a_confident_match_does_not_pay_for_a_second_opinion(two_stages):
    """У разных людей косинус 0.026 при пороге 0.40 — уверенное совпадение
    локальная модель уже отличила. Переспрашивать про него значит потратить
    платный вызов и десять секунд паузы на подтверждение известного."""
    two_stages["face-index"]._result = _archive_answer(
        [{"path": "D:/ира.jpg", "score": 0.91, "same": True}], "Похоже, это он: ира.jpg"
    )
    result = _run("search_face_archive", "D:/новое.jpg")
    assert result.ok
    assert two_stages["faceplusplus"].calls == []
    assert result.message == "Похоже, это он: ира.jpg"


def test_a_doubtful_match_gets_a_second_opinion(two_stages):
    """Спорное — ровно тот случай, где второе мнение и нужно."""
    two_stages["face-index"]._result = _archive_answer(
        [{"path": "D:/ира.jpg", "score": 0.33, "same": False}], "Уверенного совпадения нет"
    )
    two_stages["faceplusplus"]._result = ConnectorResult(True, "…", data={"same": True})
    result = _run("search_face_archive", "D:/новое.jpg")
    assert two_stages["faceplusplus"].calls == [
        {"mode": "compare", "first": "D:/новое.jpg", "second": "D:/ира.jpg"}
    ]
    assert "Уверенного совпадения нет" in result.message
    assert "один человек" in result.message


def test_a_disagreeing_second_opinion_is_voiced_too(two_stages):
    """Модели разошлись — человек должен услышать обе, а не одну удобную."""
    two_stages["face-index"]._result = _archive_answer(
        [{"path": "D:/ира.jpg", "score": 0.33, "same": False}], "Уверенного совпадения нет"
    )
    two_stages["faceplusplus"]._result = ConnectorResult(True, "…", data={"same": False})
    assert "разные люди" in _run("search_face_archive", "D:/новое.jpg").message


def test_a_silent_second_opinion_does_not_spoil_the_local_answer(two_stages):
    """Нет согласия, нет ключа, кончилась квота — локальный поиск уже ответил, и
    глушить его сообщением про чужой сервис, которого человек не звал, незачем."""
    two_stages["face-index"]._result = _archive_answer(
        [{"path": "D:/ира.jpg", "score": 0.33, "same": False}], "Уверенного совпадения нет"
    )
    two_stages["faceplusplus"]._result = ConnectorResult(False, "нужно разрешить", reason="no-consent")
    result = _run("search_face_archive", "D:/новое.jpg")
    assert result.ok
    assert result.message == "Уверенного совпадения нет"


def test_an_empty_archive_asks_no_one(two_stages):
    two_stages["face-index"]._result = _archive_answer([], "В архиве такого человека нет")
    _run("search_face_archive", "D:/новое.jpg")
    assert two_stages["faceplusplus"].calls == []


def test_a_refusing_archive_asks_no_one(two_stages):
    """Пустой архив или нет пакета — второе мнение не про что запрашивать."""
    two_stages["face-index"]._result = ConnectorResult(False, "Архив лиц пуст", reason="unavailable")
    result = _run("search_face_archive", "D:/новое.jpg")
    assert not result.ok
    assert two_stages["faceplusplus"].calls == []


# --- Перевод: разбор языка из хвоста фразы -----------------------------------


@pytest.mark.parametrize(
    "said, text, target",
    [
        ("привет на английский", "привет", "en"),
        ("привет на английском", "привет", "en"),
        ("доброе утро на немецкий", "доброе утро", "de"),
        # «как будет X по-английски» — самая обиходная форма, и дефис здесь
        # приходит одним токеном, а не отдельным словом.
        ("привет по-английски", "привет", "en"),
        ("hello по-русски", "hello", "ru"),
    ],
)
def test_translate_takes_the_language_from_the_phrase(built, said, text, target):
    _run("translate", said)
    assert built["translator"].calls == [{"text": text, "target": target}]


def test_translate_without_language_flips_russian_to_english(built):
    """«Переведи привет» без указания языка — поведение настольных
    переводчиков, иначе «на английский» приходится говорить каждый раз."""
    _run("translate", "привет как дела")
    assert built["translator"].calls == [{"text": "привет как дела", "target": "en"}]


def test_translate_without_language_flips_foreign_to_russian(built):
    _run("translate", "how are you")
    assert built["translator"].calls == [{"text": "how are you", "target": "ru"}]


def test_trailing_na_is_kept_when_it_is_not_a_language(built):
    """«Переведи деньги на карту» — не перевод на язык «карту». Хвост режется
    только если слово после «на» действительно язык."""
    _run("translate", "переведи деньги на карту")
    assert built["translator"].calls[0]["text"] == "переведи деньги на карту"


def test_translate_without_text_refuses(built):
    result = _run("translate", "  ")
    assert not result.ok and built == {}


def test_commands_yaml_phrases_route_to_these_actions():
    """Действие без фразы в commands.yaml недосягаемо голосом — а это
    единственный способ его вызвать."""
    from johnny.config import load_config

    actions = {rule.action for rule in load_config("config").commands}
    assert {
        "describe_image",
        "read_image_text",
        "compare_faces",
        "analyze_face",
        "search_face_archive",
        "find_profiles",
    } <= actions


def test_phrases_reach_the_action_through_the_router():
    """«Что на картинке» не должна раньше зацепиться за «открой *» — порядок
    правил в commands.yaml здесь и проверяется."""
    from johnny.config import load_config
    from johnny.router import route

    config = load_config("config")
    routed = route("что на картинке https://site/pic.jpg", config.commands)
    assert routed is not None and routed.action == "describe_image"
    assert route("найди профили durov", config.commands).action == "find_profiles"
    assert route("что за лицо C:/фото.jpg", config.commands).action == "analyze_face"
    assert route("сравни лица C:/1.jpg и C:/2.jpg", config.commands).action == "compare_faces"
    assert route("переведи привет на английский", config.commands).action == "translate"


def test_archive_phrases_promise_only_the_archive():
    """Слово «архив» в каждой фразе — граница обещания, а не украшение.

    Коннектор ищет ТОЛЬКО среди снимков, которые человек сам сложил в индекс.
    Фразы «найди это лицо» здесь нет и быть не должно: она обещает поиск по
    интернету, которого у нас нет ни одним коннектором — на этом уже обожглись
    с TinEye и Azure.
    """
    from johnny.config import load_config
    from johnny.router import route

    config = load_config("config")
    for said in ("есть ли в архиве C:/фото.jpg", "найди в архиве C:/фото.jpg"):
        routed = route(said, config.commands)
        assert routed is not None and routed.action == "search_face_archive", said

    for rule in config.commands:
        if rule.action == "search_face_archive":
            assert "архив" in rule.pattern, rule.pattern

    # «найди профили» рядом и начинается так же — жадное правило архива забрало
    # бы её себе.
    assert route("найди профили durov", config.commands).action == "find_profiles"


def test_read_text_phrase_wins_over_describe():
    """«Прочитай текст с картинки» стоит перед «что на картинке»: обе фразы про
    картинку, но features у них разные, и порядок правил тут и решает."""
    from johnny.config import load_config
    from johnny.router import route

    config = load_config("config")
    routed = route("прочитай текст с картинки D:/чек.png", config.commands)
    # Роутер приводит фразу к нижнему регистру, поэтому путь доезжает
    # строчными. Для Windows это безразлично — регистр в путях там не значим.
    assert routed.action == "read_image_text" and routed.argument == "d:/чек.png"


def test_removed_phrases_are_gone_from_commands():
    """«Откуда эта картинка» и «найди это лицо» выполнить нечем: у Azure нет
    поиска по индексу интернета, Face++ по сети не ходит. Обещать команду,
    которой нет, хуже, чем не иметь её вовсе."""
    from johnny.config import load_config

    actions = {rule.action for rule in load_config("config").commands}
    assert "reverse_image" not in actions and "face_search" not in actions


def test_translate_phrase_does_not_swallow_the_word_fraza():
    """«переведи фразу *» стоит перед жадным «переведи *» — иначе слово
    «фразу» уезжает в переводимый текст."""
    from johnny.config import load_config
    from johnny.router import route

    routed = route("переведи фразу доброе утро", load_config("config").commands)
    assert routed.action == "translate" and routed.argument == "доброе утро"


# ── свои снимки: экран, область, окно, камера ────────────────────────────────

@pytest.fixture
def grabbed(monkeypatch, tmp_path):
    """Подменяет захват: снимок «получается», путь настоящий и существует."""
    made = {"discarded": []}

    def fake_grab(kind):
        def grab(*args, **kwargs):
            path = tmp_path / f"{kind}.png"
            path.write_bytes(b"png")
            made[kind] = str(path)
            return str(path), ""

        return grab

    monkeypatch.setattr(connector_action.capture, "screen", fake_grab("screen"))
    monkeypatch.setattr(connector_action.capture, "region", fake_grab("region"))
    monkeypatch.setattr(connector_action.capture, "window", fake_grab("window"))
    monkeypatch.setattr(connector_action.capture, "camera", fake_grab("camera"))
    monkeypatch.setattr(
        connector_action.capture, "discard", lambda path: made["discarded"].append(path)
    )
    return made


def test_describe_capture_sends_its_own_screenshot(built, grabbed):
    """«Что на экране» раньше не работала вовсе: назвать путь к файлу, которого
    ещё нет на диске, человек не может."""
    result = _run("describe_capture", "screen")
    assert result.ok
    assert built["azure-vision"].calls == [
        {"source": grabbed["screen"], "features": connector_action.DESCRIBE_FEATURES}
    ]


def test_read_capture_uses_ocr_features(built, grabbed):
    _run("read_capture", "screen")
    assert built["azure-vision"].calls[0]["features"] == connector_action.READ_FEATURES


def test_capture_source_picks_the_device(built, grabbed):
    _run("describe_capture", "camera")
    assert built["azure-vision"].calls[0]["source"] == grabbed["camera"]
    _run("read_capture", "window")
    assert built["azure-vision"].calls[1]["source"] == grabbed["window"]


def test_every_known_source_is_actually_reachable(built, grabbed):
    """Каждый источник из _SOURCES обязан звать одноимённую функцию захвата.

    Ключ, которому в capture.py ничего не соответствует, отвечал бы человеку
    «не удалось» уже после того, как он выбрал источник из списка в панели.
    """
    for name in connector_action._SOURCES:
        built.clear()
        result = _run("describe_capture", name)
        assert result.ok, name
        assert built["azure-vision"].calls[0]["source"] == grabbed[name], name


def test_region_asks_for_a_selection_and_sends_only_it(built, grabbed):
    """Выделение — это и есть «селективный скриншот»: наружу уедет кусок, а не
    весь рабочий стол."""
    result = _run("read_capture", "region")
    assert result.ok
    assert built["azure-vision"].calls[0]["source"] == grabbed["region"]
    assert grabbed["discarded"] == [grabbed["region"]]


def test_cancelled_selection_is_spoken_not_swallowed(built, grabbed, monkeypatch):
    """Человек нажал Escape — он должен услышать это, а не тишину. И платить
    за отменённое выделение тоже не надо."""
    monkeypatch.setattr(
        connector_action.capture, "region", lambda *a, **kw: ("", "выделение отменили")
    )
    result = _run("describe_capture", "region")
    assert not result.ok
    assert "выделение отменили" in result.message
    assert "azure-vision" not in built


def test_screenshot_is_deleted_after_the_answer(built, grabbed):
    """На снимке экрана бывает чужая переписка. Он не должен пережить ответ."""
    _run("describe_capture", "screen")
    assert grabbed["discarded"] == [grabbed["screen"]]


def test_screenshot_is_deleted_even_when_the_connector_refuses(built, grabbed, monkeypatch):
    """Отказ Azure (нет ключа, нет согласия) — не повод оставить снимок на диске."""
    monkeypatch.setattr(
        connector_action.factory,
        "build",
        lambda name, config, **kw: FakeConnector(ConnectorResult(False, "нет ключа", reason="unavailable")),
    )
    _run("describe_capture", "screen")
    assert grabbed["discarded"] == [grabbed["screen"]]


def test_screenshot_is_deleted_even_when_the_connector_explodes(grabbed, monkeypatch):
    """Коннектор не должен бросать, но если бросит — снимок всё равно удаляется:
    это и есть смысл finally, а не перестраховка."""
    class Exploding:
        def call(self, **params):
            raise RuntimeError("бум")

    monkeypatch.setattr(connector_action.factory, "build", lambda name, config, **kw: Exploding())
    with pytest.raises(RuntimeError):
        _run("describe_capture", "screen")
    assert grabbed["discarded"] == [grabbed["screen"]]


def test_capture_refusal_is_spoken_not_swallowed(built, monkeypatch):
    """Камера занята Zoom'ом — человек должен услышать причину, а не «Готово»."""
    monkeypatch.setattr(
        connector_action.capture, "camera", lambda *a, **kw: ("", "камера занята другой программой")
    )
    result = _run("describe_capture", "camera")
    assert not result.ok
    assert "занята другой программой" in result.message
    assert "azure-vision" not in built    # за отказ платить не надо


def test_unknown_capture_source_does_not_crash(built):
    result = _run("describe_capture", "телепорт")
    assert not result.ok
    assert "azure-vision" not in built


def test_empty_source_means_the_screen(built, grabbed):
    """Пустой аргумент — самый частый случай: «что видишь» без уточнения."""
    _run("describe_capture", "")
    assert built["azure-vision"].calls[0]["source"] == grabbed["screen"]


# ── память ───────────────────────────────────────────────────────────────────

def test_recognised_text_lands_in_short_memory(built, grabbed, monkeypatch):
    """Следом за «что на экране» почти всегда идёт «переведи это». Без записи
    в буфер модели следующей фразы не на что сослаться: ответа она не видела."""
    turns = []
    monkeypatch.setattr(connector_action.memory, "record_turn", lambda q, a: turns.append((q, a)))
    monkeypatch.setattr(
        connector_action.factory,
        "build",
        lambda name, config, **kw: FakeConnector(ConnectorResult(True, "на картинке: счёт на 900 рублей")),
    )
    _run("read_capture", "screen")
    assert turns == [("прочитай текст", "на картинке: счёт на 900 рублей")]


def test_refusal_is_not_remembered(built, grabbed, monkeypatch):
    """«Нет ключа» в буфере вытеснило бы настоящий ответ: слотов всего пять."""
    turns = []
    monkeypatch.setattr(connector_action.memory, "record_turn", lambda q, a: turns.append((q, a)))
    monkeypatch.setattr(
        connector_action.factory,
        "build",
        lambda name, config, **kw: FakeConnector(ConnectorResult(False, "нет ключа", reason="unavailable")),
    )
    _run("describe_capture", "screen")
    assert turns == []


def test_capture_never_writes_to_long_term_memory(built, grabbed, monkeypatch):
    """На экране бывает переписка. Превращать каждый снимок в вечный факт
    нельзя — для этого есть отдельное «запомни», которое говорит человек."""
    monkeypatch.setattr(
        connector_action.memory,
        "remember",
        lambda text: pytest.fail("снимок экрана не должен попадать в долгую память"),
    )
    _run("describe_capture", "screen")


# ── фразы ────────────────────────────────────────────────────────────────────

def test_screen_phrases_do_not_hit_the_file_rules():
    """«Прочитай текст с экрана» иначе совпало бы с «прочитай текст *», и Джони
    пошёл бы искать файл с именем «с экрана». Порядок правил тут и решает."""
    from johnny.config import load_config
    from johnny.router import route

    commands = load_config("config").commands
    for phrase in ("прочитай что на экране", "прочитай текст с экрана", "что написано на экране"):
        routed = route(phrase, commands)
        assert routed is not None, phrase
        assert (routed.action, routed.argument) == ("read_capture", "screen"), phrase


def test_camera_and_window_phrases_route_to_their_source():
    from johnny.config import load_config
    from johnny.router import route

    commands = load_config("config").commands
    assert route("что видит камера", commands).argument == "camera"
    assert route("что в окне", commands).argument == "window"
    assert route("что на экране", commands).action == "describe_capture"


def test_region_phrases_route_to_the_selection():
    """«Прочитай выделенное» и «что вот здесь» — как человек про это говорит.
    Без своих фраз выделение существовало бы только кнопкой в панели."""
    from johnny.config import load_config
    from johnny.router import route

    commands = load_config("config").commands
    for phrase in ("прочитай выделенное", "прочитай текст из области"):
        routed = route(phrase, commands)
        assert routed is not None, phrase
        assert (routed.action, routed.argument) == ("read_capture", "region"), phrase
    for phrase in ("что вот здесь", "опиши выделенное"):
        routed = route(phrase, commands)
        assert routed is not None, phrase
        assert (routed.action, routed.argument) == ("describe_capture", "region"), phrase


def test_capture_phrases_do_not_shadow_the_plain_screenshot():
    """«Сделай скриншот» кладёт картинку человеку в Pictures насовсем — это
    другая команда, и перехватывать её захват не должен."""
    from johnny.config import load_config
    from johnny.router import route

    commands = load_config("config").commands
    assert route("сделай скриншот", commands).action == "system"
    assert route("скриншот", commands).action == "system"


# ── «запомни, что на экране»: долгая память ──────────────────────────────────

def _ocr(lines):
    return ConnectorResult(True, "Прочитал: " + " ".join(lines), data={"kind": "text", "lines": lines})


@pytest.fixture
def remembered(monkeypatch):
    facts = []
    monkeypatch.setattr(connector_action.memory, "remember", lambda text: facts.append(text))
    return facts


def test_remember_capture_stores_the_text_not_the_spoken_answer(built, grabbed, remembered, monkeypatch):
    """«Прочитал: …» — обращение к человеку. В списке фактов оно выглядело бы
    как чужая реплика, а не как записанный номер заказа."""
    monkeypatch.setattr(
        connector_action.factory,
        "build",
        lambda name, config, **kw: FakeConnector(_ocr(["Заказ 8891", "ул. Садовая 12"])),
    )
    result = _run("remember_capture", "screen")

    assert result.ok
    assert remembered == ["Заказ 8891 ул. Садовая 12"]


def test_remember_capture_speaks_back_what_it_stored(built, grabbed, remembered, monkeypatch):
    """OCR ошибается на мелком шрифте, и услышать это надо сразу, а не через
    неделю в списке фактов."""
    monkeypatch.setattr(
        connector_action.factory, "build", lambda name, config, **kw: FakeConnector(_ocr(["код 4471"]))
    )
    assert "код 4471" in _run("remember_capture", "screen").message


def test_remember_capture_uses_ocr_not_scene_description(built, grabbed, remembered):
    """Запоминают с экрана номер, адрес, код — то есть текст. Английское
    «a screenshot of a computer» вечным фактом быть не просит."""
    _run("remember_capture", "screen")
    assert built["azure-vision"].calls[0]["features"] == connector_action.READ_FEATURES


def test_remember_capture_without_text_stores_nothing(built, grabbed, remembered, monkeypatch):
    monkeypatch.setattr(
        connector_action.factory, "build", lambda name, config, **kw: FakeConnector(_ocr([]))
    )
    result = _run("remember_capture", "screen")

    assert not result.ok
    assert remembered == []


def test_remember_capture_refusal_stores_nothing(built, grabbed, remembered, monkeypatch):
    """Нет согласия или ключа — в память не должно попасть «нет ключа»."""
    monkeypatch.setattr(
        connector_action.factory,
        "build",
        lambda name, config, **kw: FakeConnector(ConnectorResult(False, "нет ключа", reason="unavailable")),
    )
    assert not _run("remember_capture", "screen").ok
    assert remembered == []


def test_remember_capture_deletes_the_screenshot(built, grabbed, remembered):
    """Текст ушёл в память сознательно, сам снимок — нет."""
    _run("remember_capture", "screen")
    assert grabbed["discarded"] == [grabbed["screen"]]


def test_remember_phrase_does_not_fall_into_the_wildcard_rule():
    """«запомни *» стоит после и перехватила бы фразу первой — в факт уехало бы
    буквально «что на экране» вместо того, что там написано."""
    from johnny.config import load_config
    from johnny.router import route

    commands = load_config("config").commands
    routed = route("запомни что на экране", commands)
    assert (routed.action, routed.argument) == ("remember_capture", "screen")
    # обычное «запомни …» при этом должно остаться прежним
    assert route("запомни мой день рождения 5 мая", commands).action == "remember"
