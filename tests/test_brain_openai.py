"""Провайдер gpt-5.6-sol по API-ключу.

Ключевое отличие формы от Groq — `max_completion_tokens` вместо `max_tokens`:
у рассуждающих моделей потолок считает размышление вместе с видимым ответом.
"""

import johnny.brain_openai as brain_openai


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {"choices": [{"message": {"content": "Токио, сэр."}}]}
        self.text = text

    def json(self):
        return self._payload


def _clean(monkeypatch):
    monkeypatch.setattr(brain_openai, "_rejected", set())
    monkeypatch.setattr(brain_openai, "in_cooldown", lambda key: False)


def test_provider_sends_key_model_and_prompt(monkeypatch):
    _clean(monkeypatch)
    seen = {}

    def fake_post(url, headers, payload, timeout):
        seen.update(url=url, headers=headers, payload=payload, timeout=timeout)
        return FakeResponse()

    monkeypatch.setattr(brain_openai, "post", fake_post)
    answer = brain_openai.make_provider("ключ")("столица японии?")

    assert answer == "Токио, сэр."
    assert seen["url"] == "https://api.openai.com/v1/chat/completions"
    assert seen["headers"]["Authorization"] == "Bearer ключ"
    assert seen["payload"]["model"] == "gpt-5.6-sol"
    assert seen["payload"]["messages"] == [{"role": "user", "content": "столица японии?"}]
    assert seen["timeout"] == 20.0


def test_uses_max_completion_tokens_not_max_tokens(monkeypatch):
    """У рассуждающих моделей параметр переназван; max_tokens тут не тот ключ."""
    _clean(monkeypatch)
    seen = {}
    monkeypatch.setattr(
        brain_openai, "post", lambda u, h, p, t: seen.update(p) or FakeResponse()
    )
    brain_openai.make_provider("ключ")("вопрос")

    assert "max_tokens" not in seen
    assert seen["max_completion_tokens"] >= 2048
    assert seen["reasoning_effort"] == "low"
    assert "temperature" not in seen


def test_http_error_returns_empty_string_and_marks_cooldown(monkeypatch):
    _clean(monkeypatch)
    marked = []
    monkeypatch.setattr(brain_openai, "mark_failure", lambda key: marked.append(key))
    monkeypatch.setattr(
        brain_openai, "post", lambda *a: FakeResponse(status_code=500, text="oops")
    )
    assert brain_openai.make_provider("ключ")("вопрос") == ""
    assert marked == ["gpt"]


def test_bad_key_disables_provider_without_cooldown(monkeypatch):
    """401 сам не пройдёт — cooldown вернул бы нас к тому же отказу через минуту."""
    _clean(monkeypatch)
    marked = []
    calls = []
    monkeypatch.setattr(brain_openai, "mark_failure", lambda key: marked.append(key))
    monkeypatch.setattr(
        brain_openai,
        "post",
        lambda *a: calls.append(1) or FakeResponse(status_code=401, text="bad key"),
    )
    provider = brain_openai.make_provider("ключ")

    assert provider("вопрос") == ""
    assert marked == []
    assert provider("вопрос") == ""
    assert len(calls) == 1        # второй раз в сеть не пошли


def test_network_exception_returns_empty_string(monkeypatch):
    _clean(monkeypatch)

    def boom(*args):
        raise RuntimeError("нет сети")

    monkeypatch.setattr(brain_openai, "post", boom)
    assert brain_openai.make_provider("ключ")("вопрос") == ""


def test_empty_content_returns_empty_string(monkeypatch):
    """Пустой content при HTTP 200 — весь бюджет ушёл на размышление."""
    _clean(monkeypatch)
    monkeypatch.setattr(
        brain_openai,
        "post",
        lambda *a: FakeResponse(payload={"choices": [{"message": {"content": "  "}}]}),
    )
    assert brain_openai.make_provider("ключ")("вопрос") == ""


def test_broken_json_returns_empty_string(monkeypatch):
    _clean(monkeypatch)
    monkeypatch.setattr(brain_openai, "post", lambda *a: FakeResponse(payload={"error": "oops"}))
    assert brain_openai.make_provider("ключ")("вопрос") == ""


def test_cooldown_skips_network(monkeypatch):
    _clean(monkeypatch)
    called = []
    monkeypatch.setattr(brain_openai, "post", lambda *a: called.append(1))
    monkeypatch.setattr(brain_openai, "in_cooldown", lambda key: True)
    assert brain_openai.make_provider("ключ")("вопрос") == ""
    assert called == []


# --- адрес посредника ---------------------------------------------------
# Здесь, в отличие от Opus 5, хвост /v1/chat/completions дописывается руками:
# у SDK Anthropic это делает сам клиент, а тут голый POST. Посредники дают
# КОРЕНЬ, но их документация показывает и полный путь, и /v1 — принимать надо
# все три формы, иначе адрес молча превратится в .../v1/chat/completions/v1/...
# и вместо ответа придёт 404, который мы примем за «ключ не тот».


import pytest


@pytest.mark.parametrize(
    "base_url, expected",
    [
        ("", "https://api.openai.com/v1/chat/completions"),
        ("https://agentrouter.org", "https://agentrouter.org/v1/chat/completions"),
        ("https://agentrouter.org/", "https://agentrouter.org/v1/chat/completions"),
        ("  https://agentrouter.org  ", "https://agentrouter.org/v1/chat/completions"),
        ("https://agentrouter.org/v1", "https://agentrouter.org/v1/chat/completions"),
        ("https://agentrouter.org/v1/", "https://agentrouter.org/v1/chat/completions"),
        (
            "https://agentrouter.org/v1/chat/completions",
            "https://agentrouter.org/v1/chat/completions",
        ),
    ],
)
def test_endpoint_is_built_without_doubling_the_tail(base_url, expected):
    assert brain_openai._endpoint(base_url) == expected


def test_address_reaches_the_request(monkeypatch):
    _clean(monkeypatch)
    seen = {}

    def fake_post(url, headers, payload, timeout):
        seen["url"] = url
        return FakeResponse()

    monkeypatch.setattr(brain_openai, "post", fake_post)
    brain_openai.make_provider("ключ", "gpt-5.6-sol", "https://agentrouter.org")("вопрос")
    assert seen["url"] == "https://agentrouter.org/v1/chat/completions"


def test_rejection_remembers_the_address_too(monkeypatch):
    """Ключ посредника на официальном адресе отвечает 401, а на своём работает.

    Запомнив один ключ, мы выключили бы и рабочую пару — то есть сильную
    модель целиком, до перезапуска.
    """
    _clean(monkeypatch)
    monkeypatch.setattr(brain_openai, "post", lambda *a: FakeResponse(status_code=401))
    assert brain_openai.make_provider("ключ")("вопрос") == ""

    assert ("ключ", "") in brain_openai._rejected
    assert ("ключ", "https://agentrouter.org") not in brain_openai._rejected
