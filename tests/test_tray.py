"""Только один сценарий: справочник людей обязан доезжать до build_vocabulary
в tray.main() — точке входа для запуска из трея (johnny_tray.pyw). Без этого
теста из main() можно было бы убрать config.people из вызова
build_vocabulary, и весь набор тестов остался бы зелёным, а Whisper при
запуске из трея (обычный способ запуска Джони) переставал бы слышать имена
людей в словаре подсказки. Тот же класс дефекта, что уже чинили для execute()
и для johnny/app.py::run() (см. tests/test_app.py).

Тяжёлые части (микрофон, распознаватель, слушатель, контроллер, иконка в
трее) подменены фейками — реальный трей и настоящий цикл прослушивания здесь
не участвуют.
"""

PEOPLE = {"гоша": {"discord": "Гречка", "username": "ne_godjaj", "aliases": ["гоша"]}}


class _FakeIcon:
    def __init__(self, *args, **kwargs):
        self.menu = None
        self.icon = None

    def run(self):
        # Настоящий icon.run() запускает бесконечный цикл трея — в тесте он
        # не нужен, важно лишь то, что произошло ДО этого вызова.
        return None

    def stop(self):
        pass


class _FakeMicrophone:
    def open(self):
        return self

    def close(self):
        pass


class _FakeListener:
    def close(self):
        pass


class _FakeController:
    def __init__(self, config, speaker):
        self.paused = False
        self.last_command = None

    def run(self, listener, recognizer, mic):
        pass

    def stop(self):
        pass

    def pause(self):
        pass

    def resume(self):
        pass


def test_build_vocabulary_получает_людей_в_main(monkeypatch):
    import johnny.audio as audio_module
    import johnny.controller as controller_module
    import johnny.listener as listener_module
    import johnny.recognizer as recognizer_module
    import johnny.tray as tray
    from johnny.config import Config, Settings

    config = Config(
        apps={},
        commands=[],
        settings=Settings("джони", "", "off", "medium", "cuda"),
        people=PEOPLE,
    )
    seen = {}

    def fake_build_vocabulary(apps, channels, commands, people):
        seen["people"] = people
        return "vocab"

    # _setup_logging пишет в реальный johnny.log и переключает sys.stdout/
    # stderr на файл — в тесте это не нужно и опасно (потерялся бы вывод).
    monkeypatch.setattr(tray, "_setup_logging", lambda: None)
    monkeypatch.setattr(tray.single_instance, "acquire", lambda *a, **k: True)
    monkeypatch.setattr(tray, "load_config", lambda config_dir: config)
    monkeypatch.setattr(tray, "make_speaker", lambda settings, secrets: object())
    monkeypatch.setattr(tray, "AssistantController", _FakeController)
    monkeypatch.setattr(tray.pystray, "Icon", lambda *a, **k: _FakeIcon())
    monkeypatch.setattr(recognizer_module, "build_vocabulary", fake_build_vocabulary)
    monkeypatch.setattr(recognizer_module, "Recognizer", lambda *a, **k: object())
    monkeypatch.setattr(listener_module, "Listener", lambda *a, **k: _FakeListener())
    monkeypatch.setattr(audio_module, "Microphone", lambda: _FakeMicrophone())

    tray.main()

    assert seen["people"] == PEOPLE, "справочник людей не доехал до build_vocabulary в main()"
