"""Базовый коннектор: согласие, кеш, готовность, cooldown, квота, офлайн-отказ.

Порядок проверок в Connector.call() — контракт: согласие ДО кеша, кеш ДО
готовности, готовность ДО cooldown, cooldown ДО квоты, квота ДО вызова.
Каждый тест ниже фиксирует одну ступень этого порядка.
"""

import pytest

import johnny.connectors.base as base
import johnny.connectors.store as store
from johnny import http_client
from johnny.http_client import in_cooldown, mark_failure


class FakeConnector(base.Connector):
    """Наследник, который умеет «отказать» на любом этапе и посчитать вызовы."""

    name = "fake-svc"
    requires_consent = False
    daily_quota = 0
    min_interval_seconds = 0.0
    calls = 0
    available_result = (True, "")

    def available(self):
        return self.available_result

    def _run(self, **params):
        self.calls += 1
        return {"echo": params}

    def describe(self, data):
        return f"Echo: {data['echo'].get('q')}"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """Два источника протечек между тестами, оба уже сработали:

    1. cooldown живёт в модульном словаре http_client — отказ, отмеченный
       одним тестом, глушил вызов в следующем;
    2. DEFAULT_CACHE_DIR — реальный models/connector-cache в проекте. Тест,
       забывший передать пути, писал кеш и квоту туда, и следующий тест
       получал «ответ из кеша» вместо живого вызова. Подменяем и default:
       забывчивость должна попадать в tmp_path, а не в рабочий каталог.
    """
    monkeypatch.setattr(http_client, "_last_failure", {})
    monkeypatch.setattr(base, "DEFAULT_CACHE_DIR", tmp_path)


def _fresh(tmp_path, consent=False):
    """Экземпляр, у которого кеш и счётчик квоты лежат там же, куда тест их
    подкладывает: иначе тест проверяет не то, что думает."""
    return FakeConnector(
        cache_dir=tmp_path, state_path=tmp_path / "state.json", consent=consent
    )


def test_consent_blocks_before_anything(tmp_path, monkeypatch):
    monkeypatch.setattr(FakeConnector, "requires_consent", True)
    c = _fresh(tmp_path, consent=False)
    result = c.call(q="лицо")
    assert not result.ok and result.reason == "no-consent"
    # Ни вызова, ни файла: сервис не должен узнать даже о факте запроса.
    assert c.calls == 0 and list(tmp_path.glob("*")) == []


def test_consent_granted_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(FakeConnector, "requires_consent", True)
    c = _fresh(tmp_path, consent=True)
    result = c.call(q="лицо")
    assert result.ok and c.calls == 1


def test_cache_served_even_when_unavailable(tmp_path, monkeypatch):
    """Сервис лёг — а вчерашний ответ на тот же вопрос всё ещё отдаётся.
    Это и есть офлайн-режим: кеш проверяется ДО available()."""
    monkeypatch.setattr(FakeConnector, "available_result", (False, "ключ пропал"))
    c = _fresh(tmp_path)
    store.write(tmp_path, "fake-svc", {"q": "раз"}, {"echo": {"q": "раз"}})
    result = c.call(q="раз")
    assert result.ok and result.cached
    assert c.calls == 0


def test_cache_served_even_in_cooldown(tmp_path):
    """Та же ступень с другой стороны: свежий отказ сервиса не отбирает
    доступ к уже полученному ответу."""
    mark_failure("fake-svc")
    c = _fresh(tmp_path)
    store.write(tmp_path, "fake-svc", {"q": "раз"}, {"echo": {"q": "раз"}})
    assert c.call(q="раз").cached


def test_cached_answer_does_not_spend_quota(tmp_path):
    """Иначе кеш терял бы смысл: бесплатный повтор всё равно съедал бы лимит."""
    c = _fresh(tmp_path)
    store.write(tmp_path, "fake-svc", {"q": "раз"}, {"echo": {"q": "раз"}})
    c.call(q="раз")
    assert store.usage(tmp_path / "state.json", "fake-svc")[0] == 0


def test_unavailable_is_honest_refusal_not_exception(tmp_path, monkeypatch):
    """Нет ключа — отказ с причиной посреди разговора, а не трассировка."""
    monkeypatch.setattr(FakeConnector, "available_result", (False, "ключ пропал"))
    c = _fresh(tmp_path)
    result = c.call(q="раз")
    assert not result.ok and result.reason == "unavailable"
    assert "ключ пропал" in result.message
    assert c.calls == 0


def test_quota_blocks_calls(tmp_path, monkeypatch):
    monkeypatch.setattr(FakeConnector, "daily_quota", 2)
    c = _fresh(tmp_path)
    store.bump(tmp_path / "state.json", "fake-svc")
    store.bump(tmp_path / "state.json", "fake-svc")
    result = c.call(q="раз")
    assert not result.ok and result.reason == "quota"
    assert c.calls == 0


def test_cooldown_blocks_before_quota_and_call(tmp_path):
    """Сервис только что отказал: следующий вызов не должен ждать таймаут
    повторно — идём в отказ сразу, до квоты и вызова."""
    mark_failure("fake-svc")
    c = _fresh(tmp_path)
    result = c.call(q="раз")
    assert not result.ok and result.reason == "cooldown"
    assert c.calls == 0


def test_min_interval_waits_instead_of_earning_429(tmp_path, monkeypatch):
    """Сервис с интервалом ответил бы 429, а он тратит квоту и попадает под
    cooldown, глуша следующий вызов. Дешевле подождать самим."""
    slept = []
    monkeypatch.setattr(FakeConnector, "min_interval_seconds", 5.0)
    monkeypatch.setattr(base.time, "sleep", lambda s: slept.append(s))
    c = _fresh(tmp_path)
    store.bump(tmp_path / "state.json", "fake-svc")
    result = c.call(q="раз")
    assert result.ok and c.calls == 1
    assert slept and 0 < slept[0] <= 5.0


def test_failure_marks_cooldown_and_returns_refusal(tmp_path):
    calls = []

    class Failing(FakeConnector):
        def _run(self, **params):
            calls.append(1)
            raise RuntimeError("сеть упала")

    c = Failing(cache_dir=tmp_path, state_path=tmp_path / "state.json")
    result = c.call(q="раз")
    assert not result.ok and result.reason == "error"
    assert calls == [1]
    assert in_cooldown("fake-svc")


def test_failure_message_hides_internals(tmp_path):
    """Текст ошибки уходит в data для лога, а человеку — короткая фраза:
    в исключении легко оказывается URL с ключом в query-строке."""

    class Failing(FakeConnector):
        def _run(self, **params):
            raise RuntimeError("401 https://svc/api?key=СЕКРЕТ")

    c = Failing(cache_dir=tmp_path, state_path=tmp_path / "state.json")
    result = c.call(q="раз")
    assert "СЕКРЕТ" not in result.message
    assert "СЕКРЕТ" in result.data["error"]


def test_failure_counts_against_quota(tmp_path):
    """Неудачный вызов у большинства сервисов тоже списывается с квоты —
    bump в finally, иначе квота молча перерасходуется."""

    class Failing(FakeConnector):
        def _run(self, **params):
            raise RuntimeError("429")

    c = Failing(cache_dir=tmp_path, state_path=tmp_path / "state.json")
    c.call(q="раз")
    assert store.usage(tmp_path / "state.json", "fake-svc")[0] == 1


def test_failure_is_not_cached(tmp_path):
    """Иначе одна сетевая заминка запомнилась бы на сутки как «ответ»."""

    class Failing(FakeConnector):
        def _run(self, **params):
            raise RuntimeError("сеть упала")

    c = Failing(cache_dir=tmp_path, state_path=tmp_path / "state.json")
    c.call(q="раз")
    assert store.read(tmp_path, "fake-svc", {"q": "раз"}) is None


def test_success_is_cached_and_does_not_call_again(tmp_path):
    c = _fresh(tmp_path)
    first = c.call(q="раз")
    second = c.call(q="раз")
    assert first.ok and not first.cached and c.calls == 1
    assert second.ok and second.cached and c.calls == 1


def test_different_params_trigger_new_call(tmp_path):
    c = _fresh(tmp_path)
    c.call(q="раз")
    c.call(q="два")
    assert c.calls == 2


def test_result_carries_describe_message(tmp_path):
    c = _fresh(tmp_path)
    result = c.call(q="стоп")
    assert result.message == "Echo: стоп"


def test_cache_params_narrows_the_key(tmp_path):
    """Путь к файлу в ключе кеша — ловушка: screenshot.png перезаписывается,
    и ответ на вчерашнюю картинку выдавался бы за сегодняшнюю. cache_params
    позволяет ключевать содержимым, а путь в ключ не пускать."""

    class ByContent(FakeConnector):
        def cache_params(self, params):
            return {"hash": params["hash"]}

    c = ByContent(cache_dir=tmp_path, state_path=tmp_path / "state.json")
    c.call(hash="одинаковый", path="a.png")
    second = c.call(hash="одинаковый", path="совсем-другой.png")
    assert second.cached and c.calls == 1


def test_cache_params_still_passes_full_params_to_run(tmp_path):
    """Сузили ключ — но сам вызов должен получить всё: путь к файлу нужен,
    чтобы файл прочитать."""
    seen = []

    class ByContent(FakeConnector):
        def cache_params(self, params):
            return {"hash": params["hash"]}

        def _run(self, **params):
            seen.append(params)
            return {"echo": params}

    c = ByContent(cache_dir=tmp_path, state_path=tmp_path / "state.json")
    c.call(hash="h", path="a.png")
    assert seen == [{"hash": "h", "path": "a.png"}]
