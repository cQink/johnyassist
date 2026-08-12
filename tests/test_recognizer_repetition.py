from johnny.recognizer import Recognizer


class FakeSegment:
    def __init__(self, text, avg_logprob=-0.2):
        self.text = text
        self.avg_logprob = avg_logprob


class FakeModel:
    def __init__(self, segments):
        self.segments = segments

    def transcribe(self, audio, **kwargs):
        return iter(self.segments), None


def _recognizer(segments):
    recognizer = Recognizer.__new__(Recognizer)  # без загрузки настоящей модели
    recognizer._model = FakeModel(segments)
    recognizer._prompt = "Джони. Русские голосовые команды. введи"
    recognizer.last_confidence = 0.0
    return recognizer


def test_декодер_зациклился_на_одном_слове_считается_провалом():
    """ЖИВОЙ БАГ (2026-08-06): «Джони, введи <текст>» вернуло "введи",
    повторённое ~60 раз, вместо надиктованного текста — классическая петля
    декодера Whisper на длинном/невнятном хвосте аудио, усугублённая тем,
    что "введи" само есть в initial_prompt (build_vocabulary включает
    ключевые слова команд) и декодер зацикливается именно на нём.
    Печатать такой мусор в чужое поле ввода нельзя — считаем расшифровку
    полностью проваленной, как уже делается для эха подсказки."""
    text = "введи " * 60 + "введи"
    recognizer = _recognizer([FakeSegment(text)])

    assert recognizer.transcribe(None) == ""
    assert recognizer.last_confidence == 0.0


def test_двойное_слово_это_естественная_речь_не_петля():
    """«очень очень устал» — законное усиление, не паразитный повтор."""
    recognizer = _recognizer([FakeSegment("я очень очень устал")])

    assert recognizer.transcribe(None) == "я очень очень устал"


def test_тройной_повтор_в_середине_фразы_тоже_провал():
    """Петля необязательно на весь текст — достаточно тройного повтора где угодно,
    остаток текста рядом с ней доверия не заслуживает."""
    recognizer = _recognizer([FakeSegment("привет привет привет как дела")])

    assert recognizer.transcribe(None) == ""
