import johnny.windows as windows

# Две области как у пользователя: слева 2560x1440, справа 1920x1080.
MONITORS = [(0, 0, 2560, 1440), (2560, 209, 4480, 1289)]


def test_pick_monitor_by_index():
    assert windows.pick_monitor(MONITORS, 1) == (0, 0, 2560, 1440)
    assert windows.pick_monitor(MONITORS, 2) == (2560, 209, 4480, 1289)


def test_pick_monitor_out_of_range_returns_none():
    assert windows.pick_monitor(MONITORS, 3) is None
    assert windows.pick_monitor(MONITORS, 0) is None
    assert windows.pick_monitor([], 1) is None


def test_move_window_positions_on_target_monitor(monkeypatch):
    calls = {"move": None, "shows": []}

    fake_gui = type("G", (), {})()
    fake_gui.SW_RESTORE = 9
    fake_gui.SW_MAXIMIZE = 3
    fake_gui.ShowWindow = lambda hwnd, cmd: calls["shows"].append(cmd)
    fake_gui.MoveWindow = lambda hwnd, x, y, w, h, repaint: calls.update(move=(x, y, w, h))

    monkeypatch.setattr(windows, "win32gui", fake_gui)
    monkeypatch.setattr(windows, "win32con", fake_gui)

    ok = windows.move_window_to_monitor(hwnd=123, monitors=MONITORS, index=2)
    assert ok is True
    x, y, _w, _h = calls["move"]
    # окно должно оказаться в пределах второго монитора (X>=2560)
    assert x >= 2560 and y >= 209
    # сначала восстановить, в конце развернуть
    assert calls["shows"][0] == fake_gui.SW_RESTORE
    assert calls["shows"][-1] == fake_gui.SW_MAXIMIZE


def test_move_window_bad_index_returns_false(monkeypatch):
    monkeypatch.setattr(windows, "win32gui", type("G", (), {})())
    assert windows.move_window_to_monitor(hwnd=123, monitors=MONITORS, index=5) is False


def test_move_window_no_hwnd_returns_false():
    assert windows.move_window_to_monitor(hwnd=0, monitors=MONITORS, index=1) is False


def test_wait_for_new_window_returns_appeared(monkeypatch):
    snapshots = iter([{1, 2}, {1, 2}, {1, 2, 9}])
    monkeypatch.setattr(windows, "visible_windows", lambda: next(snapshots))
    monkeypatch.setattr(windows.time, "sleep", lambda s: None)
    assert windows.wait_for_new_window({1, 2}, timeout=10, poll=0.01) == 9


def test_wait_for_new_window_timeout_returns_none(monkeypatch):
    monkeypatch.setattr(windows, "visible_windows", lambda: {1, 2})
    monkeypatch.setattr(windows.time, "sleep", lambda s: None)
    clock = iter([0.0, 1.0, 2.0, 99.0])
    monkeypatch.setattr(windows.time, "monotonic", lambda: next(clock))
    assert windows.wait_for_new_window({1, 2}, timeout=5, poll=0.01) is None


class _FakeProcessWindows:
    """Подделка EnumWindows/GetWindowThreadProcessId/GetModuleFileNameEx:
    hwnd 1->chrome.exe, hwnd 2->obs64.exe, hwnd 3->невидимое, hwnd 4->без
    доступа к процессу (имитирует защищённый/чужой процесс)."""

    _EXE_BY_HWND = {1: "C:/Program Files/Google/Chrome/Application/chrome.exe", 2: "C:/obs/obs64.exe"}

    def IsWindowVisible(self, hwnd):
        return hwnd != 3

    def GetWindowText(self, hwnd):
        return "" if hwnd == 3 else f"title{hwnd}"

    def EnumWindows(self, cb, extra):
        for hwnd in (1, 2, 3, 4):
            cb(hwnd, extra)


def test_find_window_by_exe_matches_ignoring_path_and_case(monkeypatch):
    fake_gui = _FakeProcessWindows()
    fake_process = type("P", (), {})()
    fake_process.GetWindowThreadProcessId = lambda hwnd: (0, hwnd)

    def fake_get_module(handle, flag):
        if handle == 4:
            raise OSError("access denied")
        return _FakeProcessWindows._EXE_BY_HWND[handle]

    fake_process.GetModuleFileNameEx = fake_get_module

    fake_api = type("A", (), {})()
    fake_api.OpenProcess = lambda flags, inherit, pid: pid
    fake_api.CloseHandle = lambda handle: None

    monkeypatch.setattr(windows, "win32gui", fake_gui)
    monkeypatch.setattr(windows, "win32process", fake_process)
    monkeypatch.setattr(windows, "win32api", fake_api)

    assert windows.find_window_by_exe("OBS64.EXE") == 2  # регистр не важен
    assert windows.find_window_by_exe("chrome.exe") == 1


def test_find_window_by_exe_returns_none_when_not_running(monkeypatch):
    fake_gui = _FakeProcessWindows()
    fake_process = type("P", (), {})()
    fake_process.GetWindowThreadProcessId = lambda hwnd: (0, hwnd)
    fake_process.GetModuleFileNameEx = lambda handle, flag: _FakeProcessWindows._EXE_BY_HWND.get(
        handle
    ) or (_ for _ in ()).throw(OSError("no access"))

    fake_api = type("A", (), {})()
    fake_api.OpenProcess = lambda flags, inherit, pid: pid
    fake_api.CloseHandle = lambda handle: None

    monkeypatch.setattr(windows, "win32gui", fake_gui)
    monkeypatch.setattr(windows, "win32process", fake_process)
    monkeypatch.setattr(windows, "win32api", fake_api)

    assert windows.find_window_by_exe("telegram.exe") is None
