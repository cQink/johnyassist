import pytest

import johnny.sounds as sounds


class FakeWinmm:
    """Подменяет ctypes.windll.winmm: отдаёт заданные коды возврата по очереди
    и запоминает поданные команды, настоящий звук не проигрывается.

    "status ... mode" обрабатывается отдельно от остальных команд: пишет
    строку в буфер (как настоящий MCI) и НЕ тратит элемент из `results` —
    иначе каждому тесту пришлось бы заранее знать, сколько раз опросится
    статус."""

    def __init__(self, results, status_responses=None):
        self._results = list(results)
        self.commands = []
        self._status_responses = list(status_responses) if status_responses else ["stopped"]

    def mciSendStringW(self, command, buffer, buffer_size, hwnd):
        self.commands.append(command)
        if command.startswith("status "):
            value = self._status_responses.pop(0) if len(self._status_responses) > 1 else self._status_responses[0]
            if buffer is not None:
                buffer.value = value
            return 0
        return self._results.pop(0)


def test_pick_returns_mp3_and_ignores_m4a(tmp_path, monkeypatch):
    d = tmp_path / "wakeup"
    d.mkdir()
    (d / "a.mp3").write_text("x")
    (d / "b.m4a").write_text("x")
    monkeypatch.setattr(sounds, "_SIGNALS_DIR", tmp_path)
    picked = sounds._pick("wakeup")
    assert picked is not None and picked.suffix == ".mp3"


def test_pick_empty_folder_returns_none(tmp_path, monkeypatch):
    (tmp_path / "answer").mkdir()
    monkeypatch.setattr(sounds, "_SIGNALS_DIR", tmp_path)
    assert sounds._pick("answer") is None


def test_pick_missing_folder_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(sounds, "_SIGNALS_DIR", tmp_path)
    assert sounds._pick("nope") is None


def test_play_random_plays_existing_file(tmp_path, monkeypatch):
    d = tmp_path / "wakeup"
    d.mkdir()
    (d / "a.mp3").write_text("x")
    monkeypatch.setattr(sounds, "_SIGNALS_DIR", tmp_path)
    played = {}
    monkeypatch.setattr(sounds, "_play_mci", lambda p: played.setdefault("p", p))
    assert sounds.play_random("wakeup") is True
    assert played["p"].endswith("a.mp3")


def test_play_random_no_file_returns_false(tmp_path, monkeypatch):
    (tmp_path / "wakeup").mkdir()
    monkeypatch.setattr(sounds, "_SIGNALS_DIR", tmp_path)
    assert sounds.play_random("wakeup") is False


def test_play_mci_success_calls_open_play_close_in_order(monkeypatch):
    fake = FakeWinmm([0, 0, 0])  # open, play, close — статус не тратит results
    monkeypatch.setattr(sounds.ctypes.windll, "winmm", fake)
    sounds._play_mci("a.mp3")
    assert fake.commands[0].startswith("open ")
    assert fake.commands[1].startswith("play ")
    assert fake.commands[-1].startswith("close ")
    assert any(c.startswith("status ") for c in fake.commands), (
        "play теперь БЕЗ 'wait' — статус обязан опрашиваться, иначе close "
        "случится до реального конца воспроизведения"
    )


def test_play_mci_raises_oserror_when_open_fails(monkeypatch):
    fake = FakeWinmm([1])   # ненулевой код = ошибка
    monkeypatch.setattr(sounds.ctypes.windll, "winmm", fake)
    with pytest.raises(OSError):
        sounds._play_mci("a.mp3")
    assert len(fake.commands) == 1   # play/close даже не пытались — открыть не удалось


def test_play_mci_raises_oserror_when_play_fails_but_still_closes(monkeypatch):
    fake = FakeWinmm([0, 1, 0])   # open ок, play — ошибка, close вызван всё равно
    monkeypatch.setattr(sounds.ctypes.windll, "winmm", fake)
    with pytest.raises(OSError):
        sounds._play_mci("a.mp3")
    assert len(fake.commands) == 3
    assert fake.commands[2].startswith("close ")


def test_play_random_returns_false_when_playback_fails(tmp_path, monkeypatch):
    d = tmp_path / "wakeup"
    d.mkdir()
    (d / "a.mp3").write_text("x")
    monkeypatch.setattr(sounds, "_SIGNALS_DIR", tmp_path)

    def boom(path):
        raise OSError("MCI отказал")

    monkeypatch.setattr(sounds, "_play_mci", boom)
    assert sounds.play_random("wakeup") is False   # не врёт True при неудаче


def test_play_file_propagates_playback_error(monkeypatch):
    def boom(path):
        raise OSError("MCI отказал")

    monkeypatch.setattr(sounds, "_play_mci", boom)
    with pytest.raises(OSError):
        sounds.play_file("a.mp3")


def test_default_volume_sends_no_setaudio_command(monkeypatch):
    """Громкость 1.0 (по умолчанию) — поведение как раньше, без лишней команды."""
    fake = FakeWinmm([0, 0, 0])
    monkeypatch.setattr(sounds.ctypes.windll, "winmm", fake)
    sounds._play_mci("a.mp3")
    assert not any("setaudio" in c for c in fake.commands)


def test_reduced_volume_sends_setaudio_before_play(monkeypatch):
    monkeypatch.setattr(sounds, "_VOLUME", 0.6)
    fake = FakeWinmm([0, 0, 0, 0])
    monkeypatch.setattr(sounds.ctypes.windll, "winmm", fake)
    sounds._play_mci("a.mp3")
    assert fake.commands[0].startswith("open ")
    assert "setaudio" in fake.commands[1] and "volume to 600" in fake.commands[1]
    assert fake.commands[2].startswith("play ")
    assert fake.commands[-1].startswith("close ")


def test_set_volume_clamps_to_valid_range(monkeypatch):
    monkeypatch.setattr(sounds, "_VOLUME", 1.0)  # monkeypatch восстановит после теста
    sounds.set_volume(5)
    assert sounds._VOLUME == 1.0
    sounds.set_volume(-1)
    assert sounds._VOLUME == 0.0
    sounds.set_volume(0.6)
    assert sounds._VOLUME == 0.6


def test_stop_all_does_nothing_when_nothing_plays(monkeypatch):
    fake = FakeWinmm([])
    monkeypatch.setattr(sounds.ctypes.windll, "winmm", fake)
    sounds.stop_all()  # не должно упасть и не должно ничего слать
    assert fake.commands == []


def test_stop_all_marks_alias_without_sending_mci_command(monkeypatch):
    """ЖИВОЙ БАГ (2026-08-03): пользователь сказал «Джони, стоп» трижды, пока
    Джони рассказывал анекдот — все три раза лог подтвердил совпадение
    («стоп(перебил)»), но звук ни разу не остановился. Причина: MCI на этой
    машине игнорирует "stop {alias}", посланный ИЗ ДРУГОГО потока, пока в том
    же алиасе блокирующе крутится "play ... wait" — команда просто теряется
    (недокументированная, но известная особенность MCI: он надёжен, только
    когда команды алиасу подаёт ОДИН И ТОТ ЖЕ поток, что его открыл).

    Поэтому stop_all() САМ больше ничего не шлёт winmm — только помечает
    alias. Стоп реально шлёт поток, который играет (см. _wait_until_stopped),
    из СВОЕГО потока, где MCI это уважает."""
    fake = FakeWinmm([])
    monkeypatch.setattr(sounds.ctypes.windll, "winmm", fake)
    monkeypatch.setattr(sounds, "_current_alias", "jsnd42")
    monkeypatch.setattr(sounds, "_stopped_aliases", set())
    sounds.stop_all()
    assert fake.commands == []
    assert sounds._stopped_aliases == {"jsnd42"}


def test_wait_until_stopped_sends_stop_itself_when_alias_marked(monkeypatch):
    """Поток, который САМ играет alias, замечает пометку stop_all() и сам же
    шлёт «stop» — команда от своего потока, а не извне (см. баг выше)."""
    fake = FakeWinmm([0])  # ответ на саму команду "stop"
    monkeypatch.setattr(sounds, "_stopped_aliases", {"jsnd1"})
    sounds._wait_until_stopped(fake, "jsnd1")
    assert fake.commands == ["stop jsnd1"]


def test_wait_until_stopped_polls_status_until_playback_ends(monkeypatch):
    """Без пометки stop_all — просто ждёт естественного конца, опрашивая
    статус, а не блокируясь в MCI "wait" (который и оказался ненадёжным)."""
    fake = FakeWinmm([], status_responses=["playing", "playing", "stopped"])
    monkeypatch.setattr(sounds, "_stopped_aliases", set())
    monkeypatch.setattr(sounds, "_STATUS_POLL_SECONDS", 0)
    sounds._wait_until_stopped(fake, "jsnd1")
    assert fake.commands.count("status jsnd1 mode") == 3
    assert not any(c.startswith("stop ") for c in fake.commands)


def test_play_mci_real_playback_error_still_raises(monkeypatch):
    """Ненулевой код от play() — настоящая ошибка запуска (не хватает
    кодека, битый файл), её нельзя молча проглатывать."""
    fake = FakeWinmm([0, 1, 0])
    monkeypatch.setattr(sounds.ctypes.windll, "winmm", fake)
    with pytest.raises(OSError):
        sounds._play_mci("a.mp3")
