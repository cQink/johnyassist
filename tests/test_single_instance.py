import johnny.single_instance as single_instance


class _FakeWin32Event:
    def __init__(self, already_exists: bool):
        self._already_exists = already_exists
        self.created_with = None

    def CreateMutex(self, security, initial_owner, name):
        self.created_with = (security, initial_owner, name)
        return "handle"


class _FakeWin32Api:
    def __init__(self, already_exists: bool):
        self._already_exists = already_exists
        self.closed = []

    def GetLastError(self):
        return 183 if self._already_exists else 0  # ERROR_ALREADY_EXISTS = 183

    def CloseHandle(self, handle):
        self.closed.append(handle)


def test_acquire_true_when_first_instance(monkeypatch):
    fake_event = _FakeWin32Event(already_exists=False)
    fake_api = _FakeWin32Api(already_exists=False)
    monkeypatch.setattr(single_instance, "win32event", fake_event)
    monkeypatch.setattr(single_instance, "win32api", fake_api)
    monkeypatch.setattr(single_instance, "winerror", type("W", (), {"ERROR_ALREADY_EXISTS": 183}))

    assert single_instance.acquire("TestMutex") is True
    assert fake_event.created_with == (None, False, "TestMutex")
    assert fake_api.closed == []  # хендл не закрыт — держим его живым


def test_acquire_false_when_already_running(monkeypatch):
    fake_event = _FakeWin32Event(already_exists=True)
    fake_api = _FakeWin32Api(already_exists=True)
    monkeypatch.setattr(single_instance, "win32event", fake_event)
    monkeypatch.setattr(single_instance, "win32api", fake_api)
    monkeypatch.setattr(single_instance, "winerror", type("W", (), {"ERROR_ALREADY_EXISTS": 183}))

    assert single_instance.acquire("TestMutex") is False
    assert fake_api.closed == ["handle"]  # ненужный хендл закрыт сразу
