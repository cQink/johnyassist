import difflib
import json
import logging
import re
from pathlib import Path

from . import audio

_ROOT = Path(__file__).resolve().parent.parent


def _resolve_model_path(path: str) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else (_ROOT / path)

try:
    from vosk import KaldiRecognizer, Model
except Exception:  # pragma: no cover - environment-dependent
    KaldiRecognizer = None
    Model = None

logger = logging.getLogger(__name__)

# Сколько первых слов расшифровки просматриваем в поисках имени. Дальше не
# смотрим: случайное созвучие в конце длинной фразы («включи первое видео
# джони») срезало бы всю команду целиком.
_HEAD_WORDS = 3

# Порог нечёткого сравнения с вариантами имени. Выше 0.72 (порога роутера):
# здесь сравнивается одно короткое слово целиком с известными вариантами
# имени, а роутер сравнивает длинные фразы, где литеральная часть даёт
# больше опоры. На пятибуквенном слове 0.75 — это примерно «ошибка в одну
# букву»; мягче нельзя, иначе с именем начнут совпадать обычные русские
# слова.
_NAME_RATIO = 0.75


def _words(text: str) -> list[str]:
    """Слова в нижнем регистре, ё→е, без пунктуации — как в recognizer."""
    return re.findall(r"[a-zа-я0-9]+", text.lower().replace("ё", "е"))


def strip_wake_word(text: str, variants: list[str]) -> tuple[str, bool]:
    """Убрать имя из начала слитной фразы.

    Возвращает (текст без имени, найдено ли имя). Второй элемент — тот самый
    якорь: Vosk (small) услышал имя, а Whisper (medium) на той же записи его
    не видит — значит Vosk скорее всего ошибся, и доверие к фразе надо
    понизить. Выбрасывать такое молча нельзя: Whisper мог проглотить быстрое
    тихое «Джони», а потерять настоящую команду хуже, чем выполнить лишнюю.

    Текст возвращается нормализованным (слова через пробел, нижний регистр,
    ё→е). Роутер всё равно нормализует вход, поэтому потери нет, зато история
    и логи выглядят одинаково для обеих веток.
    """
    said = _words(text)
    known = [w.lower().replace("ё", "е") for w in variants]
    cut = -1
    for index, word in enumerate(said[:_HEAD_WORDS]):
        for variant in known:
            if difflib.SequenceMatcher(None, word, variant).ratio() >= _NAME_RATIO:
                cut = index  # именно последнее совпадение в голове фразы
                break
    if cut < 0:
        return " ".join(said), False
    return " ".join(said[cut + 1 :]), True


def recover_misheard_name(text: str, commands) -> str | None:
    """Вернуть фразу без первого слова, если это ослышанное имя. Иначе None.

    Whisper НЕ теряет имя — он заменяет его чужим словом, и оно остаётся
    первым, ломая разбор. По живым логам: «не сделай громче», «желание найди
    на ютубе… и включи первое видео». Похожесть «не» на «джони» — 0.29,
    «желание» — 0.50, при том что настоящие варианты дают 0.80–0.91. Никакой
    порог `_NAME_RATIO` эти случаи не поймает, не начав совпадать с половиной
    русских слов, поэтому опора здесь другая.

    Опора — остаток фразы: если БЕЗ первого слова получается настоящая
    команда, а С ним не получается ничего, значит первое слово и было именем.
    Оба условия обязательны. Второе не менее важно первого: у «сделай громче»
    остаток («громче») тоже команда, и без проверки целой фразы Джони съедал
    бы нужные слова.
    """
    from . import chain  # лениво: chain тянет actions, а listener грузится рано

    words = _words(text)
    if len(words) < 2 or chain.understands(" ".join(words), commands):
        return None
    rest = " ".join(words[1:])
    return rest if chain.understands(rest, commands) else None


_LATIN_GARBAGE = re.compile(r"^[a-z]{1,4}$")


def recover_latin_prefix(text: str) -> str | None:
    """Первое слово — короткий латинский мусор вместо ослышанного активатора?

    Живой баг (2026-08-06, history.log): Whisper иногда пишет «Джони»
    короткими латинскими буквами («vd» и т.п.) вместо кириллицы — восемь
    живых случаев, во всех мусорный токен строго первым словом, дальше идёт
    связный русский текст. `recover_misheard_name` это не ловит: он требует,
    чтобы остаток фразы был точной командой, а тут почти всегда обычный
    разговор для модели, а не команда.
    """
    words = _words(text)
    if len(words) < 2:
        return None
    head, rest = words[0], words[1:]
    if not _LATIN_GARBAGE.match(head):
        return None
    if not any(re.search(r"[а-я]", word) for word in rest):
        return None
    return " ".join(rest)


class Listener:
    """Ловит слово-активатор через Vosk (офлайн, без ключей и регистрации)."""

    _DEFAULT_SMALL_MODEL = "models/vosk-model-small-ru-0.22"

    def __init__(self, wake_word: str, model_path: str):
        # wake_word может содержать несколько вариантов через "|"
        # (Vosk слышит «джони» как «джонни»/«джани» — принимаем все).
        self.wake_words = [w.strip() for w in wake_word.lower().split("|") if w.strip()]
        self._model = None
        self._recognizer = None
        if Model is None or KaldiRecognizer is None:
            logger.warning("Vosk недоступен, wake-word распознавание будет отключено")
            return

        tried_paths = [model_path]
        if model_path != self._DEFAULT_SMALL_MODEL:
            tried_paths.append(self._DEFAULT_SMALL_MODEL)

        for path in tried_paths:
            resolved = _resolve_model_path(path)
            if not resolved.exists():
                logger.warning(
                    "Vosk-модель %r не найдена, пробую следующий путь",
                    resolved,
                )
                continue
            try:
                if resolved != _resolve_model_path(model_path):
                    logger.warning(
                        "Пытаюсь загрузить fallback Vosk-модель %r вместо %r",
                        resolved,
                        model_path,
                    )
                self._model = Model(str(resolved))
                self._recognizer = KaldiRecognizer(self._model, 16000)
                if resolved != _resolve_model_path(model_path):
                    logger.warning("Vosk fallback-модель %r успешно загружена", resolved)
                break
            except Exception as exc:  # pragma: no cover - environment-dependent
                logger.warning("Не удалось загрузить Vosk-модель %r: %s", resolved, exc)

        if self._recognizer is None and _resolve_model_path(model_path) != _resolve_model_path(self._DEFAULT_SMALL_MODEL):
            logger.error(
                "Vosk не смог загрузить ни основную модель %r, ни fallback %r",
                model_path,
                self._DEFAULT_SMALL_MODEL,
            )

    @property
    def available(self) -> bool:
        return self._recognizer is not None

    def recognize(self, data: bytes) -> str:
        """Текст (частичный или финальный результат) для одной порции звука.

        Общая часть и для ожидания имени (wait_for_wake_word), и для
        потоковой проверки «стоп» на лету, пока Джони занят (см.
        controller._check_for_stop_while_busy) — там больше не пишем
        отдельную запись и не зовём Whisper, а реагируем прямо на этот же
        непрерывный поток Vosk.
        """
        if self._recognizer is None:
            return ""
        if self._recognizer.AcceptWaveform(data):
            return json.loads(self._recognizer.Result()).get("text", "")
        return json.loads(self._recognizer.PartialResult()).get("partial", "")

    def reset(self) -> None:
        if self._recognizer is not None:
            self._recognizer.Reset()  # состояние Vosk — да, звук — нет

    def wait_for_wake_word(self, mic) -> None:
        """Читать блоки из ОБЩЕГО микрофона, пока не прозвучит имя.

        Свой поток больше не открывается и хвост очереди больше НЕ чистится:
        именно этот хвост — начало команды, сказанной слитно с именем. Раньше
        он выбрасывался, и слитный вызов был невозможен физически.
        """
        if self._recognizer is None:
            # Деградирующий режим: Vosk недоступен, но Whisper может быть.
            # Команда начнётся при первом воспоминании речи.
            while True:
                data = mic.read_block()
                if not audio.is_silent(data):
                    return
            return

        while True:
            data = mic.read_block()
            lowered = self.recognize(data).lower()
            if any(word in lowered for word in self.wake_words):
                self.reset()
                return

    def close(self) -> None:
        pass
