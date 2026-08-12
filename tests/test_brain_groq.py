import pytest
import johnny.brain_groq as brain_groq


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {"choices": [{"message": {"content": "Токио, сэр."}}]}
        self.text = text

    def json(self):
        return self._payload


def test_provider_sends_key_model_and_prompt(monkeypatch):
    seen = {}

    def fake_post(url, headers, payload, timeout):
        seen.update(url=url, headers=headers, payload=payload, timeout=timeout)
        return FakeResponse()

    monkeypatch.setattr(brain_groq, "post", fake_post)
    answer = brain_groq.make_provider("ключ", "llama-3.3-70b-versatile")("столица японии?")

    assert answer == "Токио, сэр."
    assert seen["url"] == "https://api.groq.com/openai/v1/chat/completions"
    assert seen["headers"]["Authorization"] == "Bearer ключ"
    assert seen["payload"]["model"] == "llama-3.3-70b-versatile"
    assert seen["payload"]["messages"] == [{"role": "user", "content": "столица японии?"}]
    assert seen["payload"]["max_tokens"] == 200
    assert seen["timeout"] == 10.0


def test_http_error_returns_empty_string(monkeypatch):
    monkeypatch.setattr(
        brain_groq, "post", lambda *a: FakeResponse(status_code=429, text="rate limit")
    )
    assert brain_groq.make_provider("ключ", "модель")("что-то") == ""


def test_network_exception_returns_empty_string(monkeypatch):
    def boom(*args):
        raise RuntimeError("нет сети")

    monkeypatch.setattr(brain_groq, "post", boom)
    assert brain_groq.make_provider("ключ", "модель")("что-то") == ""


def test_broken_json_returns_empty_string(monkeypatch):
    monkeypatch.setattr(brain_groq, "post", lambda *a: FakeResponse(payload={"error": "oops"}))
    assert brain_groq.make_provider("ключ", "модель")("что-то") == ""


def test_null_content_returns_empty_string(monkeypatch):
    """content=null в валидном JSON-ответе — это ошибка ответа, не исключение."""
    monkeypatch.setattr(
        brain_groq, "post",
        lambda *a: FakeResponse(payload={"choices": [{"message": {"content": None}}]})
    )
    assert brain_groq.make_provider("ключ", "модель")("что-то") == ""


def test_empty_choices_returns_empty_string(monkeypatch):
    """Пустой массив choices — тоже ошибка."""
    monkeypatch.setattr(
        brain_groq, "post",
        lambda *a: FakeResponse(payload={"choices": []})
    )
    assert brain_groq.make_provider("ключ", "модель")("что-то") == ""


def test_cooldown_skips_network_and_returns_empty_string(monkeypatch):
    called = []
    monkeypatch.setattr(brain_groq, "post", lambda *a: called.append(1))
    monkeypatch.setattr(brain_groq, "in_cooldown", lambda key: True)
    assert brain_groq.make_provider("ключ", "модель")("что-то") == ""
    assert called == []          # в сеть даже не сходили


def test_http_error_marks_failure_for_cooldown(monkeypatch):
    marked = []
    monkeypatch.setattr(brain_groq, "mark_failure", lambda key: marked.append(key))
    monkeypatch.setattr(brain_groq, "post", lambda *a: FakeResponse(status_code=500, text="oops"))
    brain_groq.make_provider("ключ", "модель")("что-то")
    assert marked == ["groq"]


def test_network_exception_marks_failure_for_cooldown(monkeypatch):
    marked = []
    monkeypatch.setattr(brain_groq, "mark_failure", lambda key: marked.append(key))

    def boom(*args):
        raise RuntimeError("нет сети")

    monkeypatch.setattr(brain_groq, "post", boom)
    brain_groq.make_provider("ключ", "модель")("что-то")
    assert marked == ["groq"]


class FakeStream:
    """Подставной ответ requests со stream=True."""

    def __init__(self, lines, status_code=200, text=""):
        self._lines = lines
        self.status_code = status_code
        self.text = text

    def iter_lines(self, decode_unicode=False):
        for line in self._lines:
            yield line


def _sse(*pieces):
    """Строки SSE в формате OpenAI, как их шлёт Groq."""
    out = []
    for piece in pieces:
        out.append('data: {"choices":[{"delta":{"content":"%s"}}]}' % piece)
    out.append("data: [DONE]")
    return out


def test_streaming_yields_pieces_as_they_arrive(monkeypatch):
    monkeypatch.setattr(
        brain_groq, "post_stream", lambda *a, **k: FakeStream(_sse("При", "вет"))
    )
    provider = brain_groq.make_streaming_provider("k", "m")
    assert list(provider("вопрос")) == ["При", "вет"]


def test_streaming_asks_groq_for_a_stream(monkeypatch):
    """Без "stream": true в теле Groq ответит обычным JSON, и iter_lines отдаст
    его одной строкой — разбор молча вернёт пустоту."""
    seen = {}

    def fake(url, headers, payload, timeout):
        seen.update(payload)
        return FakeStream(_sse("да"))

    monkeypatch.setattr(brain_groq, "post_stream", fake)
    list(brain_groq.make_streaming_provider("k", "m")("вопрос"))
    assert seen["stream"] is True


def test_streaming_stops_on_done_marker(monkeypatch):
    """После [DONE] Groq может держать соединение — не остановившись, Джони
    молчал бы до сетевого таймаута уже ПОСЛЕ готового ответа."""
    lines = _sse("готово") + ['data: {"choices":[{"delta":{"content":"лишнее"}}]}']
    monkeypatch.setattr(brain_groq, "post_stream", lambda *a, **k: FakeStream(lines))
    assert list(brain_groq.make_streaming_provider("k", "m")("в")) == ["готово"]


def test_streaming_skips_keepalive_and_broken_lines(monkeypatch):
    """SSE легально содержит пустые строки и комментарии, а последний кусок
    приходит обрезанным при разрыве. Падать на них нельзя — уже озвученное
    останется, а остаток дочитаем."""
    lines = ["", ": keep-alive", 'data: {"choices":[{"delta":{}}]}',
             "data: {битый", 'data: {"choices":[{"delta":{"content":"ок"}}]}',
             "data: [DONE]"]
    monkeypatch.setattr(brain_groq, "post_stream", lambda *a, **k: FakeStream(lines))
    assert list(brain_groq.make_streaming_provider("k", "m")("в")) == ["ок"]


def test_streaming_in_cooldown_yields_nothing(monkeypatch):
    """Тот же щит, что у make_provider: не ждать сетевой таймаут на каждую
    фразу, когда интернет только что пропал."""
    monkeypatch.setattr(brain_groq, "in_cooldown", lambda key: True)
    called = []
    monkeypatch.setattr(brain_groq, "post_stream", lambda *a, **k: called.append(1))
    assert list(brain_groq.make_streaming_provider("k", "m")("в")) == []
    assert called == []


def test_streaming_raises_stream_broken_on_http_error(monkeypatch):
    """Обрыв обязан отличаться от нормального конца потока: после нормального
    конца Джони молчит, после обрыва — говорит, что связь пропала."""
    monkeypatch.setattr(
        brain_groq, "post_stream",
        lambda *a, **k: FakeStream([], status_code=429, text="rate limit"),
    )
    with pytest.raises(brain_groq.StreamBroken):
        list(brain_groq.make_streaming_provider("k", "m")("в"))


def test_streaming_failure_starts_cooldown(monkeypatch):
    marked = []
    monkeypatch.setattr(brain_groq, "mark_failure", lambda key: marked.append(key))
    monkeypatch.setattr(brain_groq, "in_cooldown", lambda key: False)

    def boom(*a, **k):
        raise OSError("сеть пропала")

    monkeypatch.setattr(brain_groq, "post_stream", boom)
    with pytest.raises(brain_groq.StreamBroken):
        list(brain_groq.make_streaming_provider("k", "m")("в"))
    assert marked == ["groq"]
