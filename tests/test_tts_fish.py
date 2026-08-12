from pathlib import Path

import pytest

import johnny.tts_cache as tts_cache
import johnny.tts_fish as tts_fish

# Настоящего mp3 в тестах не нужно — достаточно тела, которое проходит
# _looks_like_mp3 (сигнатура ID3 + разумная длина).
_MP3 = b"ID3" + b"\x00" * 61
assert len(_MP3) >= tts_fish._MIN_MP3_BYTES


class FakeResponse:
    def __init__(self, status_code=200, content=_MP3, text=""):
        self.status_code = status_code
        self.content = content
        self.text = text


def _fish(monkeypatch, tmp_path, response=None, fallback=None, boom=False):
    """Собрать say() с подменёнными сетью и проигрыванием. Возвращает (say, events)."""
    events = {"http": [], "played": [], "fallback": []}

    def fake_post(url, headers, payload, timeout):
        events["http"].append(payload["text"])
        if boom:
            raise RuntimeError("нет сети")
        return response or FakeResponse()

    monkeypatch.setattr(tts_fish, "post", fake_post)
    say = tts_fish.make_fish_tts(
        "ключ",
        "модель",
        fallback or events["fallback"].append,
        cache_dir=tmp_path,
        play=events["played"].append,
    )
    return say, events


def test_short_phrase_is_cached_and_second_call_skips_network(monkeypatch, tmp_path):
    say, events = _fish(monkeypatch, tmp_path)
    say("Не понял команду")
    say("Не понял команду")
    assert events["http"] == ["Не понял команду"]      # сеть дёрнули один раз
    assert len(events["played"]) == 2                   # звук проиграли дважды
    assert len(list(tmp_path.glob("*.mp3"))) == 1


def test_cache_file_holds_synthesized_bytes(monkeypatch, tmp_path):
    say, _ = _fish(monkeypatch, tmp_path, response=FakeResponse(content=_MP3 + b"JARVIS"))
    say("Готово")
    assert list(tmp_path.glob("*.mp3"))[0].read_bytes() == _MP3 + b"JARVIS"


def test_long_text_is_not_cached(monkeypatch, tmp_path):
    long_text = "Токио — столица Японии и самый населённый город мира, сэр."
    say, events = _fish(monkeypatch, tmp_path)
    say(long_text)
    say(long_text)
    assert events["http"] == [long_text, long_text]     # каждый раз заново
    assert list(tmp_path.glob("*.mp3")) == []           # в кеше ничего не осело
    assert len(events["played"]) == 2


def test_http_error_falls_back(monkeypatch, tmp_path):
    say, events = _fish(monkeypatch, tmp_path, response=FakeResponse(status_code=402, text="no money"))
    say("Не понял команду")
    assert events["fallback"] == ["Не понял команду"]   # ушли на запасной голос
    assert events["played"] == []
    assert list(tmp_path.glob("*.mp3")) == []           # неудачу не кешируем


def test_network_exception_falls_back(monkeypatch, tmp_path):
    say, events = _fish(monkeypatch, tmp_path, boom=True)
    say("Не понял команду")
    assert events["fallback"] == ["Не понял команду"]


def test_empty_text_does_nothing(monkeypatch, tmp_path):
    say, events = _fish(monkeypatch, tmp_path)
    say("")
    assert events == {"http": [], "played": [], "fallback": []}


def test_store_is_atomic_and_leaves_no_part_file(tmp_path):
    target = tmp_path / "phrase.mp3"
    tts_fish._store(target, _MP3)
    assert target.read_bytes() == _MP3
    assert list(tmp_path.glob("*.part")) == []


def test_trim_removes_oldest_beyond_limit(tmp_path):
    import os

    for number in range(5):
        path = tmp_path / f"{number}.mp3"
        path.write_bytes(b"x")
        os.utime(path, (number, number))          # 0.mp3 — самый старый
    tts_fish._trim(tmp_path, limit=2)
    assert sorted(p.name for p in tmp_path.glob("*.mp3")) == ["3.mp3", "4.mp3"]


def test_trim_removes_orphan_part_files(tmp_path):
    """Процесс мог быть убит между записью .part и os.replace в _store —
    такой огрызок сам себя никогда не удалит, только следующая уборка кеша."""
    (tmp_path / "orphan.part").write_bytes(b"partial-bytes")
    (tmp_path / "0.mp3").write_bytes(b"x")
    tts_fish._trim(tmp_path, limit=100)
    assert list(tmp_path.glob("*.part")) == []
    assert [p.name for p in tmp_path.glob("*.mp3")] == ["0.mp3"]


def test_synthesize_sends_key_model_and_reference(monkeypatch):
    seen = {}

    def fake_post(url, headers, payload, timeout):
        seen.update(url=url, headers=headers, payload=payload, timeout=timeout)
        return FakeResponse(content=_MP3)

    monkeypatch.setattr(tts_fish, "post", fake_post)
    assert tts_fish.synthesize("привет", "ключ", "модель") == _MP3
    assert seen["url"] == "https://api.fish.audio/v1/tts"
    assert seen["headers"]["Authorization"] == "Bearer ключ"
    assert seen["headers"]["model"] == "s2.1-pro-free"       # бесплатная модель клонирования
    assert seen["payload"] == {"text": "привет", "reference_id": "модель", "format": "mp3"}
    assert seen["timeout"] == 10.0


def test_synthesize_rejects_non_mp3_body_with_200(monkeypatch):
    """Fish отдал 200, но тело — страница Cloudflare, а не аудио: доверять
    статус-коду нельзя, иначе такой мусор осядет в кеше навсегда."""
    monkeypatch.setattr(
        tts_fish, "post", lambda *a, **kw: FakeResponse(content=b"<html>Cloudflare")
    )
    with pytest.raises(RuntimeError):
        tts_fish.synthesize("привет", "ключ", "модель")


def test_synthesize_accepts_valid_mp3_header(monkeypatch):
    monkeypatch.setattr(tts_fish, "post", lambda *a, **kw: FakeResponse(content=_MP3))
    assert tts_fish.synthesize("привет", "ключ", "модель") == _MP3


def test_non_mp3_body_falls_back_and_cache_stays_empty(monkeypatch, tmp_path):
    """Тело b'<html>Cloudflare' с кодом 200 не должно попасть в кеш."""
    say, events = _fish(monkeypatch, tmp_path, response=FakeResponse(content=b"<html>Cloudflare"))
    say("Не понял команду")
    assert events["fallback"] == ["Не понял команду"]
    assert events["played"] == []
    assert list(tmp_path.glob("*.mp3")) == []


def test_cache_key_depends_on_model_id(tmp_path):
    # Переобучили голос Джарвиса и сменили fish_model_id в настройках — старый
    # кеш обязан стать невидимым (другое имя файла), а не звучать прежним
    # голосом молча до ручной чистки models/tts-cache/.
    path_a = tts_fish._cache_path(tmp_path, "Слушаю", "модель-1")
    path_b = tts_fish._cache_path(tmp_path, "Слушаю", "модель-2")
    assert path_a != path_b


def test_different_model_id_does_not_reuse_cached_file(monkeypatch, tmp_path):
    events = {"http": [], "played": []}

    def fake_post(url, headers, payload, timeout):
        events["http"].append(payload["reference_id"])
        return FakeResponse(content=_MP3)

    monkeypatch.setattr(tts_fish, "post", fake_post)
    say_v1 = tts_fish.make_fish_tts(
        "ключ", "модель-1", lambda t: None, cache_dir=tmp_path, play=events["played"].append
    )
    say_v2 = tts_fish.make_fish_tts(
        "ключ", "модель-2", lambda t: None, cache_dir=tmp_path, play=events["played"].append
    )
    say_v1("Слушаю")
    say_v2("Слушаю")
    assert events["http"] == ["модель-1", "модель-2"]   # оба раза сходили в сеть
    assert len(list(tmp_path.glob("*.mp3"))) == 2       # два разных файла кеша


def test_play_failure_on_cached_phrase_falls_back_and_deletes_broken_cache_file(monkeypatch, tmp_path):
    """Ветка «попадание в кеш» (файл уже существовал): play() бросает
    исключение — файл считается битым и удаляется, чтобы не проигрываться
    (безуспешно) вечно, даже если пересинтез следом тоже не удастся."""
    events = {"fallback": []}
    cached_path = tts_fish._cache_path(tmp_path, "Готово", "модель")
    cached_path.parent.mkdir(parents=True, exist_ok=True)
    cached_path.write_bytes(b"broken-bytes")

    def fake_post(url, headers, payload, timeout):
        raise RuntimeError("нет сети")

    def fake_play_fails(path):
        raise RuntimeError("play error")

    monkeypatch.setattr(tts_fish, "post", fake_post)
    say = tts_fish.make_fish_tts(
        "ключ", "модель", events["fallback"].append, cache_dir=tmp_path, play=fake_play_fails
    )

    say("Готово")

    assert events["fallback"] == ["Готово"]
    assert not cached_path.exists()   # битый файл не остался лежать под тем же именем


def test_play_failure_on_freshly_cached_phrase_falls_back(monkeypatch, tmp_path):
    """Короткая фраза (кешируемая ветка), кеша ещё нет: play() свежесинтезированного
    файла бросает исключение → fallback, без необработанного исключения наружу."""
    events = {"fallback": [], "store": []}

    def fake_post(url, headers, payload, timeout):
        return FakeResponse(content=_MP3)

    def fake_play_fails(path):
        events["store"].append(path)
        raise RuntimeError("play error")

    monkeypatch.setattr(tts_fish, "post", fake_post)
    say = tts_fish.make_fish_tts(
        "ключ",
        "модель",
        events["fallback"].append,
        cache_dir=tmp_path,
        play=fake_play_fails,
    )

    # Это должно не бросить исключение наружу, а вызвать fallback
    say("Готово")

    assert events["fallback"] == ["Готово"]
    assert len(events["store"]) == 1  # play был вызван с синтезированным файлом


def test_play_failure_on_long_uncached_text_falls_back_and_removes_temp_file(monkeypatch, tmp_path):
    """Ветка «текст длиннее 40 символов» (не кешируется): play() падает —
    fallback вызван, а временный файл не должен остаться на диске навсегда."""
    long_text = "Токио — столица Японии и самый населённый город мира, сэр."
    events = {"fallback": []}

    def fake_post(url, headers, payload, timeout):
        return FakeResponse(content=_MP3)

    def fake_play_fails(path):
        raise RuntimeError("play error")

    monkeypatch.setattr(tts_fish, "post", fake_post)
    # tempfile/cooldown живут в общем tts_cache (им пользуется и локальный
    # сервис) — патчим там, проверяем по-прежнему сквозной путь fish.
    monkeypatch.setattr(tts_cache.tempfile, "gettempdir", lambda: str(tmp_path))
    say = tts_fish.make_fish_tts(
        "ключ", "модель", events["fallback"].append, cache_dir=tmp_path, play=fake_play_fails
    )

    say(long_text)

    assert events["fallback"] == [long_text]
    assert list(tmp_path.glob("*.mp3")) == []   # временный файл удалён, не осиротел


def test_trim_is_actually_called_after_caching_new_phrase(monkeypatch, tmp_path):
    """Пробел покрытия: раньше ни один тест не падал бы, если убрать вызов
    _trim() из say() — переполнение кеша осталось бы незамеченным."""
    import os

    for number in range(tts_fish._MAX_CACHE_FILES):
        path = tmp_path / f"old{number}.mp3"
        path.write_bytes(b"x")
        os.utime(path, (number, number))

    say, _ = _fish(monkeypatch, tmp_path, response=FakeResponse(content=_MP3))
    say("Новая короткая фраза")

    assert len(list(tmp_path.glob("*.mp3"))) == tts_fish._MAX_CACHE_FILES


def test_cooldown_skips_network_and_goes_straight_to_fallback(monkeypatch, tmp_path):
    say, events = _fish(monkeypatch, tmp_path)
    monkeypatch.setattr(tts_cache, "in_cooldown", lambda key: True)
    say("Не понял команду")
    assert events["http"] == []                          # в сеть не ходили
    assert events["fallback"] == ["Не понял команду"]


def test_network_failure_marks_cooldown(monkeypatch, tmp_path):
    marked = []
    monkeypatch.setattr(tts_cache, "mark_failure", lambda key: marked.append(key))
    say, events = _fish(monkeypatch, tmp_path, boom=True)
    say("Не понял команду")
    assert marked == ["fish"]


def test_prepare_returns_a_file_instead_of_playing_it(tmp_path):
    """Ядро конвейера: синтез отдаёт ФАЙЛ, а играет его кто-то другой и позже.
    Слитые вместе, они не дают синтезировать следующую фразу во время
    проигрывания предыдущей."""
    prepare = tts_cache.make_cached_prepare(
        lambda text: b"ID3" + b"x" * 100,
        provider="fish", voice_key="v", cache_dir=tmp_path,
    )
    prepared = prepare("привет")
    assert prepared.path is not None
    assert Path(prepared.path).exists()


def test_prepare_reuses_the_cache_for_short_phrases(tmp_path):
    """Филлеры короткие и звучат сотнями раз: второй раз они обязаны браться
    с диска, иначе филлер сам станет задержкой, которую призван скрыть."""
    calls = []

    def synth(text):
        calls.append(text)
        return b"ID3" + b"x" * 100

    prepare = tts_cache.make_cached_prepare(
        synth, provider="fish", voice_key="v", cache_dir=tmp_path
    )
    first = prepare("Секунду")
    second = prepare("Секунду")
    assert calls == ["Секунду"]
    assert first.path == second.path
    assert first.temporary is False


def test_prepare_marks_long_answers_as_temporary(tmp_path):
    """Ответы модели дословно не повторяются — их файлы удаляются после
    проигрывания, иначе кеш зарастает мусором."""
    prepare = tts_cache.make_cached_prepare(
        lambda text: b"ID3" + b"x" * 100,
        provider="fish", voice_key="v", cache_dir=tmp_path,
    )
    prepared = prepare("а" * (tts_cache.MAX_CACHED_CHARS + 1))
    assert prepared.temporary is True


def test_prepare_reports_failure_instead_of_raising(tmp_path):
    """Отказ синтеза в середине ответа не должен ронять конвейер: остаток
    доигрывает запасной голос, а решает это зовущий."""
    def boom(text):
        raise RuntimeError("fish отказал")

    prepare = tts_cache.make_cached_prepare(
        boom, provider="fish-test-fail", voice_key="v", cache_dir=tmp_path
    )
    assert prepare("привет").path is None


def test_prepare_rejects_a_body_that_is_not_mp3(tmp_path):
    """Cloudflare отдаёт 200 со страницей вместо mp3 — попав в кеш, она
    проигрывалась бы вечно."""
    prepare = tts_cache.make_cached_prepare(
        lambda text: b"<html>not mp3</html>",
        provider="fish-test-html", voice_key="v", cache_dir=tmp_path,
    )
    assert prepare("привет").path is None
