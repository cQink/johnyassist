import johnny.recognizer as recognizer_module
from johnny.recognizer import Recognizer


class FakeSegment:
    def __init__(self, text, avg_logprob):
        self.text = text
        self.avg_logprob = avg_logprob


class FakeModel:
    def __init__(self, segments):
        self.segments = segments

    def transcribe(self, audio, **kwargs):
        return iter(self.segments), None


def _recognizer(segments):
    recognizer = Recognizer.__new__(Recognizer)   # без загрузки настоящей модели
    recognizer._model = FakeModel(segments)
    recognizer._prompt = "Джони. Русские голосовые команды."
    recognizer.last_confidence = 0.0
    return recognizer


def test_запоминает_худшую_уверенность():
    recognizer = _recognizer([FakeSegment("привет", -0.2), FakeSegment("гоше", -0.9)])

    recognizer.transcribe(None)

    assert recognizer.last_confidence == -0.9


def test_без_сегментов_уверенность_нулевая():
    recognizer = _recognizer([])

    assert recognizer.transcribe(None) == ""
    assert recognizer.last_confidence == 0.0


def test_эхо_подсказки_сбрасывает_уверенность():
    """Отброшенный текст не должен оставлять за собой свою уверенность.

    Собственную подсказку Whisper продолжает уверенно, поэтому без сброса в
    историю попадала бы пустая строка с хорошей уверенностью — и портила бы
    ровно тот набор данных, ради которого уверенность и пишется.
    """
    recognizer = _recognizer([FakeSegment("Джони. Русские голосовые команды.", -0.1)])

    assert recognizer.transcribe(None) == ""
    assert recognizer.last_confidence == 0.0


def test_recognizer_can_be_created_without_whisper(monkeypatch):
    monkeypatch.setattr(recognizer_module, "WhisperModel", None)
    monkeypatch.setattr(recognizer_module, "_WHISPER_IMPORT_ERROR", RuntimeError("no dll"))

    recognizer = Recognizer("small", "cpu", "")

    assert recognizer._model is None
    assert recognizer.transcribe(None) == ""


def test_recognizer_handles_model_initialization_failure(monkeypatch):
    class FakeModel:
        def __init__(self, model, device=None, compute_type=None):
            raise RuntimeError("init failed")

    monkeypatch.setattr(recognizer_module, "WhisperModel", FakeModel)
    recognizer = Recognizer("small", "cpu", "")

    assert recognizer._model is None
    assert recognizer.transcribe(None) == ""
    assert recognizer.available is False


def test_recognizer_falls_back_from_float16_to_float32(monkeypatch):
    calls = []

    class FakeModel:
        def __init__(self, model, device=None, compute_type=None):
            calls.append((device, compute_type))
            if device == "cuda" and compute_type == "float16":
                raise RuntimeError("Requested float16 compute type")

    monkeypatch.setattr(recognizer_module, "WhisperModel", FakeModel)
    recognizer = Recognizer("medium", "cuda", "")

    assert recognizer._model is not None
    assert recognizer.available is True
    assert calls[0] == ("cuda", "float16")
    assert calls[1] == ("cuda", "float32")
