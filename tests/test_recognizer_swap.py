"""Подмена модели Whisper на ходу: она же экономия видеопамяти во время игры."""
import threading

import johnny.recognizer as recognizer_module
from johnny.recognizer import Recognizer


class FakeWhisper:
    """Считает, сколько раз её создавали и с какими аргументами."""

    created: list[tuple[str, str, str]] = []

    def __init__(self, model, device, compute_type):
        FakeWhisper.created.append((model, device, compute_type))
        self.model = model

    def transcribe(self, audio, **kwargs):
        return iter(()), None


def _fresh(monkeypatch):
    FakeWhisper.created = []
    monkeypatch.setattr(recognizer_module, "WhisperModel", FakeWhisper)
    return Recognizer("medium", "cuda", "")


def test_use_model_меняет_модель(monkeypatch):
    recognizer = _fresh(monkeypatch)

    changed = recognizer.use_model("small")

    assert changed is True
    assert recognizer.model_name == "small"
    assert recognizer._model.model == "small"


def test_use_model_на_ту_же_модель_ничего_не_делает(monkeypatch):
    """Сторож опрашивает признак раз в несколько секунд и почти всегда видит
    то же самое. Перезагружать модель на каждый опрос — 2.4 секунды впустую и
    выброшенная из памяти рабочая модель."""
    recognizer = _fresh(monkeypatch)
    было = len(FakeWhisper.created)

    changed = recognizer.use_model("medium")

    assert changed is False
    assert len(FakeWhisper.created) == было


def test_старая_модель_отпускается_до_загрузки_новой(monkeypatch):
    """Смысл подмены — освободить видеопамять. Если обе модели полежат на карте
    одновременно, экономии не будет вовсе, будет перерасход.

    Проверяем порядок напрямую: в момент, когда создаётся новая модель, поле
    _model обязано быть уже пустым. Через __del__ и сборщик мусора это же
    свойство проверялось бы недетерминированно."""
    recognizer = _fresh(monkeypatch)
    состояние_при_загрузке = []

    class Наблюдаемая(FakeWhisper):
        def __init__(self, model, device, compute_type):
            состояние_при_загрузке.append(recognizer._model)
            super().__init__(model, device, compute_type)

    monkeypatch.setattr(recognizer_module, "WhisperModel", Наблюдаемая)
    recognizer.use_model("small")

    assert состояние_при_загрузке == [None]


def test_если_новая_модель_не_поднялась_имя_не_меняется(monkeypatch):
    """Игра успела забрать всю память. Соврать про то, какая модель стоит,
    нельзя: сторож сравнивает model_name с желаемым и на вранье начнёт
    дёргать подмену каждый опрос."""
    recognizer = _fresh(monkeypatch)

    class Отказ:
        def __init__(self, model, device, compute_type):
            raise RuntimeError("нет памяти")

    monkeypatch.setattr(recognizer_module, "WhisperModel", Отказ)
    changed = recognizer.use_model("small")

    assert changed is False
    assert recognizer.model_name == "medium"


def test_расшифровка_и_подмена_делят_один_замок(monkeypatch):
    """transcribe работает в потоке контроллера, use_model — в потоке сторожа.
    Выдернуть модель из-под работающей расшифровки значит проглотить команду."""
    recognizer = _fresh(monkeypatch)

    assert isinstance(recognizer._lock, type(threading.RLock()))
