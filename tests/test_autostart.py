import johnny.autostart as autostart


def _make_startup(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    startup = tmp_path / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    startup.mkdir(parents=True)
    return startup


def test_shortcut_lives_in_startup_dir(tmp_path, monkeypatch):
    startup = _make_startup(tmp_path, monkeypatch)
    assert autostart.shortcut_path().parent == startup
    assert autostart.is_installed() is False


def test_uninstall_removes_shortcut(tmp_path, monkeypatch):
    _make_startup(tmp_path, monkeypatch)
    autostart.shortcut_path().write_text("dummy", encoding="utf-8")
    assert autostart.is_installed() is True
    assert autostart.uninstall() is True
    assert autostart.is_installed() is False


def test_uninstall_when_absent_returns_false(tmp_path, monkeypatch):
    _make_startup(tmp_path, monkeypatch)
    assert autostart.uninstall() is False
