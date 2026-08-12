import builtins

import pytest

import johnny.speaker as speaker
from johnny.config import Settings
from johnny.speaker import Speaker, _ACK_PHRASES, _make_edge_tts


def test_voice_mode_calls_tts():
    said = []
    sp = Speaker("voice", tts=said.append, beep=lambda: said.append("BEEP"))
    sp.say("привет")
    assert said == ["привет"]


def test_beep_mode_calls_beep_only():
    events = []
    sp = Speaker("beep", tts=lambda t: events.append(("tts", t)), beep=lambda: events.append("beep"))
    sp.say("привет")
    assert events == ["beep"]


def test_off_mode_stays_silent():
    events = []
    sp = Speaker("off", tts=lambda t: events.append(t), beep=lambda: events.append("beep"))
    sp.say("привет")
    assert events == []


def test_acknowledge_voice_beeps_then_speaks():
    events = []
    sp = Speaker("voice", tts=lambda t: events.append(("tts", t)), beep=lambda: events.append("beep"))
    sp.acknowledge()
    assert events[0] == "beep"                       # сначала сигнал
    assert events[1][0] == "tts"                     # потом голос
    assert events[1][1] in _ACK_PHRASES              # одна из фраз-подтверждений


def test_acknowledge_beep_mode_beeps_only():
    events = []
    sp = Speaker("beep", tts=lambda t: events.append(("tts", t)), beep=lambda: events.append("beep"))
    sp.acknowledge()
    assert events == ["beep"]


def test_acknowledge_off_mode_silent():
    events = []
    sp = Speaker("off", tts=lambda t: events.append(t), beep=lambda: events.append("beep"))
    sp.acknowledge()
    assert events == []


def test_acknowledge_plays_wakeup_sound_and_skips_beep():
    events = []
    sp = Speaker(
        "voice",
        tts=lambda t: events.append(("tts", t)),
        beep=lambda: events.append("beep"),
        play_wakeup=lambda: (events.append("wakeup") or True),
    )
    sp.acknowledge()
    assert events == ["wakeup"]  # свой звук — ни бипа, ни голоса


def test_acknowledge_falls_back_when_no_wakeup_sound():
    events = []
    sp = Speaker(
        "voice",
        tts=lambda t: events.append(("tts", t)),
        beep=lambda: events.append("beep"),
        play_wakeup=lambda: False,
    )
    sp.acknowledge()
    assert events[0] == "beep" and events[1][0] == "tts"


def test_play_answer_true_when_sound_plays():
    sp = Speaker("voice", play_answer=lambda: True)
    assert sp.play_answer() is True


def test_play_answer_false_when_no_sound():
    sp = Speaker("voice", play_answer=lambda: False)
    assert sp.play_answer() is False


def test_play_answer_off_mode_is_silent_true():
    sp = Speaker("off", play_answer=lambda: True)
    assert sp.play_answer() is True


def test_edge_tts_uses_edge_when_ok(monkeypatch):
    calls = {}
    monkeypatch.setattr(speaker, "_edge_synth_and_play", lambda t, v: calls.setdefault("edge", (t, v)))
    fell_back = []
    say = _make_edge_tts("ru-RU-DmitryNeural", fell_back.append)
    say("привет")
    assert calls["edge"] == ("привет", "ru-RU-DmitryNeural")
    assert fell_back == []  # запасной голос не трогаем


def test_edge_tts_falls_back_on_error(monkeypatch):
    def boom(text, voice):
        raise RuntimeError("нет интернета")

    monkeypatch.setattr(speaker, "_edge_synth_and_play", boom)
    fell_back = []
    say = _make_edge_tts("ru-RU-DmitryNeural", fell_back.append)
    say("привет")
    assert fell_back == ["привет"]  # ушли на офлайн-голос


def test_edge_tts_ignores_empty_text(monkeypatch):
    calls = []
    monkeypatch.setattr(speaker, "_edge_synth_and_play", lambda t, v: calls.append(t))
    say = _make_edge_tts("ru-RU-DmitryNeural", calls.append)
    say("")
    assert calls == []


def _settings(**overrides) -> Settings:
    base = dict(
        wake_word="джони",
        vosk_model_path="",
        response_mode="voice",
        whisper_model="small",
        whisper_device="cpu",
    )
    base.update(overrides)
    return Settings(**base)


def _no_audio(monkeypatch):
    """Заглушить всё, что лезет к железу и в сеть при сборке Speaker.

    Прогрев TTS-сервиса — тоже сюда: он уходит в фоновый поток с сетевым
    таймаутом до минуты, и ни одному свойству, которое проверяют тесты ниже,
    он не нужен. Кому нужен — подменяет _start_local_warm_up своим.
    """
    monkeypatch.setattr(speaker, "_make_tts", lambda volume=1.0: (lambda text: None))
    monkeypatch.setattr(speaker, "_make_beep", lambda volume=1.0: (lambda: None))
    monkeypatch.setattr(speaker, "_start_local_warm_up", lambda url, token: None)


def test_make_speaker_uses_fish_when_key_and_model_present(monkeypatch):
    import johnny.tts_fish as tts_fish

    _no_audio(monkeypatch)
    seen = {}

    def fake_make_fish_tts(api_key, model_id, fallback):
        seen["args"] = (api_key, model_id)
        return lambda text: None

    monkeypatch.setattr(tts_fish, "make_fish_tts", fake_make_fish_tts)
    speaker.make_speaker(_settings(fish_model_id="abc"), {"fish_api_key": "k"})
    assert seen["args"] == ("k", "abc")


def test_make_speaker_uses_edge_without_key(monkeypatch):
    import johnny.tts_fish as tts_fish

    _no_audio(monkeypatch)
    monkeypatch.setattr(tts_fish, "make_fish_tts", lambda *a, **kw: pytest.fail("fish без ключа"))
    speaker.make_speaker(_settings(fish_model_id="abc"), {})


def test_make_speaker_uses_edge_when_provider_is_edge(monkeypatch):
    import johnny.tts_fish as tts_fish

    _no_audio(monkeypatch)
    monkeypatch.setattr(tts_fish, "make_fish_tts", lambda *a, **kw: pytest.fail("провайдер edge"))
    speaker.make_speaker(_settings(tts_provider="edge", fish_model_id="abc"), {"fish_api_key": "k"})


def test_make_speaker_uses_edge_without_model_id(monkeypatch):
    import johnny.tts_fish as tts_fish

    _no_audio(monkeypatch)
    monkeypatch.setattr(tts_fish, "make_fish_tts", lambda *a, **kw: pytest.fail("нет model_id"))
    speaker.make_speaker(_settings(fish_model_id=""), {"fish_api_key": "k"})


def test_make_speaker_uses_local_service_when_configured(monkeypatch):
    import johnny.tts_local as tts_local

    _no_audio(monkeypatch)
    seen = {}

    def fake_make_local_tts(url, voice_id, fallback, style="", token=""):
        seen["args"] = (url, voice_id, style)
        seen["token"] = token
        return lambda text: None

    monkeypatch.setattr(tts_local, "make_local_tts", fake_make_local_tts)
    speaker.make_speaker(
        _settings(
            tts_provider="local",
            tts_local_url="http://127.0.0.1:8765",
            tts_voice_id="johnny_v1",
            tts_style="calm",
        ),
        {"tts_local_token": "secret"},
    )
    assert seen["args"] == ("http://127.0.0.1:8765", "johnny_v1", "calm")
    # Токен приходит ИЗ СЕКРЕТОВ: settings.yaml лежит в репозитории, а
    # облачный endpoint без токена — чужой инференс за наш счёт.
    assert seen["token"] == "secret"


def test_local_provider_keeps_fish_as_second_link(monkeypatch):
    """Машина без запущенного GPU-сервиса не должна терять голос Джарвиса:
    fish обязан стоять ВТОРЫМ звеном, а не отключаться вместе с провайдером."""
    import johnny.tts_fish as tts_fish
    import johnny.tts_local as tts_local

    _no_audio(monkeypatch)
    seen = {}
    monkeypatch.setattr(
        tts_fish, "make_fish_tts", lambda key, model, fb: seen.setdefault("fish", (key, model))
    )
    monkeypatch.setattr(tts_local, "make_local_tts", lambda *a, **kw: (lambda text: None))
    speaker.make_speaker(
        _settings(tts_provider="local", tts_local_url="http://x", fish_model_id="abc"),
        {"fish_api_key": "k"},
    )
    assert seen["fish"] == ("k", "abc")


def test_make_speaker_skips_local_without_url(monkeypatch):
    """tts_provider: local без адреса — выключено, ни одной сетевой попытки."""
    import johnny.tts_local as tts_local

    _no_audio(monkeypatch)
    monkeypatch.setattr(
        tts_local, "make_local_tts", lambda *a, **kw: pytest.fail("local без tts_local_url")
    )
    speaker.make_speaker(_settings(tts_provider="local", tts_local_url=""), {})


def test_make_speaker_warms_up_local_service(monkeypatch):
    """Локальный TTS включён — прогрев уходит вместе с ним, тем же адресом и
    тем же токеном, что и синтез.

    Пока warm_up не звали ниоткуда, задуманная защита была мёртвым кодом:
    облако со scale-to-zero просыпается десятки секунд, а голосовой таймаут —
    5 с, то есть первая фраза Джони гарантированно уезжала на запасной голос.
    """
    import johnny.tts_local as tts_local

    _no_audio(monkeypatch)
    seen = {}
    monkeypatch.setattr(tts_local, "make_local_tts", lambda *a, **kw: (lambda text: None))
    monkeypatch.setattr(
        speaker, "_start_local_warm_up", lambda url, token: seen.update(url=url, token=token)
    )
    speaker.make_speaker(
        _settings(tts_provider="local", tts_local_url="http://127.0.0.1:8765"),
        {"tts_local_token": "secret"},
    )
    assert seen == {"url": "http://127.0.0.1:8765", "token": "secret"}


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param(dict(tts_provider="fish", tts_local_url="http://x"), id="провайдер-fish"),
        pytest.param(dict(tts_provider="edge", tts_local_url="http://x"), id="провайдер-edge"),
        pytest.param(dict(tts_provider="local", tts_local_url=""), id="local-без-адреса"),
        pytest.param(
            dict(tts_provider="local", tts_local_url="http://x", response_mode="beep"),
            id="режим-beep",
        ),
        pytest.param(
            dict(tts_provider="local", tts_local_url="http://x", response_mode="off"),
            id="режим-off",
        ),
    ],
)
def test_no_warm_up_when_local_tts_is_off(monkeypatch, overrides):
    """Выключенный локальный TTS не прогревают ВОВСЕ: ни потока, ни запроса.

    Прогрев обязан сниматься на том же условии, на котором выключается сам
    сервис. Разъехавшись с ним, он будил бы /health по адресу, которого у
    этого провайдера нет, — и делал бы это при каждом запуске Джони.
    """
    _no_audio(monkeypatch)
    monkeypatch.setattr(
        speaker, "_start_local_warm_up", lambda *a, **kw: pytest.fail("прогрев без локального TTS")
    )
    speaker.make_speaker(_settings(**overrides), {"fish_api_key": "k"})


def test_warm_up_runs_in_background_daemon_thread(monkeypatch):
    """Прогрев уходит в поток-демон и получает адрес с токеном.

    Демон, а не join: health ждёт ответа до минуты, и запуск Джони не должен
    упираться в спящее облако — а если оно так и не ответит, демон не задержит
    и выход.
    """
    import types

    import johnny.tts_local as tts_local

    seen = {}
    monkeypatch.setattr(
        tts_local, "warm_up", lambda url, token="": seen.update(url=url, token=token) or True
    )
    spawned = {}

    class FakeThread:
        def __init__(self, target, daemon=False, name=""):
            spawned.update(daemon=daemon, name=name)
            self._target = target

        def start(self):
            spawned["started"] = True
            self._target()

    # Подменяем ССЫЛКУ на threading внутри speaker, а не сам модуль: настоящий
    # threading нужен остальным тестам живым.
    monkeypatch.setattr(speaker, "threading", types.SimpleNamespace(Thread=FakeThread))
    speaker._start_local_warm_up("http://127.0.0.1:8765", "secret")

    assert spawned["started"] is True
    assert spawned["daemon"] is True
    assert seen == {"url": "http://127.0.0.1:8765", "token": "secret"}


def test_edge_provider_does_not_reach_cloud_at_all(monkeypatch):
    import johnny.tts_fish as tts_fish
    import johnny.tts_local as tts_local

    _no_audio(monkeypatch)
    monkeypatch.setattr(tts_fish, "make_fish_tts", lambda *a, **kw: pytest.fail("провайдер edge"))
    monkeypatch.setattr(tts_local, "make_local_tts", lambda *a, **kw: pytest.fail("провайдер edge"))
    speaker.make_speaker(
        _settings(tts_provider="edge", tts_local_url="http://x", fish_model_id="abc"),
        {"fish_api_key": "k"},
    )


def test_make_speaker_off_mode_has_no_tts(monkeypatch):
    _no_audio(monkeypatch)
    sp = speaker.make_speaker(_settings(response_mode="off"), {})
    assert sp.mode == "off"
    assert sp._tts is None


def test_make_beep_scales_amplitude_by_volume(monkeypatch):
    import numpy as np
    import sounddevice as sd

    played = {}
    monkeypatch.setattr(sd, "play", lambda wave, sr: played.update(wave=wave))
    monkeypatch.setattr(sd, "wait", lambda: None)

    speaker._make_beep(1.0)()
    peak_full = float(np.max(np.abs(played["wave"])))

    speaker._make_beep(0.5)()
    peak_half = float(np.max(np.abs(played["wave"])))

    assert peak_half == pytest.approx(peak_full * 0.5, rel=1e-6)


def test_make_speaker_applies_tts_volume_to_beep(monkeypatch):
    seen = {}
    monkeypatch.setattr(speaker, "_make_tts", lambda volume=1.0: (lambda text: None))
    monkeypatch.setattr(
        speaker, "_make_beep", lambda volume=1.0: seen.setdefault("beep_volume", volume) or (lambda: None)
    )
    speaker.make_speaker(_settings(tts_volume=0.6), {})
    assert seen["beep_volume"] == 0.6


def test_make_speaker_falls_back_when_audio_dependencies_missing(monkeypatch):
    import importlib

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name in {"sounddevice", "numpy", "pyttsx3"}:
            raise ModuleNotFoundError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    importlib.reload(speaker)
    sp = speaker.make_speaker(_settings(), {})
    assert sp.mode == "voice"
    assert sp._tts is not None
    assert sp._beep is not None
    importlib.reload(speaker)
