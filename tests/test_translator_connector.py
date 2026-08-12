"""Перевод: локальный движок вперёд сети, разбор языка, отказ без движка.

Главное здесь — коннектор не уходит в сеть, когда справился локально, и не
уходит в неё вообще, пока адрес не вписан руками. Продиктованный текст не
должен уезжать на чужой сервер молча.
"""

import pytest

import johnny.connectors.base as base
import johnny.connectors.translator as translator
import johnny.http_client as http_client
from johnny.connectors.translator import TranslatorConnector


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """Те же две протечки, что и в test_connector_base: cooldown в модульном
    словаре и кеш, уходящий в реальный models/connector-cache."""
    monkeypatch.setattr(http_client, "_last_failure", {})
    monkeypatch.setattr(base, "DEFAULT_CACHE_DIR", tmp_path)


def _fresh(tmp_path, **kwargs):
    return TranslatorConnector(
        cache_dir=tmp_path, state_path=tmp_path / "state.json", **kwargs
    )


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {"translatedText": "hello"}

    def json(self):
        return self._payload


@pytest.fixture
def local(monkeypatch):
    """Локальный движок, который переводит всё в «hello» и считает вызовы.

    _argos подменяется вместе с ним обязательно: available() спрашивает именно
    его, и без подмены вызов отказывался бы ещё до перевода — а тесты про
    отказы «пустой текст», «тот же язык» проходили бы по неверной причине.
    """
    calls = []

    def fake(text, source, target):
        calls.append((text, source, target))
        return "hello"

    monkeypatch.setattr(translator, "_argos", lambda: object())
    monkeypatch.setattr(translator, "_translate_locally", fake)
    return calls


@pytest.fixture
def no_local(monkeypatch):
    monkeypatch.setattr(translator, "_translate_locally", lambda *a: None)
    monkeypatch.setattr(translator, "_argos", lambda: None)


# --- Готовность --------------------------------------------------------------


def test_without_engine_and_address_it_says_what_to_install(tmp_path, no_local):
    ready, why = _fresh(tmp_path).available()
    assert not ready
    assert "argostranslate" in why and "pip install" in why


def test_address_alone_is_enough(tmp_path, no_local):
    ready, _ = _fresh(tmp_path, endpoint="http://localhost:5000/translate").available()
    assert ready


def test_package_alone_is_enough(tmp_path, monkeypatch):
    monkeypatch.setattr(translator, "_argos", lambda: object())
    assert _fresh(tmp_path).available()[0]


def test_no_consent_needed(tmp_path):
    """Свой текст своему движку — не персональные данные третьих лиц."""
    assert _fresh(tmp_path).requires_consent is False


# --- Локально вперёд сети ----------------------------------------------------


def test_local_translation_never_touches_the_network(tmp_path, local, monkeypatch):
    def explode(*args, **kwargs):
        raise AssertionError("ушли в сеть, хотя перевели локально")

    monkeypatch.setattr(translator, "post", explode)
    result = _fresh(tmp_path, endpoint="http://example/translate").call(
        text="привет", target="en"
    )
    assert result.ok and result.message == "hello"
    assert local == [("привет", "ru", "en")]


def test_without_address_missing_pair_is_a_refusal_not_a_request(tmp_path, no_local, monkeypatch):
    """Пакет есть, пары нет, адреса нет — в сеть идти некуда, и человеку надо
    сказать, что именно доустановить."""
    monkeypatch.setattr(translator, "_argos", lambda: object())
    result = _fresh(tmp_path).call(text="привет", target="ja")
    assert not result.ok


def test_remote_used_only_when_local_cannot(tmp_path, no_local, monkeypatch):
    sent = []

    def fake_post(url, headers, payload, timeout):
        sent.append((url, payload))
        return FakeResponse()

    monkeypatch.setattr(translator, "post", fake_post)
    result = _fresh(tmp_path, endpoint="http://example/translate").call(
        text="привет", target="en"
    )
    assert result.ok and result.message == "hello"
    assert sent[0][1] == {"q": "привет", "source": "ru", "target": "en", "format": "text"}


def test_engine_is_recorded_in_data(tmp_path, local):
    result = _fresh(tmp_path).call(text="привет", target="en")
    assert result.data["engine"] == "local"


# --- Отказы ------------------------------------------------------------------


def test_http_error_does_not_leak_the_key(tmp_path, no_local, monkeypatch):
    """LibreTranslate возвращает в теле ошибки исходный запрос вместе с
    api_key — тело в текст ошибки попадать не должно."""
    monkeypatch.setattr(translator, "post", lambda *a, **kw: FakeResponse(403, {"q": "СЕКРЕТ"}))
    result = _fresh(
        tmp_path, endpoint="http://example/translate", api_key="СЕКРЕТ"
    ).call(text="привет", target="en")
    assert not result.ok
    assert "СЕКРЕТ" not in result.message and "СЕКРЕТ" not in str(result.data)


def test_empty_answer_is_a_refusal(tmp_path, no_local, monkeypatch):
    monkeypatch.setattr(translator, "post", lambda *a, **kw: FakeResponse(200, {}))
    assert not _fresh(tmp_path, endpoint="http://e/t").call(text="привет", target="en").ok


def test_same_language_is_refused_without_spending_anything(tmp_path, local):
    result = _fresh(tmp_path).call(text="привет", target="ru")
    assert not result.ok and local == []


def test_too_long_text_is_refused(tmp_path, local):
    result = _fresh(tmp_path).call(text="а" * (translator.MAX_CHARS + 1), target="en")
    assert not result.ok and local == []


def test_empty_text_is_refused(tmp_path, local):
    assert not _fresh(tmp_path).call(text="   ", target="en").ok


def test_missing_target_is_refused(tmp_path, local):
    assert not _fresh(tmp_path).call(text="привет", target="").ok


# --- Кеш ---------------------------------------------------------------------


def test_repeated_phrase_is_not_translated_twice(tmp_path, local):
    connector = _fresh(tmp_path)
    connector.call(text="привет", target="en")
    second = connector.call(text="привет", target="en")
    assert second.cached and len(local) == 1


def test_case_and_spacing_hit_the_same_cache_entry(tmp_path, local):
    connector = _fresh(tmp_path)
    connector.call(text="привет", target="en")
    assert connector.call(text="  ПРИВЕТ ", target="en").cached
    assert len(local) == 1


def test_other_target_language_is_a_new_call(tmp_path, local):
    connector = _fresh(tmp_path)
    connector.call(text="привет", target="en")
    connector.call(text="привет", target="de")
    assert len(local) == 2


def test_describe_speaks_only_the_translation(tmp_path):
    """Ни «перевожу с русского», ни имени движка: просили перевод."""
    assert _fresh(tmp_path).describe({"text": "hello", "engine": "remote"}) == "hello"


# --- Языки -------------------------------------------------------------------


@pytest.mark.parametrize(
    "said, code",
    [
        ("английский", "en"),
        ("английском", "en"),
        ("англ", "en"),
        ("en", "en"),
        ("немецкий", "de"),
        ("украинский", "uk"),
        ("АНГЛИЙСКИЙ", "en"),
        ("карту", ""),
        ("", ""),
    ],
)
def test_language_code_understands_spoken_forms(said, code):
    """Whisper пишет то «на английский», то «на английском» — обе формы обязаны
    дать один код, поэтому сверка идёт по основе слова."""
    assert translator.language_code(said) == code


def test_every_stem_maps_to_a_speakable_name():
    """Код без произносимого названия однажды прозвучит как «перевёл на en»."""
    assert set(translator.LANGUAGE_STEMS.values()) <= set(translator.LANGUAGE_NAMES)


@pytest.mark.parametrize(
    "text, expected", [("привет", "ru"), ("hello", "en"), ("ёлка", "ru"), ("123", "en")]
)
def test_detect_language_splits_cyrillic_from_the_rest(text, expected):
    assert translator.detect_language(text) == expected


def test_opposite_flips_the_pair():
    assert translator.opposite("ru") == "en" and translator.opposite("en") == "ru"
