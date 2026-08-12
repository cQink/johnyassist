import logging

import johnny.http_client as http_client


class FakeResponse:
    status_code = 200
    text = "ok"


def test_post_adds_browser_user_agent(monkeypatch):
    seen = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        seen.update(url=url, headers=headers, json=json, timeout=timeout)
        return FakeResponse()

    monkeypatch.setattr(http_client.requests, "post", fake_post)
    http_client.post("https://api.example/tts", {"Authorization": "Bearer k"}, {"text": "привет"}, 10.0)

    assert seen["headers"]["User-Agent"] == http_client.USER_AGENT
    assert seen["headers"]["Authorization"] == "Bearer k"   # свои заголовки не потерялись
    assert seen["json"] == {"text": "привет"}
    assert seen["timeout"] == 10.0


def test_post_returns_response(monkeypatch):
    monkeypatch.setattr(http_client.requests, "post", lambda *a, **kw: FakeResponse())
    assert http_client.post("https://api.example", {}, {}, 1.0).status_code == 200


def test_warn_once_warns_first_then_debug(monkeypatch, caplog):
    monkeypatch.setattr(http_client, "_warned", set())
    with caplog.at_level(logging.DEBUG, logger="johnny.http_client"):
        http_client.warn_once("fish", "fish недоступен")
        http_client.warn_once("fish", "fish недоступен")

    levels = [record.levelno for record in caplog.records]
    assert levels == [logging.WARNING, logging.DEBUG]


def test_warn_once_separate_keys_both_warn(monkeypatch, caplog):
    monkeypatch.setattr(http_client, "_warned", set())
    with caplog.at_level(logging.DEBUG, logger="johnny.http_client"):
        http_client.warn_once("fish", "fish недоступен")
        http_client.warn_once("groq", "groq недоступен")

    assert [record.levelno for record in caplog.records] == [logging.WARNING, logging.WARNING]


def test_in_cooldown_false_before_any_failure(monkeypatch):
    monkeypatch.setattr(http_client, "_last_failure", {})
    assert http_client.in_cooldown("fish") is False


def test_in_cooldown_true_right_after_failure(monkeypatch):
    monkeypatch.setattr(http_client, "_last_failure", {})
    monkeypatch.setattr(http_client.time, "monotonic", lambda: 100.0)
    http_client.mark_failure("fish")
    monkeypatch.setattr(http_client.time, "monotonic", lambda: 101.0)
    assert http_client.in_cooldown("fish") is True


def test_in_cooldown_false_after_60_seconds(monkeypatch):
    monkeypatch.setattr(http_client, "_last_failure", {})
    monkeypatch.setattr(http_client.time, "monotonic", lambda: 100.0)
    http_client.mark_failure("fish")
    monkeypatch.setattr(http_client.time, "monotonic", lambda: 100.0 + http_client._COOLDOWN_SECONDS)
    assert http_client.in_cooldown("fish") is False


def test_cooldown_is_tracked_per_key(monkeypatch):
    monkeypatch.setattr(http_client, "_last_failure", {})
    monkeypatch.setattr(http_client.time, "monotonic", lambda: 100.0)
    http_client.mark_failure("fish")
    assert http_client.in_cooldown("groq") is False


def test_post_stream_asks_requests_not_to_buffer(monkeypatch):
    """Без stream=True requests скачивает ответ целиком перед возвратом — то
    есть SSE-поток Groq пришёл бы одним куском в конце, и стриминга бы не было
    вовсе, причём молча: код выглядел бы рабочим."""
    seen = {}

    def fake_post(url, **kwargs):
        seen.update(kwargs)
        return "ответ"

    monkeypatch.setattr(http_client.requests, "post", fake_post)
    assert http_client.post_stream("http://x", {}, {"a": 1}, 5.0) == "ответ"
    assert seen["stream"] is True


def test_post_stream_keeps_the_browser_user_agent(monkeypatch):
    """Groq стоит за Cloudflare, который режет клиентов без User-Agent, —
    потоковому запросу заголовок нужен ровно так же, как обычному."""
    seen = {}

    def fake_post(url, **kwargs):
        seen.update(kwargs)
        return "ответ"

    monkeypatch.setattr(http_client.requests, "post", fake_post)
    http_client.post_stream("http://x", {"Authorization": "Bearer k"}, {}, 5.0)
    assert seen["headers"]["User-Agent"] == http_client.USER_AGENT
    assert seen["headers"]["Authorization"] == "Bearer k"
