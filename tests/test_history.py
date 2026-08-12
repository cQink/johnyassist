import johnny.history as history


def test_add_writes_one_line_with_text(tmp_path, monkeypatch):
    f = tmp_path / "history.log"
    monkeypatch.setattr(history, "_HISTORY_FILE", f)
    history.add("открой ютуб")
    content = f.read_text(encoding="utf-8")
    assert "открой ютуб" in content
    assert content.count("\n") == 1


def test_add_marks_empty_recognition(tmp_path, monkeypatch):
    f = tmp_path / "history.log"
    monkeypatch.setattr(history, "_HISTORY_FILE", f)
    history.add("   ")
    assert "(пусто)" in f.read_text(encoding="utf-8")


def test_add_appends(tmp_path, monkeypatch):
    f = tmp_path / "history.log"
    monkeypatch.setattr(history, "_HISTORY_FILE", f)
    history.add("раз")
    history.add("два")
    assert f.read_text(encoding="utf-8").count("\n") == 2


def test_add_writes_text_and_via(tmp_path, monkeypatch):
    log = tmp_path / "history.log"
    monkeypatch.setattr(history, "_HISTORY_FILE", log)
    history.add("громкость 5", "похоже (0.74)")
    line = log.read_text(encoding="utf-8")
    assert "громкость 5" in line
    assert "похоже (0.74)" in line


def test_add_without_via_is_backwards_compatible(tmp_path, monkeypatch):
    log = tmp_path / "history.log"
    monkeypatch.setattr(history, "_HISTORY_FILE", log)
    history.add("открой ютуб")
    assert "открой ютуб" in log.read_text(encoding="utf-8")


def test_empty_text_is_marked(tmp_path, monkeypatch):
    log = tmp_path / "history.log"
    monkeypatch.setattr(history, "_HISTORY_FILE", log)
    history.add("")
    assert "(пусто)" in log.read_text(encoding="utf-8")
