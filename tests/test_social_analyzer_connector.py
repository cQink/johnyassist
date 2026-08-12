"""social-analyzer: отказ без пакета, согласие, разбор чужой схемы.

Настоящий пакет здесь не нужен и не желателен: он ходит по 900 сайтам. Модуль
подменяется через sys.modules — ровно так, как его грузит коннектор.
"""

import json
import sys

import pytest

import johnny.connectors.base as base
import johnny.connectors.social_analyzer as sa
from johnny import http_client


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(http_client, "_last_failure", {})
    monkeypatch.setattr(base, "DEFAULT_CACHE_DIR", tmp_path)
    monkeypatch.setattr(base.time, "sleep", lambda s: None)


RAW = {
    "detected": [
        {"title": "vk", "link": "https://vk.com/durov", "rate": "%75", "type": "detected"},
        {"title": "github", "link": "https://github.com/durov", "rate": "%100"},
        {"link": "https://ok.ru/durov", "rate": "%50"},
    ],
    "unknown": [{"link": "https://x.com/durov"}, {"link": "https://t.me/durov"}],
    "failed": [{"link": "https://dead.site"}],
}


class FakeAnalyzer:
    def __init__(self, raw=RAW):
        self._raw = raw
        self.calls = []

    def run_as_object(self, **kwargs):
        self.calls.append(kwargs)
        return self._raw


def _install(monkeypatch, raw=RAW):
    """Подсовывает пакет под его настоящим именем с дефисом."""
    analyzer = FakeAnalyzer(raw)

    class FakeModule:
        SocialAnalyzer = staticmethod(lambda: analyzer)

    monkeypatch.setitem(sys.modules, sa.PACKAGE, FakeModule)
    return analyzer


def _connector(tmp_path, consent=True, **kwargs):
    return sa.SocialAnalyzerConnector(
        cache_dir=tmp_path,
        state_path=tmp_path / "state.json",
        log_path=tmp_path / "osint.log",
        consent=consent,
        **kwargs,
    )


def test_missing_package_says_what_to_install(tmp_path, monkeypatch):
    """«Не работает» без подсказки живёт месяцами."""
    monkeypatch.delitem(sys.modules, sa.PACKAGE, raising=False)
    monkeypatch.setattr(
        sa.importlib, "import_module", lambda name: (_ for _ in ()).throw(ImportError())
    )
    result = _connector(tmp_path).call(username="durov")
    assert not result.ok and result.reason == "unavailable"
    assert "pip install social-analyzer" in result.message


def test_without_consent_package_is_not_even_touched(tmp_path, monkeypatch):
    """Ник — не менее личное, чем лицо: сводка профилей и есть деанонимизация."""
    analyzer = _install(monkeypatch)
    result = _connector(tmp_path, consent=False).call(username="durov")
    assert not result.ok and result.reason == "no-consent"
    assert analyzer.calls == []
    assert not (tmp_path / "osint.log").exists()


def test_search_returns_sorted_profiles(tmp_path, monkeypatch):
    _install(monkeypatch)
    result = _connector(tmp_path).call(username="durov")
    assert result.ok and result.data["total"] == 3
    assert result.data["matches"][0]["site"] == "github"


def test_rate_is_a_number_not_a_percent_string(tmp_path, monkeypatch):
    """"%100" < "%9" как строки — так сортировка врёт молча."""
    _install(monkeypatch)
    rates = [m["rate"] for m in _connector(tmp_path).call(username="durov").data["matches"]]
    assert rates == [100, 75, 50]


def test_site_falls_back_to_domain(tmp_path, monkeypatch):
    """Пакет не всегда заполняет title, а «нашёл профиль где-то» произносить
    вслух бессмысленно."""
    _install(monkeypatch)
    sites = [m["site"] for m in _connector(tmp_path).call(username="durov").data["matches"]]
    assert "ok.ru" in sites


def test_unknown_count_is_reported_aloud(tmp_path, monkeypatch):
    """Иначе «нашёл 3» звучит исчерпывающе, хотя часть сайтов не ответила."""
    _install(monkeypatch)
    message = _connector(tmp_path).call(username="durov").message
    assert "3" in message and "2" in message


def test_empty_result_is_honest(tmp_path, monkeypatch):
    _install(monkeypatch, raw={"detected": [], "unknown": [], "failed": []})
    result = _connector(tmp_path).call(username="никого")
    assert result.ok and result.data["total"] == 0
    assert "не нашлось" in result.message


def test_broken_schema_does_not_crash(tmp_path, monkeypatch):
    """Схема пакета менялась между версиями; падать посреди разговора нельзя."""
    _install(monkeypatch, raw={"detected": "вовсе не список"})
    result = _connector(tmp_path).call(username="durov")
    assert result.ok and result.data["total"] == 0


def test_search_is_silent_and_filtered(tmp_path, monkeypatch):
    """silent — иначе пакет печатает прогресс в консоль Джони. filter=good —
    иначе в сводку попадают догадки, по которым делают выводы о человеке."""
    analyzer = _install(monkeypatch)
    _connector(tmp_path).call(username="durov")
    assert analyzer.calls[0]["silent"] is True
    assert analyzer.calls[0]["filter"] == "good"


def test_access_is_logged(tmp_path, monkeypatch):
    _install(monkeypatch)
    _connector(tmp_path).call(username="durov")
    record = json.loads((tmp_path / "osint.log").read_text(encoding="utf-8").strip())
    assert record["username"] == "durov" and record["found"] == 3


def test_repeat_search_is_cached(tmp_path, monkeypatch):
    analyzer = _install(monkeypatch)
    c = _connector(tmp_path)
    c.call(username="durov")
    second = c.call(username="durov")
    assert second.cached and len(analyzer.calls) == 1


def test_cache_key_ignores_case_and_spaces(tmp_path, monkeypatch):
    """«Дуров» и «дуров » — один и тот же обход 50 сайтов."""
    analyzer = _install(monkeypatch)
    c = _connector(tmp_path)
    c.call(username="Durov")
    c.call(username=" durov ")
    assert len(analyzer.calls) == 1


def test_different_top_is_a_different_answer(tmp_path, monkeypatch):
    """Обход 50 сайтов не должен подменять обход 900."""
    analyzer = _install(monkeypatch)
    c = _connector(tmp_path)
    c.call(username="durov")
    c.call(username="durov", top=900)
    assert len(analyzer.calls) == 2


def test_empty_username_is_refusal(tmp_path, monkeypatch):
    _install(monkeypatch)
    result = _connector(tmp_path).call(username="  ")
    assert not result.ok and result.reason == "error"
