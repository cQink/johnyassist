"""Диск коннекторов: кеш с TTL и суточная квота, переживающая перезапуск."""

import json
import time

import johnny.connectors.store as store


def test_cache_survives_and_returns_same_data(tmp_path):
    store.write(tmp_path, "azure-vision", {"url": "a.jpg"}, {"matches": 3})
    assert store.read(tmp_path, "azure-vision", {"url": "a.jpg"}) == {"matches": 3}


def test_cache_key_ignores_param_order(tmp_path):
    """Иначе один и тот же запрос с другим порядком ключей — второй файл и
    второй платный вызов."""
    store.write(tmp_path, "azure-vision", {"url": "a.jpg", "limit": 5}, {"matches": 1})
    assert store.read(tmp_path, "azure-vision", {"limit": 5, "url": "a.jpg"}) == {"matches": 1}


def test_different_params_do_not_collide(tmp_path):
    store.write(tmp_path, "azure-vision", {"url": "a.jpg"}, {"matches": 1})
    assert store.read(tmp_path, "azure-vision", {"url": "b.jpg"}) is None


def test_stale_cache_is_not_returned(tmp_path, monkeypatch):
    store.write(tmp_path, "azure-vision", {"url": "a.jpg"}, {"matches": 3})
    real_time = time.time()
    monkeypatch.setattr(store.time, "time", lambda: real_time + 2 * 86400)
    assert store.read(tmp_path, "azure-vision", {"url": "a.jpg"}) is None


def test_ttl_zero_means_forever(tmp_path, monkeypatch):
    store.write(tmp_path, "whois", {"domain": "x.ru"}, {"owner": "кто-то"})
    real_time = time.time()
    monkeypatch.setattr(store.time, "time", lambda: real_time + 365 * 86400)
    assert store.read(tmp_path, "whois", {"domain": "x.ru"}, ttl=0) == {"owner": "кто-то"}


def test_broken_cache_file_is_deleted_not_raised(tmp_path):
    """Битый файл обязан исчезнуть: иначе он вечно выглядит как «кеш есть»
    и живой запрос не случается никогда."""
    path = tmp_path / f"{store.cache_key('azure-vision', {'url': 'a.jpg'})}.json"
    path.write_text("{не json", encoding="utf-8")
    assert store.read(tmp_path, "azure-vision", {"url": "a.jpg"}) is None
    assert not path.exists()


def test_missing_cache_dir_is_not_an_error(tmp_path):
    assert store.read(tmp_path / "нет", "azure-vision", {"url": "a.jpg"}) is None


def test_trim_keeps_newest_and_removes_orphan_parts(tmp_path):
    for i in range(5):
        store.write(tmp_path, "svc", {"i": i}, {"n": i})
    (tmp_path / "oops.part").write_text("огрызок", encoding="utf-8")
    store.trim(tmp_path, limit=2)
    assert len(list(tmp_path.glob("*.json"))) == 2
    assert list(tmp_path.glob("*.part")) == []


def test_write_leaves_no_part_file(tmp_path):
    store.write(tmp_path, "svc", {"a": 1}, {"b": 2})
    assert list(tmp_path.glob("*.part")) == []


def test_quota_counts_only_bumped_calls(tmp_path):
    state = tmp_path / "state.json"
    assert store.usage(state, "faceplusplus")[0] == 0
    store.bump(state, "faceplusplus")
    store.bump(state, "faceplusplus")
    assert store.usage(state, "faceplusplus")[0] == 2


def test_quota_survives_restart(tmp_path):
    """Смысл счётчика на диске: Джони за вечер правок перезапускается десятки
    раз, и счёт в памяти сжёг бы суточную квоту за час — молча."""
    state = tmp_path / "state.json"
    store.bump(state, "faceplusplus")
    assert json.loads(state.read_text(encoding="utf-8"))["faceplusplus"]["count"] == 1
    assert store.usage(state, "faceplusplus")[0] == 1


def test_quota_is_per_connector(tmp_path):
    state = tmp_path / "state.json"
    store.bump(state, "faceplusplus")
    assert store.usage(state, "azure-vision")[0] == 0


def test_quota_resets_on_new_day(tmp_path, monkeypatch):
    state = tmp_path / "state.json"
    store.bump(state, "faceplusplus")
    monkeypatch.setattr(store, "_today", lambda: "2099-01-01")
    assert store.usage(state, "faceplusplus")[0] == 0


def test_bump_records_last_call_time(tmp_path):
    state = tmp_path / "state.json"
    before = time.time()
    store.bump(state, "svc")
    _, last = store.usage(state, "svc")
    assert last >= before


def test_broken_state_file_does_not_crash(tmp_path):
    """Потерять счёт квоты хуже, чем уронить ассистента на служебном файле —
    но уронить нельзя вовсе."""
    state = tmp_path / "state.json"
    state.write_text("{битый", encoding="utf-8")
    assert store.usage(state, "svc") == (0, 0.0)
    store.bump(state, "svc")
    assert store.usage(state, "svc")[0] == 1
