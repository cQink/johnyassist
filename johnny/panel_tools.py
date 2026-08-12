"""Тулкиты в панели: кнопка -> то же действие, что и голосом.

Кнопка идёт через registry.execute, а не напрямую в коннектор, и это главное
решение здесь. Иначе разбор языка («на английский»), выковыривание ссылки из
фразы и текст отказов пришлось бы писать заново, и они бы разошлись: голос
говорит одно, кнопка показывает другое, а починка нужна в двух местах.

Логика лежит отдельно от panel.py, чтобы её можно было проверить без Tk:
виджеты в тестах не поднять, а вот «что уйдёт в действие» и «готов ли
коннектор» — можно.
"""

import logging
import queue
from dataclasses import dataclass

from .actions.registry import registry
from .connectors import factory

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Tool:
    """Одна карточка в разделе «Тулкиты»."""

    action: str  # действие в registry — общий путь с голосовой командой
    connector: str  # имя для factory: нужно только чтобы узнать готовность
    title: str
    hint: str  # что вписать в поле, текстом в самом поле
    button: str
    # "file" — «Обзор» подставляет путь вместо содержимого поля; "files" —
    # дописывает к тому, что уже есть, чтобы за два нажатия набрать пару снимков;
    # "pick" — вместо поля список готовых значений (см. options): у захвата
    # вписывать нечего, источник выбирается, а не набирается.
    kind: str = "text"
    phrase: str = ""  # как это же сказать голосом — подсказка под полем
    # Для kind="pick": (значение для действия, подпись человеку). Первый —
    # выбранный по умолчанию.
    options: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class Status:
    """Живое состояние Джони одной строкой.

    tone — «насколько всё плохо», а не цвет: цвета живут в палитре панели, и
    подбирать их здесь значило бы держать оформление в двух файлах.
    """

    text: str
    tone: str  # "ok" | "warn" | "bad"
    listening: bool


def status(*, listener: bool, recognizer: bool, paused: bool = False) -> Status:
    """Что показать про состояние движков.

    Ладдер по (listener, recognizer) был выписан трижды: в app.py для консоли,
    в tray.py для лога и там же ещё раз для отдельного окошка статуса. Три копии
    уже разошлись формулировками, а состояний всего четыре — держим одну.

    Сломанные движки важнее паузы: пауза снимается одной кнопкой, а без Vosk и
    Whisper снимать её незачем — Джони всё равно ничего не услышит.
    """
    if not listener and not recognizer:
        return Status("Не слышит: ни Vosk, ни Whisper не поднялись", "bad", False)
    if paused:
        return Status("На паузе", "warn", False)
    if listener and recognizer:
        return Status("Слушает", "ok", True)
    if listener:
        return Status("Слышит имя, но команды не разберёт: Whisper отключён", "warn", True)
    return Status("Слушает без имени: распознаёт любую речь", "warn", True)


# Откуда взять снимок — один список на обе карточки захвата: набор источников
# один и тот же, а разъехавшиеся копии значат, что «область» появилась в одной
# карточке и не появилась в другой. Значения обязаны совпадать с _SOURCES в
# actions/connector_action.py — там же и проверяется тестом.
#
# «экран» первым: он выбран по умолчанию, и это самый частый вопрос. «область»
# сразу за ним — то же самое, но точнее, и на снимок уедет только выделенное.
_CAPTURE_SOURCES = (
    ("screen", "экран"),
    ("region", "область"),
    ("window", "активное окно"),
    ("camera", "камера"),
)

# Карточка на ДЕЙСТВИЕ, а не на коннектор: у azure-vision их два («что на
# картинке» и «прочитай текст»), у faceplusplus тоже два, и различаются они
# только параметрами вызова. Поэтому и виджеты, и готовность ниже ключуются по
# action — по имени коннектора вторая карточка затирала бы первую.
TOOLS = (
    Tool(
        action="describe_image",
        connector="azure-vision",
        title="Что на картинке",
        hint="файл на этом компьютере или https://… ссылка",
        button="Разобрать",
        kind="file",
        phrase="что на картинке …",
    ),
    Tool(
        action="read_image_text",
        connector="azure-vision",
        title="Текст с картинки",
        hint="скриншот или фото документа; текст вернётся на своём языке",
        button="Прочитать",
        kind="file",
        phrase="прочитай текст с картинки …",
    ),
    # Свои снимки: экран, окно, камера. Без поля с путём — его и нельзя
    # заполнить, файла ещё нет. Снимок удаляется сразу после ответа
    # (см. actions/connector_action.py): на экране бывает чужая переписка.
    Tool(
        action="describe_capture",
        connector="azure-vision",
        title="Что я вижу",
        hint="снимок делается сам и удаляется сразу после ответа; «область» — мышью",
        button="Посмотреть",
        kind="pick",
        phrase="что на экране",
        options=_CAPTURE_SOURCES,
    ),
    Tool(
        action="read_capture",
        connector="azure-vision",
        title="Текст с экрана",
        hint="OCR по своему снимку; текст вернётся на своём языке",
        button="Прочитать",
        kind="pick",
        phrase="прочитай что на экране",
        options=_CAPTURE_SOURCES,
    ),
    Tool(
        action="compare_faces",
        connector="faceplusplus",
        title="Сравнить лица",
        hint="два снимка; «Обзор» дописывает второй к первому",
        button="Сравнить",
        kind="files",
        phrase="сравни лица … и …",
    ),
    Tool(
        action="analyze_face",
        connector="faceplusplus",
        title="Что за лицо",
        hint="снимок: сколько лиц в кадре и признаки крупнейшего",
        button="Разобрать",
        kind="file",
        phrase="что за лицо …",
    ),
    Tool(
        action="find_profiles",
        connector="social-analyzer",
        title="Профили по нику",
        hint="ник без собачки",
        button="Собрать",
        phrase="найди профили …",
    ),
    Tool(
        action="translate",
        connector="translator",
        title="Перевод",
        hint="текст; можно дописать «на английский»",
        button="Перевести",
        phrase="переведи … на английский",
    ),
)


def run(tool: Tool, value: str, config) -> tuple[bool, str]:
    """Выполнить тулкит и вернуть (получилось, что сказать человеку).

    Вызывать только из фонового потока: внутри сеть, а разбор картинки у Azure
    занимает секунды — в потоке Tk панель бы висела всё это время без единого
    признака жизни.
    """
    if not value.strip():
        return False, f"Нужно заполнить поле: {tool.hint}"
    try:
        result = registry.execute(tool.action, value, config=config)
    except Exception:
        # Тулкит не должен уносить панель за собой: человек нажал кнопку, а не
        # согласился перезапускать Джони.
        logger.exception("Тулкит %s упал", tool.action)
        return False, "Что-то сломалось внутри, подробности в johnny.log"
    return bool(result.ok), result.message


def drain(pending: queue.Queue, stop_on: tuple = ()) -> bool:
    """Выполнить всё, что фоновые потоки положили в очередь. Вернуть, продолжать ли.

    Фоновый поток не может ни трогать виджеты, ни даже позвать after: after сам
    по себе вызов в Tcl, и из чужого потока он падает «main thread is not in main
    loop». Поэтому поток кладёт замыкание в очередь, а разбирает её поток Tk.

    Живёт здесь, а не в panel.py, ровно из-за одного правила ниже: упавшее
    задание не должно останавливать очередь. Без него первая же ошибка навсегда
    замораживает все тулкиты, и проверить это на живом Tk нечем.

    stop_on — исключения «панель закрыли»: продолжать разбор бессмысленно,
    показывать ответ уже некуда.
    """
    while True:
        try:
            job = pending.get_nowait()
        except queue.Empty:
            return True
        try:
            job()
        except stop_on:
            return False
        except Exception:
            logger.exception("Ответ тулкита не удалось показать")


def readiness(config) -> dict[str, tuple[bool, str]]:
    """По каждому тулкиту: готов ли и если нет — почему, словами.

    Ключ — action, а не имя коннектора: на одном коннекторе висит по две
    карточки, и по имени вторая затирала бы первую (осталась бы навсегда с
    надписью «проверяю…»).

    Тоже из фонового потока: available() у social-analyzer и переводчика
    импортирует пакет, а это заметная пауза на первом разе.
    """
    state: dict[str, tuple[bool, str]] = {}
    for tool in TOOLS:
        state[tool.action] = _one(tool, config)
    return state


def _one(tool: Tool, config) -> tuple[bool, str]:
    if config is None:
        return False, "Внешние тулзы не настроены"
    try:
        connector = factory.build(tool.connector, config)
    except Exception:
        logger.exception("Не собрался коннектор %s", tool.connector)
        return False, "Не удалось собрать коннектор, подробности в johnny.log"
    if connector is None:
        return False, "Такого коннектора нет"
    if connector.requires_consent and not connector._consent:
        # Ключ может быть на месте, а согласия нет — это разные вещи, и человек
        # должен видеть именно вторую причину, иначе пойдёт искать ключ.
        return False, f"Нужно согласие: connectors.{tool.connector}.consent в settings.yaml"
    try:
        return connector.available()
    except Exception:
        logger.exception("available() коннектора %s упал", tool.connector)
        return False, "Не удалось проверить готовность, подробности в johnny.log"
