"""Провайдер claude-opus-5 по API-ключу.

Отдельного внимания стоят два теста про форму запроса: `temperature` и старая
`max_tokens: 200` из brain_groq на Opus 5 отвечают 400 и обрывом ответа
соответственно, и копипаста оттуда — самый вероятный способ сломать модуль.
"""

import johnny.brain_anthropic as brain_anthropic


class FakeBlock:
    def __init__(self, kind="text", text=""):
        self.type = kind
        self.text = text


class FakeMessage:
    def __init__(self, content=None, stop_reason="end_turn"):
        self.content = content if content is not None else [FakeBlock(text="Токио, сэр.")]
        self.stop_reason = stop_reason


class FakeMessages:
    def __init__(self, result):
        self._result = result
        self.seen = {}

    def create(self, **kwargs):
        self.seen.update(kwargs)
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class FakeClient:
    def __init__(self, result=None):
        self.messages = FakeMessages(result if result is not None else FakeMessage())


class HTTPError(Exception):
    def __init__(self, status_code):
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


def _with_client(monkeypatch, client, asked=None):
    """Подменяет клиента SDK. asked — список, куда падают пары (ключ, адрес),
    с которыми провайдер его запрашивал: адрес доезжает до клиента, и проверять
    это надо, иначе подмена скрыла бы потерю адреса по дороге."""

    def fake_client(api_key, base_url=""):
        if asked is not None:
            asked.append((api_key, base_url))
        return client

    monkeypatch.setattr(brain_anthropic, "_client", fake_client)
    monkeypatch.setattr(brain_anthropic, "_rejected", set())
    monkeypatch.setattr(brain_anthropic, "in_cooldown", lambda key: False)
    return client


def test_provider_sends_model_and_prompt(monkeypatch):
    client = _with_client(monkeypatch, FakeClient())
    answer = brain_anthropic.make_provider("ключ")("столица японии?")

    assert answer == "Токио, сэр."
    assert client.messages.seen["model"] == "claude-opus-5"
    assert client.messages.seen["messages"] == [{"role": "user", "content": "столица японии?"}]


def test_temperature_is_never_sent(monkeypatch):
    """На Opus 5 temperature/top_p/top_k отвечают 400 — параметра быть не должно."""
    client = _with_client(monkeypatch, FakeClient())
    brain_anthropic.make_provider("ключ")("вопрос")
    assert "temperature" not in client.messages.seen
    assert "top_p" not in client.messages.seen
    assert "top_k" not in client.messages.seen


def test_thinking_is_not_sent_and_effort_is(monkeypatch):
    """thinking с budget_tokens отвечает 400; рычаг цены — effort в output_config."""
    client = _with_client(monkeypatch, FakeClient())
    brain_anthropic.make_provider("ключ")("вопрос")
    assert "thinking" not in client.messages.seen
    assert client.messages.seen["output_config"] == {"effort": "low"}


def test_max_tokens_leaves_room_for_thinking(monkeypatch):
    """max_tokens считает размышление и ответ вместе — 200 как у Groq мало."""
    client = _with_client(monkeypatch, FakeClient())
    brain_anthropic.make_provider("ключ")("вопрос")
    assert client.messages.seen["max_tokens"] >= 2048


def test_thinking_blocks_are_not_spoken(monkeypatch):
    """Размышление приходит отдельным блоком и вслух читаться не должно."""
    message = FakeMessage(
        content=[FakeBlock("thinking", "сначала подумаю"), FakeBlock("text", "Ответ.")]
    )
    _with_client(monkeypatch, FakeClient(message))
    assert brain_anthropic.make_provider("ключ")("вопрос") == "Ответ."


def test_refusal_returns_empty_string(monkeypatch):
    """Отказ приходит с HTTP 200 и пустым content — это не исключение SDK."""
    _with_client(monkeypatch, FakeClient(FakeMessage(content=[], stop_reason="refusal")))
    assert brain_anthropic.make_provider("ключ")("вопрос") == ""


def test_empty_content_returns_empty_string(monkeypatch):
    _with_client(monkeypatch, FakeClient(FakeMessage(content=[])))
    assert brain_anthropic.make_provider("ключ")("вопрос") == ""


def test_network_error_returns_empty_string_and_marks_cooldown(monkeypatch):
    marked = []
    monkeypatch.setattr(brain_anthropic, "mark_failure", lambda key: marked.append(key))
    _with_client(monkeypatch, FakeClient(RuntimeError("нет сети")))
    assert brain_anthropic.make_provider("ключ")("вопрос") == ""
    assert marked == ["opus"]


def test_bad_key_disables_provider_without_cooldown(monkeypatch):
    """401 сам не пройдёт: нельзя раз в минуту тратить 20 секунд на тот же отказ."""
    marked = []
    monkeypatch.setattr(brain_anthropic, "mark_failure", lambda key: marked.append(key))
    client = _with_client(monkeypatch, FakeClient(HTTPError(401)))
    provider = brain_anthropic.make_provider("ключ")

    assert provider("вопрос") == ""
    assert marked == []                       # это не сетевой сбой, cooldown ни при чём
    assert ("ключ", "") in brain_anthropic._rejected

    client.messages._result = FakeMessage()   # даже если сервис «починился»
    assert provider("вопрос") == ""           # до перезапуска больше не ходим


def test_rejection_remembers_the_address_too(monkeypatch):
    """Один ключ на двух адресах — две разные судьбы.

    Ключ посредника на api.anthropic.com отвечает 401, а на своём адресе
    работает. Запоминай мы один ключ — первый же отказ выключил бы и рабочую
    пару, то есть сильную модель целиком, до перезапуска.
    """
    monkeypatch.setattr(brain_anthropic, "mark_failure", lambda key: None)
    _with_client(monkeypatch, FakeClient(HTTPError(401)))
    assert brain_anthropic.make_provider("ключ")("вопрос") == ""

    assert ("ключ", "") in brain_anthropic._rejected
    assert ("ключ", "https://agentrouter.org") not in brain_anthropic._rejected


def test_cooldown_skips_the_call(monkeypatch):
    client = _with_client(monkeypatch, FakeClient())
    monkeypatch.setattr(brain_anthropic, "in_cooldown", lambda key: True)
    assert brain_anthropic.make_provider("ключ")("вопрос") == ""
    assert client.messages.seen == {}


def test_missing_package_returns_empty_string(monkeypatch):
    """Пакет anthropic необязательный: без него Джони работает, просто без Opus 5."""
    monkeypatch.setattr(brain_anthropic, "_client", lambda api_key, base_url="": None)
    monkeypatch.setattr(brain_anthropic, "_rejected", set())
    assert brain_anthropic.make_provider("ключ")("вопрос") == ""


def test_address_reaches_the_client(monkeypatch):
    """Адрес посредника обязан доехать до SDK.

    Потеряйся он по дороге — запрос уйдёт на api.anthropic.com, где ключ
    посредника неизвестен, и Джони объявит «нет доступа» вместо ответа.
    """
    asked = []
    _with_client(monkeypatch, FakeClient(), asked)
    brain_anthropic.make_provider("ключ", "claude-opus-5", "https://agentrouter.org")("вопрос")
    assert asked == [("ключ", "https://agentrouter.org")]


def test_no_address_stays_official(monkeypatch):
    """Пусто = официальный адрес: это прежнее поведение, и ломать его нельзя."""
    asked = []
    _with_client(monkeypatch, FakeClient(), asked)
    brain_anthropic.make_provider("ключ")("вопрос")
    assert asked == [("ключ", "")]


def test_client_is_reused_across_calls(monkeypatch):
    """make_providers() зовётся на каждую фразу — клиент обязан пережить вызов."""
    built = []
    monkeypatch.setattr(brain_anthropic, "_clients", {})

    class FakeAnthropic:
        def __init__(self, **kwargs):
            built.append(kwargs)
            self.messages = FakeMessages(FakeMessage())

    import sys
    import types

    module = types.ModuleType("anthropic")
    module.Anthropic = FakeAnthropic
    monkeypatch.setitem(sys.modules, "anthropic", module)

    assert brain_anthropic._client("ключ") is brain_anthropic._client("ключ")
    assert len(built) == 1
    assert built[0]["timeout"] == 20.0
    assert built[0]["max_retries"] == 0
    # Пустой адрес не передаём вовсе: base_url="" — это запрос к пустому хосту,
    # а не «адрес по умолчанию».
    assert "base_url" not in built[0]

    # Тот же ключ на другом адресе — другой клиент: адрес зашит внутрь клиента,
    # и переиспользуй мы прежний, запрос ушёл бы не туда.
    other = brain_anthropic._client("ключ", "https://agentrouter.org")
    assert other is not brain_anthropic._client("ключ")
    assert other is brain_anthropic._client("ключ", "https://agentrouter.org")
    assert built[1]["base_url"] == "https://agentrouter.org"
    assert len(built) == 2


# --- медленно ≠ сломано -------------------------------------------------
# Замер 2026-08-08 через посредника: первый вызов после паузы берёт 29–39 с,
# следующий 2–7 с. Уходя в cooldown после первого же таймаута, Джони замолкал
# бы на минуту ровно тогда, когда следующий вопрос был бы отвечен быстро.


class FakeTimeout(Exception):
    """Имя класса важнее типа: _is_timeout смотрит на него, чтобы не тащить
    сюда импорт пакета anthropic."""


FakeTimeout.__name__ = "APITimeoutError"


def _count_failures(monkeypatch):
    marked = []
    monkeypatch.setattr(brain_anthropic, "mark_failure", lambda key: marked.append(key))
    monkeypatch.setattr(brain_anthropic, "_timeouts", {})
    return marked


def test_single_timeout_does_not_silence_the_provider(monkeypatch):
    """Один медленный ответ не должен стоить минуты молчания."""
    marked = _count_failures(monkeypatch)
    _with_client(monkeypatch, FakeClient(FakeTimeout("не успел")))
    assert brain_anthropic.make_provider("ключ")("вопрос") == ""
    assert marked == []


def test_second_timeout_in_a_row_is_treated_as_a_failure(monkeypatch):
    """Прощать таймауты бесконечно — вернуться к трате 20 секунд на каждую фразу."""
    marked = _count_failures(monkeypatch)
    _with_client(monkeypatch, FakeClient(FakeTimeout("не успел")))
    provider = brain_anthropic.make_provider("ключ")

    assert provider("вопрос") == ""
    assert provider("другой вопрос") == ""
    assert marked == ["opus"]


def test_success_resets_the_timeout_count(monkeypatch):
    """Прогрелся — счётчик обнулён, иначе редкие таймауты за день сложились бы."""
    marked = _count_failures(monkeypatch)
    client = _with_client(monkeypatch, FakeClient(FakeTimeout("не успел")))
    provider = brain_anthropic.make_provider("ключ")

    assert provider("вопрос") == ""
    client.messages._result = FakeMessage()
    assert provider("вопрос") == "Токио, сэр."

    client.messages._result = FakeTimeout("снова не успел")
    assert provider("вопрос") == ""
    assert marked == []                       # это опять ПЕРВЫЙ подряд


def test_broken_connection_still_goes_to_cooldown_at_once(monkeypatch):
    """Оборванная связь — не «медленно»: ждать её по 20 секунд смысла нет."""
    marked = _count_failures(monkeypatch)
    _with_client(monkeypatch, FakeClient(RuntimeError("нет сети")))
    assert brain_anthropic.make_provider("ключ")("вопрос") == ""
    assert marked == ["opus"]


def test_timeout_count_is_per_address(monkeypatch):
    """Медленный посредник не должен глушить официальный адрес, и наоборот."""
    _count_failures(monkeypatch)
    _with_client(monkeypatch, FakeClient(FakeTimeout("не успел")))
    brain_anthropic.make_provider("ключ", "claude-opus-5", "https://agentrouter.org")("вопрос")

    assert brain_anthropic._timeouts == {("ключ", "https://agentrouter.org"): 1}
