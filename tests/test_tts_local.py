"""Клиент локального TTS-сервиса (NeMo/XTTS по контракту POST /synthesize).

Паттерны тестов — те же, что в test_tts_fish.py: общий кеш/cooldown проверен
там один раз, здесь — специфика локального клиента: тело запроса, статус 503,
провайдер для cooldown и сквозной fallback.
"""

import contextlib
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

import johnny.tts_cache as tts_cache
import johnny.tts_local as tts_local

_MP3 = b"ID3" + b"\x00" * 61
assert len(_MP3) >= tts_cache.MIN_MP3_BYTES

_URL = "http://127.0.0.1:8765"


class FakeResponse:
    def __init__(self, status_code=200, content=_MP3, text=""):
        self.status_code = status_code
        self.content = content
        self.text = text


def _local(monkeypatch, tmp_path, response=None, fallback=None, boom=False, **kwargs):
    """Собрать say() с подменёнными сетью и проигрыванием. Возвращает (say, events)."""
    events = {"http": [], "played": [], "fallback": []}

    def fake_post(url, headers, payload, timeout):
        events["http"].append(payload)
        if boom:
            raise RuntimeError("нет сети")
        return response or FakeResponse()

    monkeypatch.setattr(tts_local, "post", fake_post)
    say = tts_local.make_local_tts(
        _URL,
        "johnny_v1",
        fallback or events["fallback"].append,
        cache_dir=tmp_path,
        play=events["played"].append,
        **kwargs,
    )
    return say, events


def test_synthesize_sends_contract_payload(monkeypatch):
    seen = {}

    def fake_post(url, headers, payload, timeout):
        seen.update(url=url, headers=headers, payload=payload, timeout=timeout)
        return FakeResponse(content=_MP3)

    monkeypatch.setattr(tts_local, "post", fake_post)
    assert tts_local.synthesize("Слушаю", _URL, "johnny_v1", style="calm") == _MP3
    assert seen["url"] == _URL
    assert seen["payload"] == {"text": "Слушаю", "voice_id": "johnny_v1", "style": "calm"}
    assert seen["timeout"] == 5.0


def test_synthesize_omits_style_when_empty(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        tts_local, "post", lambda url, headers, payload, timeout: seen.update(payload=payload) or FakeResponse()
    )
    tts_local.synthesize("Слушаю", _URL, "johnny_v1")
    assert seen["payload"] == {"text": "Слушаю", "voice_id": "johnny_v1"}


def test_synthesize_503_is_an_error_not_a_contract(monkeypatch):
    """503 по контракту — «модель ещё грузится / занята»; для нас это обычная
    неудача, ведущая на fallback, а не повод остановиться и ждать."""
    monkeypatch.setattr(
        tts_local, "post", lambda *a, **kw: FakeResponse(status_code=503, text="loading model")
    )
    with pytest.raises(RuntimeError):
        tts_local.synthesize("Слушаю", _URL, "johnny_v1")


def test_synthesize_rejects_non_mp3_body(monkeypatch):
    monkeypatch.setattr(
        tts_local, "post", lambda *a, **kw: FakeResponse(content=b"<html>placeholder")
    )
    with pytest.raises(RuntimeError):
        tts_local.synthesize("Слушаю", _URL, "johnny_v1")


def test_say_falls_back_when_server_down(monkeypatch, tmp_path):
    say, events = _local(monkeypatch, tmp_path, boom=True)
    say("Слушаю")
    assert events["fallback"] == ["Слушаю"]
    assert events["played"] == []


def test_say_plays_short_phrase_and_caches(monkeypatch, tmp_path):
    say, events = _local(monkeypatch, tmp_path)
    say("Готово")
    say("Готово")
    assert len(events["http"]) == 1      # вторая фраза — из кеша
    assert len(events["played"]) == 2
    assert len(list(tmp_path.glob("*.mp3"))) == 1


def test_style_is_part_of_cache_key(tmp_path):
    """Одна и та же фраза в calm и confident звучит по-разному — кеши
    обязаны быть разными, иначе второй стиль молча не синтезируется."""
    a = tts_cache.cache_path(tmp_path, "Слушаю", f"{_URL}|johnny_v1|calm")
    b = tts_cache.cache_path(tmp_path, "Слушаю", f"{_URL}|johnny_v1|confident")
    assert a != b


def test_failure_marks_its_own_cooldown_key(monkeypatch, tmp_path):
    """Локальный сервис и fish — разные cooldown'ы: упавший NeMo не должен
    глушить попытки к fish и наоборот (tts_cache.make_cached_tts ключует по
    провайдеру, и это обязано быть видно в тесте)."""
    marked = []
    monkeypatch.setattr(tts_cache, "mark_failure", lambda key: marked.append(key))
    say, events = _local(monkeypatch, tmp_path, boom=True)
    say("Слушаю")
    assert marked == ["tts-local"]
    assert events["fallback"] == ["Слушаю"]


# --- Живой сокет -------------------------------------------------------------
# Всё выше подменяет post(), то есть проверяет НАШУ логику, а не то, что клиент
# вообще умеет ходить по сети. Здесь поднимается настоящий HTTP-сервер на
# stdlib (без fastapi — в основном venv его намеренно нет, см.
# services/tts-server/README.md) и клиент работает через реальный сокет:
# сериализация тела, User-Agent, статусы, mp3 из сокета на диск.


@contextlib.contextmanager
def _live_server(reply: str = "mp3"):
    """HTTP-сервер, отвечающий как настоящий tts_server. Отдаёт (url, seen)."""
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            seen.append({"json": json.loads(body), "ua": self.headers.get("User-Agent", "")})
            if reply == "mp3":
                self.send_response(200)
                self.send_header("Content-Type", "audio/mpeg")
                self.send_header("Content-Length", str(len(_MP3)))
                self.end_headers()
                self.wfile.write(_MP3)
                return
            if reply == "html":  # прокси перед сервисом: 200 с телом-страницей
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"<html>placeholder</html>")
                return
            self.send_response(503)  # модель ещё не поднялась — контрактный путь
            self.end_headers()
            self.wfile.write(b'{"error": "tts model unavailable"}')

        def log_message(self, *args):
            return  # без шума в вывод pytest

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/synthesize", seen
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_live_socket_roundtrip_writes_playable_mp3(tmp_path):
    """Сквозь настоящий сокет: тело по контракту, браузерный User-Agent
    (его требует http_client.post) и mp3 из сети, дошедший до диска байт в байт."""
    played, fell_back = [], []
    with _live_server() as (url, seen):
        say = tts_local.make_local_tts(
            url, "johnny_v1", fell_back.append, style="calm",
            cache_dir=tmp_path, play=played.append,
        )
        say("Слушаю")

    assert fell_back == []
    assert seen[0]["json"] == {"text": "Слушаю", "voice_id": "johnny_v1", "style": "calm"}
    assert "Mozilla" in seen[0]["ua"]
    assert Path(played[0]).read_bytes() == _MP3


def test_live_socket_second_phrase_comes_from_cache(tmp_path):
    """Кеш обязан работать поверх реальной сети, а не только поверх мока."""
    with _live_server() as (url, seen):
        say = tts_local.make_local_tts(
            url, "johnny_v1", lambda text: None, cache_dir=tmp_path, play=lambda p: None
        )
        say("Готово")
        say("Готово")
    assert len(seen) == 1


@pytest.mark.parametrize("reply", ["503", "html"])
def test_live_socket_bad_reply_falls_back_and_leaves_cache_clean(tmp_path, reply, monkeypatch):
    """503 и «200 с HTML» на живом сокете: запасной голос и ЧИСТЫЙ кеш —
    мусор, осевший под именем mp3, проигрывался бы вечно."""
    monkeypatch.setattr(tts_cache, "mark_failure", lambda key: None)  # не копить cooldown между тестами
    played, fell_back = [], []
    with _live_server(reply) as (url, _):
        say = tts_local.make_local_tts(
            url, "johnny_v1", fell_back.append, cache_dir=tmp_path, play=played.append
        )
        say("Слушаю")
    assert fell_back == ["Слушаю"] and played == []
    assert list(tmp_path.glob("*")) == []


def test_live_socket_dead_port_falls_back(tmp_path, monkeypatch):
    """Сервис просто не запущен — самый частый случай на машине без GPU."""
    monkeypatch.setattr(tts_cache, "mark_failure", lambda key: None)
    with _live_server() as (url, _):
        pass  # сервер закрылся вместе с контекстом, порт мёртв
    fell_back = []
    say = tts_local.make_local_tts(
        url, "johnny_v1", fell_back.append, cache_dir=tmp_path, play=lambda p: None, timeout=1.0
    )
    say("Слушаю")
    assert fell_back == ["Слушаю"]


# ── Прогрев (warm_up) ────────────────────────────────────────────────────────
# Зовётся из speaker._start_local_warm_up при старте Джони — см. тесты вокруг
# него в test_speaker.py. Здесь — сама функция: куда стучится и что считает
# «проснулся».


class FakeHealth:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


def test_warm_up_asks_health_beside_synthesize(monkeypatch):
    """/health берётся рядом с /synthesize и с тем же Bearer: закрытый токеном
    облачный сервис иначе ответил бы 401, и прогрев решил бы, что тот лежит."""
    seen = {}

    def fake_get(url, headers, timeout):
        seen.update(url=url, headers=headers, timeout=timeout)
        return FakeHealth(200, {"backend": "xtts"})

    monkeypatch.setattr(tts_local, "get", fake_get)
    assert tts_local.warm_up(_URL + "/synthesize", token="secret") is True
    assert seen["url"] == _URL + "/health"
    assert seen["headers"]["Authorization"] == "Bearer secret"
    # Таймаут прогрева — НЕ голосовые 5 с: холодный старт облака дольше, а
    # ждать его тут можно, поток фоновый.
    assert seen["timeout"] > tts_local._TIMEOUT


def test_warm_up_waits_for_the_model_not_just_the_port(monkeypatch):
    """200 без backend — процесс поднялся, модель ещё грузится. Для нас это
    не «проснулся»: синтез в таком состоянии отдаст 503."""
    monkeypatch.setattr(tts_local, "get", lambda url, headers, timeout: FakeHealth(200, {}))
    assert tts_local.warm_up(_URL + "/synthesize") is False


def test_warm_up_never_raises_when_service_is_down(monkeypatch):
    """Незапущенный или спящий сервис — НОРМА, а не поломка. Прогрев обязан
    молча вернуть False: он крутится в фоновом потоке при старте Джони, и
    трейсбек оттуда просто утёк бы в лог, ничего не починив."""

    def boom(url, headers, timeout):
        raise RuntimeError("нет сети")

    monkeypatch.setattr(tts_local, "get", boom)
    assert tts_local.warm_up(_URL + "/synthesize") is False
