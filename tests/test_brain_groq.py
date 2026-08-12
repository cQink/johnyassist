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
