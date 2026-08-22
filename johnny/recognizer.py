import gc
import glob
import logging
import os
import re
import sys
import threading


def _enable_cuda_dlls() -> None:
    """Прописать в PATH папки с CUDA-DLL (cuBLAS/cuDNN/cudart) из pip-пакетов nvidia-*.

    CTranslate2 (движок faster-whisper) грузит эти DLL по имени, поэтому их
    каталоги должны быть в PATH ДО импорта faster_whisper. На CPU-only машине
    пакетов nvidia нет — тогда это просто ничего не делает.
    """
    bin_dirs: list[str] = []
    for path_entry in sys.path:
        nvidia_dir = os.path.join(path_entry, "nvidia")
        if os.path.isdir(nvidia_dir):
            bin_dirs.extend(glob.glob(os.path.join(nvidia_dir, "*", "bin")))
    if bin_dirs:
        os.environ["PATH"] = os.pathsep.join(bin_dirs) + os.pathsep + os.environ.get("PATH", "")


_enable_cuda_dlls()

import numpy as np

_WHISPER_IMPORT_ERROR: Exception | None = None
try:
    from faster_whisper import WhisperModel
except Exception as exc:  # pragma: no cover - exercised in environments without usable DLLs
    WhisperModel = None
    _WHISPER_IMPORT_ERROR = exc

# Короткая шапка + словарь имён. Целых командных фраз здесь СОЗНАТЕЛЬНО нет:
# initial_prompt обрезается на 224 токенах, а фразы вытеснили бы имена
# собственные — именно на них Whisper ошибается чаще всего.
# «Джони» в шапке не для красоты: в слитном режиме имя попадает в запись, и
# по нему strip_wake_word решает, доверять ли срабатыванию Vosk. Не подскажешь
# имя — Whisper напишет его как попало, и защита от ложных срабатываний
# начнёт отваливаться на верных командах.
# Имя стоит ПЕРВЫМ словом сознательно: в слитном режиме настоящая расшифровка
# штатно начинается с «джони». Если бы имя стояло в конце шапки, сразу за ним
# в подсказке начинался бы словарь — и настоящая команда вида «джони + слова
# словаря подряд» физически совпадала бы с непрерывным куском подсказки,
# is_prompt_echo опознавал бы её как эхо и молча выбрасывал. Когда имя первое,
# за ним в подсказке идёт «Русские голосовые команды», а не словарь, и такое
# совпадение больше невозможно.
_BASE_PROMPT = "Джони. Русские голосовые команды."

# Бюджет слов словаря. Измерено РЕАЛЬНЫМ токенизатором faster-whisper-medium
# (tokenizers.Tokenizer из tokenizer.json модели) на реальном config/: 87 слов
# словаря дают 220 токенов из 224 доступных Whisper'у — запас 4 токена.
# Запас нужен намеренно: при переполнении лимита faster-whisper обрезает
# подсказку с НАЧАЛА, а не с конца, — значит первым пропадёт «Джони» из
# _BASE_PROMPT, на котором в слитном режиме держится проверка доверия к
# вейкворду (см. комментарий выше про имя первым словом); молчаливая потеря
# этого слова обнулила бы всю защиту от ложных срабатываний. apps.yaml и
# channels.yaml пополняются почти каждую итерацию, а бюджет считается в
# словах, не в токенах: одно новое длинное имя собственное с редкими
# токенами может съесть больше токенов, чем ушло слов из лимита.
_MAX_VOCAB_WORDS = 87

# Служебные слова: биасить их бессмысленно, они и так известны модели.
_STOPWORDS = frozenset({
    "на", "в", "во", "и", "с", "со", "по", "за", "к", "у", "о", "об", "от",
    "до", "из", "мне", "мой", "моя", "это", "все", "всё", "как", "что",
    "пожалуйста", "давай", "ка",
})

logger = logging.getLogger(__name__)

# Сколько слов подряд из подсказки считаем эхом. 4 — проверено на всех 164
# командах конфига: ни одна не отсеивается ложно (при 3 отсеивались «зайди
# на йети», «найди на твиче *» и др.).
_ECHO_MIN_WORDS = 4

# Живая речь повторяет слово подряд максимум дважды («очень очень устал»,
# «да да, понял») — трижды и больше подряд естественная русская речь не
# делает НИКОГДА. Это сигнатура decode-петли Whisper (см. is_repetition_loop).
_REPEAT_LOOP = re.compile(r"(\S+)(?:\s+\1){2,}", re.IGNORECASE)


def is_repetition_loop(text: str) -> bool:
    """Whisper зациклился и повторяет одно слово подряд 3+ раз?

    ЖИВОЙ БАГ (2026-08-06): «Джони, введи <текст>» (длинная диктовка,
    max_seconds теперь 25с) вернуло "введи", повторённое ~60 раз, вместо
    надиктованного текста. Классическая деградация авторегрессивного
    декодера на длинном/невнятном хвосте аудио — усугублена тем, что
    "введи" само есть в initial_prompt (build_vocabulary включает ключевые
    слова команд), и декодер зацикливается именно на нём, а не на случайном
    слове. Печатать такой мусор в чужое поле ввода нельзя, и частично
    восстановить текст ВОКРУГ петли тоже нельзя — раз декодер сорвался в
    этом месте, доверия к остальному сегменту тоже нет. Поэтому вся
    расшифровка считается провалом целиком, тем же приёмом, что уже есть
    для эха подсказки (is_prompt_echo).
    """
    return _REPEAT_LOOP.search(text) is not None


def _words(text: str) -> list[str]:
    """Слова в нижнем регистре, ё→е, без пунктуации."""
    return re.findall(r"[a-zа-я0-9]+", text.lower().replace("ё", "е"))


def is_prompt_echo(text: str, prompt: str, min_words: int = _ECHO_MIN_WORDS) -> bool:
    """Текст целиком — непрерывный кусок подсказки длиной от min_words слов?

    Whisper держит initial_prompt в контексте как начало текста и на невнятном
    аудио просто продолжает его вместо транскрипции («пауза» → «Русские
    голосовые команды.»). Метрики уверенности такое не отличают от тихой речи,
    поэтому ловим детерминированно: свою же подсказку наружу не выдаём.

    Отдельно, НЕЗАВИСИМО от min_words, ловим случай, когда Whisper вернул
    ровно шапку подсказки целиком — это стопроцентно эхо, а не настоящая
    команда, даже если длина шапки не короче порога (сейчас у _BASE_PROMPT
    ровно 4 слова — столько же, сколько _ECHO_MIN_WORDS). Шапку берём из
    САМОГО prompt (его первые N слов, где N — длина _BASE_PROMPT в словах), а не из
    глобального _BASE_PROMPT напрямую: сегодня каждый вызывающий код строит
    prompt как _BASE_PROMPT + словарь, так что результат тот же, но функция
    честно использует свой собственный аргумент, а не молча полагается на
    то, что вызывающий код всегда начинает с одной и той же шапки.
    """
    said = _words(text)
    known = _words(prompt)
    header_len = len(_words(_BASE_PROMPT))
    if said == known[:header_len]:
        return True
    if len(said) < min_words:
        return False
    return any(
        known[i : i + len(said)] == said for i in range(len(known) - len(said) + 1)
    )


def build_vocabulary(apps: dict, channels: dict, commands=None, people=None) -> str:
    """Словарь имён и ключевых слов для initial_prompt.

    Порядок важен: сначала имена собственные (игры, программы, каналы, ЛЮДИ) —
    они уникальны и Whisper ошибается на них чаще; затем значимые слова
    команд. При переполнении бюджета обрезается хвост, то есть слова команд, а
    имена остаются.

    Имена людей — самый ценный кусок словаря: «Гречка», «Грущенко», «Твикс 1»
    Whisper без подсказки пишет как попало, а от них зависит, кому уйдёт
    сообщение и кому Джони позвонит. Бюджет ради них НЕ поднимается: он
    измерен настоящим токенизатором и стоит вплотную к лимиту Whisper —
    имена просто вытесняют из хвоста низкоценные слова команд.

    Бюджет и дедупликация — на уровне ОТДЕЛЬНЫХ СЛОВ, а не записей конфига:
    алиасы вида «9 импульс» или «серёга пират» — это два слова, а не одно.
    Если резать список ДО разбиения на слова, такие двусловные записи
    считаются одной единицей среза, а после склейки через " ".join() дают
    на 1 слово больше, чем предполагает бюджет, — словарь незаметно вылезает
    за лимит токенов Whisper.
    """
    proper: list[str] = []
    for name in apps.keys():
        proper += str(name).split()
    for info in channels.values():
        for alias in info.get("aliases", []):
            proper += str(alias).split()
    for name, info in (people or {}).items():
        # И как человека зовут вслух (ключ, алиасы), и как он подписан в
        # Discord: отображаемое имя тоже звучит в командах («напиши гречке»).
        proper += str(name).split()
        for alias in info.get("aliases", []):
            proper += str(alias).split()
        proper += str(info.get("discord", "")).lower().split()

    keywords: list[str] = []
    for rule in commands or []:
        for word in rule.pattern.replace("*", " ").split():
            if word not in _STOPWORDS and len(word) > 2:
                keywords.append(word)

    unique = list(dict.fromkeys(proper + keywords))  # уникальные, порядок сохранён
    return " ".join(unique[:_MAX_VOCAB_WORDS])


class Recognizer:
    def __init__(self, model: str, device: str, vocabulary: str = ""):
        self._prompt = _BASE_PROMPT + (" " + vocabulary if vocabulary else "")
        # Худшая уверенность последней расшифровки. Пока только пишется в
        # историю: порог отсечения выбирается по накопленным данным, а не
        # угадывается заранее.
        self.last_confidence = 0.0
        # Замок общий у расшифровки и подмены модели: transcribe работает в
        # потоке контроллера, а use_model зовут из потока сторожа. RLock, а не
        # Lock, чтобы вложенный вызов внутри одного потока не встал намертво.
        self._lock = threading.RLock()
        self._device = device
        self.model_name = model
        self._model = None

        if WhisperModel is None:
            logger.warning("faster-whisper недоступен, распознавание будет отключено: %s", _WHISPER_IMPORT_ERROR)
            return

        self._model = self._load(model, device)

    def _load(self, model: str, device: str):
        """Поднять модель, спускаясь по лесенке до первого рабочего режима.

        Возвращает модель или None. Лесенка нужна потому, что доступность
        режима зависит от машины: на карте без float16 просьба о нём падает, а
        человек всё равно должен быть услышан — пусть и на процессоре.

        Проверку «faster-whisper не установлен» __init__ уже делает — но
        _load зовут ещё и из use_model, и там этой проверки не было: на
        машине без faster-whisper WhisperModel is None, и каждый рунг лесенки
        падал бы на TypeError вместо честного пропуска. Со сторожем это
        цикл: available навсегда False → Guard.tick на каждом опросе снова
        зовёт use_model → снова TypeError на всех рунгах. Ранний выход
        избавляет от лесенки целиком и оставляет use_model один аккуратный
        warning вместо восьми бессмысленных.
        """
        if WhisperModel is None:
            return None

        if device == "cuda":
            preferred_compute_types = [
                ("cuda", "float16"),
                ("cuda", "float32"),
                ("cuda", "int8"),
                ("cpu", "int8"),
            ]
        else:
            preferred_compute_types = [
                ("cpu", "int8"),
                ("cpu", "float32"),
            ]

        last_error: Exception | None = None
        for target_device, compute_type in preferred_compute_types:
            try:
                loaded = WhisperModel(
                    model,
                    device=target_device,
                    compute_type=compute_type,
                )
            except Exception as exc:  # pragma: no cover - environment-dependent
                last_error = exc
                logger.warning(
                    "Не удалось инициализировать Whisper-модель %r с %s/%s: %s",
                    model,
                    target_device,
                    compute_type,
                    exc,
                )
                continue
            if target_device != device or compute_type != ("float16" if device == "cuda" else "int8"):
                logger.warning(
                    "Whisper-модель %r инициализирована в fallback-режиме %s/%s",
                    model,
                    target_device,
                    compute_type,
                )
            return loaded

        logger.warning("Whisper не смог загрузиться ни в одном режиме: %s", last_error)
        return None

    def use_model(self, model: str) -> bool:
        """Сменить модель Whisper на ходу. True — сменили, False — оставили как было.

        Зачем: medium на видеокарте занимает 2138 МБ, small — 648 МБ (замер
        2026-08-22 на RTX 3070). На время игры разницу отдаём игре.

        Замок держим на всю подмену: она занимает около 2.4 секунды, и если
        выдернуть модель из-под работающего transcribe, команда пропадёт.
        """
        with self._lock:
            if model == self.model_name and self._model is not None:
                return False

            previous_name = self.model_name
            # Старую отпускаем ДО загрузки новой. Иначе на карте полежат обе,
            # и подмена, затеянная ради экономии памяти, её же и не даст.
            self._model = None
            gc.collect()

            loaded = self._load(model, self._device)
            if loaded is None:
                logger.warning(
                    "Модель %r не поднялась — возвращаюсь на %r", model, previous_name
                )
                self._model = self._load(previous_name, self._device)
                return False

            self._model = loaded
            self.model_name = model
            logger.info("Whisper переключён на модель %r", model)
            return True

    @property
    def available(self) -> bool:
        return self._model is not None

    def transcribe(self, audio: np.ndarray) -> str:
        """Аудио (float32, 16 кГц) → текст. Запись — не наше дело.

        Проверка на None ОБЯЗАНА жить внутри замка, а не перед ним: use_model
        держит тот же замок всю подмену (~2.4 с) и на это время сам обнуляет
        self._model. Проверка снаружи увидела бы этот временный None и молча
        вернула бы "" — то есть проглотила бы команду ровно в тот момент,
        ради защиты которого замок и заводился. Внутри замка расшифровка,
        пришедшая во время подмены, просто ждёт своей очереди и отрабатывает
        уже на новой модели.
        """
        with self._lock:
            if self._model is None:
                return ""
            segments, _ = self._model.transcribe(
                audio,
                language="ru",
                vad_filter=True,
                initial_prompt=self._prompt,
                beam_size=5,
                # Команды независимы — не тянем контекст прошлой фразы (убирает
                # «залипания»/галлюцинации Whisper на коротких/тихих записях).
                condition_on_previous_text=False,
                # Мягче отбрасываем «нет речи» и даём температуре откатиться —
                # меньше пустых/выдуманных результатов на шумных фрагментах.
                no_speech_threshold=0.5,
                temperature=[0.0, 0.2, 0.4, 0.6],
            )
            segments = list(segments)
        self.last_confidence = min(
            (seg.avg_logprob for seg in segments), default=0.0
        )
        text = " ".join(seg.text.strip() for seg in segments).strip()
        if is_prompt_echo(text, self._prompt):
            # Whisper продолжил подсказку вместо расшифровки — считаем, что не
            # расслышали, иначе Джони выполнит собственный словарь как команду.
            # Уверенность сбрасываем вместе с текстом: свою подсказку Whisper
            # продолжает уверенно, и оставшееся число описывало бы выброшенное
            # эхо, а не пустой результат. Иначе в историю попадала бы пустая
            # строка с хорошей уверенностью — ровно та грязь, которая портит
            # набор данных, ради которого уверенность и пишется.
            logger.warning("Отброшено эхо подсказки: %r", text)
            self.last_confidence = 0.0
            return ""
        if is_repetition_loop(text):
            logger.warning("Отброшена decode-петля Whisper: %r", text)
            self.last_confidence = 0.0
            return ""
        return text
