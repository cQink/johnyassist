"""Подмена модели Whisper на ходу: она же экономия видеопамяти во время игры."""
import threading
import time

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
    # Двойной отказ (не поднялась ни новая модель, ни прежняя) обязан
    # погасить available — именно на этот флаг опирается проверка в
    # game_watch.Guard.tick, решающая, звать ли use_model повторно.
    assert recognizer.available is False


def test_load_без_faster_whisper_возвращает_none_а_не_падает(monkeypatch):
    """Находка 3 ревью: __init__ проверяет WhisperModel is None и выходит
    рано, но _load зовётся ещё и из use_model — сторож дёргает его на каждом
    опросе. Без этой же проверки внутри _load каждый рунг лесенки падал бы
    на TypeError (WhisperModel is None, значит None(...)), и со сторожем это
    были бы восемь бессмысленных предупреждений в лог каждые 5 секунд
    навсегда."""
    recognizer = _fresh(monkeypatch)
    monkeypatch.setattr(recognizer_module, "WhisperModel", None)

    assert recognizer._load("medium", "cuda") is None


def test_расшифровка_ждёт_подмену_а_не_теряет_команду(monkeypatch):
    """Настоящая проверка замка (находка 1 ревью), а не только его типа.

    Замок вводился ради того, чтобы транскрибирование не выдёргивало модель
    из-под use_model — и это направление уже работает. Но пока use_model
    держит замок (сброс модели, gc.collect(), загрузка новой — реально это
    около 2.4 секунды), self._model временно None. Если проверка
    `self._model is None` в transcribe стоит СНАРУЖИ замка, расшифровка,
    пришедшая ровно в этот момент, увидит пустой self._model и молча вернёт
    "" вместо того, чтобы подождать новую модель на замке — то есть
    проглотит команду ровно в момент, ради защиты которого замок и
    заводился.

    Проверяем это не типом объекта, а гонкой: искусственно растягиваем
    загрузку новой модели событием, ловим transcribe ровно в этом окне и
    смотрим, дождалась она конца подмены или проскочила мимо замка.
    """
    recognizer = _fresh(monkeypatch)

    можно_грузить = threading.Event()

    class МедленнаяЗагрузка(FakeWhisper):
        """__init__ не возвращается, пока тест не разрешит — имитация
        реальной загрузки модели, которая идёт секунды, а не микросекунды."""

        def __init__(self, model, device, compute_type):
            можно_грузить.wait(2.0)
            super().__init__(model, device, compute_type)

        def transcribe(self, audio, **kwargs):
            # Метим результат именем модели, чтобы отличить «дождалась новой
            # модели» от «вернула пустоту, проскочив мимо замка».
            Сегмент = type("Сегмент", (), {"text": self.model, "avg_logprob": -0.1})
            return iter([Сегмент()]), None

    monkeypatch.setattr(recognizer_module, "WhisperModel", МедленнаяЗагрузка)

    поток_подмены = threading.Thread(target=recognizer.use_model, args=("small",))
    поток_подмены.start()

    # Дождаться, что подмена реально вошла в критический участок и обнулила
    # модель — иначе расшифровка может стартовать раньше и тест ничего не
    # проверит.
    предел = time.monotonic() + 2.0
    while recognizer._model is not None and time.monotonic() < предел:
        time.sleep(0.005)
    assert recognizer._model is None, "подмена не успела войти в критический участок"

    готово = threading.Event()
    результат = {}

    def расшифровать():
        результат["текст"] = recognizer.transcribe(object())
        готово.set()

    threading.Thread(target=расшифровать, daemon=True).start()

    # Событие ещё не должно быть взведено: если transcribe уже вернулась —
    # она проскочила мимо замка вместо ожидания, и в реальности это была бы
    # проглоченная команда.
    assert готово.wait(0.3) is False, "transcribe вернулась до конца подмены — команда была бы потеряна"

    можно_грузить.set()
    поток_подмены.join(2.0)
    assert готово.wait(2.0) is True, "transcribe не дождалась конца подмены"

    assert результат["текст"] == "small"  # отработала уже на новой модели
    assert recognizer.model_name == "small"
