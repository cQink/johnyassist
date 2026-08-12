import johnny.history_viewer as history_viewer


def test_read_new_tail_returns_full_content_first_time(tmp_path):
    f = tmp_path / "history.log"
    f.write_text("строка1\n", encoding="utf-8")
    content, size = history_viewer._read_new_tail(f, 0)
    assert content == "строка1\n"
    assert size == f.stat().st_size


def test_read_new_tail_returns_only_appended_part(tmp_path):
    f = tmp_path / "history.log"
    f.write_text("строка1\n", encoding="utf-8")
    _, size1 = history_viewer._read_new_tail(f, 0)
    with open(f, "a", encoding="utf-8") as fh:
        fh.write("строка2\n")
    content, size2 = history_viewer._read_new_tail(f, size1)
    assert content == "строка2\n"
    assert size2 > size1


def test_read_new_tail_missing_file_returns_empty():
    from pathlib import Path

    content, size = history_viewer._read_new_tail(Path("nonexistent-file.log"), 0)
    assert content == ""
    assert size == 0


def test_read_new_tail_no_change_returns_empty_and_same_size(tmp_path):
    f = tmp_path / "history.log"
    f.write_text("строка1\n", encoding="utf-8")
    _, size1 = history_viewer._read_new_tail(f, 0)
    content, size2 = history_viewer._read_new_tail(f, size1)
    assert content == ""
    assert size2 == size1


def test_open_or_focus_brings_existing_window_to_front(monkeypatch):
    from pathlib import Path

    calls = []
    fake_gui = type(
        "FakeGui",
        (),
        {
            "FindWindow": staticmethod(lambda cls, title: 42),
            "IsIconic": staticmethod(lambda hwnd: False),
            "SetForegroundWindow": staticmethod(lambda hwnd: calls.append(("front", hwnd))),
            "ShowWindow": staticmethod(lambda hwnd, flag: calls.append(("show", hwnd, flag))),
        },
    )()
    monkeypatch.setattr(history_viewer, "win32gui", fake_gui)
    started = []
    monkeypatch.setattr(history_viewer.threading, "Thread", lambda *a, **k: started.append(True))

    history_viewer.open_or_focus(Path("history.log"))

    assert ("front", 42) in calls
    assert started == [], "окно уже есть — новое открывать не нужно"


def test_open_or_focus_restores_minimized_window(monkeypatch):
    from pathlib import Path

    calls = []
    fake_gui = type(
        "FakeGui",
        (),
        {
            "FindWindow": staticmethod(lambda cls, title: 42),
            "IsIconic": staticmethod(lambda hwnd: True),
            "SetForegroundWindow": staticmethod(lambda hwnd: calls.append(("front", hwnd))),
            "ShowWindow": staticmethod(lambda hwnd, flag: calls.append(("show", hwnd, flag))),
        },
    )()
    fake_con = type("FakeCon", (), {"SW_RESTORE": 9})()
    monkeypatch.setattr(history_viewer, "win32gui", fake_gui)
    monkeypatch.setattr(history_viewer, "win32con", fake_con)
    monkeypatch.setattr(history_viewer.threading, "Thread", lambda *a, **k: None)

    history_viewer.open_or_focus(Path("history.log"))

    assert ("show", 42, 9) in calls


def test_open_or_focus_spawns_new_window_when_none_exists(monkeypatch):
    from pathlib import Path

    fake_gui = type("FakeGui", (), {"FindWindow": staticmethod(lambda cls, title: 0)})()
    monkeypatch.setattr(history_viewer, "win32gui", fake_gui)

    class FakeThread:
        def __init__(self, target, args, daemon):
            self.target, self.args, self.daemon = target, args, daemon
            self.started = False

        def start(self):
            self.started = True

    created = {}

    def fake_thread_ctor(target, args, daemon):
        created["thread"] = FakeThread(target, args, daemon)
        return created["thread"]

    monkeypatch.setattr(history_viewer.threading, "Thread", fake_thread_ctor)

    history_viewer.open_or_focus(Path("history.log"))

    assert created["thread"].started is True
    assert created["thread"].daemon is True
