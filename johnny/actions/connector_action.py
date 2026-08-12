"""Голосовые команды к внешним тулзам: «что на картинке», «найди профили».

Тонкий слой поверх johnny/connectors: разобрать аргумент из фразы, собрать
коннектор по конфигу, произнести ответ. Вся защита — согласие, квота, кеш,
офлайн-отказ — уже в connectors/base.py и здесь не повторяется.

Почему обработчики такие короткие: любая логика тут оказалась бы вне тестов
коннекторов и вне их же защиты. Действие обязано остаться переводчиком между
фразой и коннектором, ничем больше.
"""

import logging
import re

from .. import capture, memory
from ..connectors import factory, translator
from ..connectors.azure_vision import DESCRIBE_FEATURES, READ_FEATURES
from .registry import ActionResult, registry

logger = logging.getLogger(__name__)

# Ссылка в диктовке приходит как есть: Vosk отдаёт «эйч ти ти пи эс», а Whisper
# — нормальный URL, поэтому вытаскиваем именно URL и не пытаемся чинить первый
# случай тут: это работа распознавания, а не действия.
_URL = re.compile(r"https?://\S+")

# «сравни лица A и B» — два пути в одной фразе. Разделитель «и» ненадёжен: он
# сплошь и рядом встречается внутри самих путей («D:/фото/Ира/…»), поэтому
# сначала пробуем кавычки, и только без них делим по слову.
_QUOTED = re.compile(r'"([^"]+)"')


def _result(connector_result) -> ActionResult:
    """ConnectorResult → ActionResult. Причина отказа уходит в лог, человеку —
    только текст: «нет ключа» и «кончилась квота» звучат одинаково буднично,
    а в логе их надо различать (иначе неделю ищешь сетевую проблему)."""
    if not connector_result.ok:
        logger.info("Коннектор отказал: %s", connector_result.reason)
    return ActionResult(connector_result.ok, connector_result.message)


def _build(name: str, ctx: dict):
    """Коннектор или ActionResult с отказом, если конфига нет.

    config приходит из ctx и может отсутствовать (старый вызов execute без
    него, плагин, тест). Молча падать в этом случае нельзя — команда есть,
    и человек должен услышать почему она не сработала.
    """
    config = ctx.get("config")
    if config is None:
        return None, ActionResult(False, "Внешние тулзы не настроены")
    connector = factory.build(name, config)
    if connector is None:
        return None, ActionResult(False, f"Не знаю коннектор {name}")
    return connector, None


def _source(argument: str) -> str:
    """Ссылка или путь к файлу из фразы. Ссылка ищется первой.

    Azure принимает и то и другое, поэтому одна команда работает и с картинкой
    из интернета, и с файлом на диске — разводить их на две значило бы
    заставлять человека помнить, какую произносить.
    """
    text = (argument or "").strip()
    match = _URL.search(text)
    return match.group() if match else text.strip('"')


def _two_paths(argument: str) -> tuple[str, str]:
    """Два пути из фразы. Кавычки надёжнее разделителя.

    Делим по слову только когда кавычек нет вовсе: в пути к файлу «и»
    встречается постоянно, и по нему одна фраза разъезжается на четыре куска.
    """
    text = (argument or "").strip()
    quoted = _QUOTED.findall(text)
    if len(quoted) >= 2:
        return quoted[0].strip(), quoted[1].strip()
    parts = re.split(r"\s+(?:и|с)\s+", text, maxsplit=1)
    if len(parts) == 2:
        return parts[0].strip().strip('"'), parts[1].strip().strip('"')
    return "", ""


@registry.register("describe_image")
def describe_image(argument: str, ctx: dict) -> ActionResult:
    """«Что на картинке <путь или ссылка>» — Azure AI Vision.

    Пришла на место «откуда эта картинка»: обратного поиска по интернету у
    Azure нет вовсе (см. connectors/azure_vision.py), и обещать его больше
    нечем — ни одним коннектором.
    """
    source = _source(argument)
    if not source:
        return ActionResult(False, "Нужна картинка: путь к файлу или ссылка")
    connector, refusal = _build("azure-vision", ctx)
    if refusal is not None:
        return refusal
    return _record("что на картинке", _result(connector.call(source=source, features=DESCRIBE_FEATURES)))


@registry.register("read_image_text")
def read_image_text(argument: str, ctx: dict) -> ActionResult:
    """«Прочитай текст с картинки <путь>» — OCR тем же Azure.

    Отдельное действие, а не флаг у предыдущего: набор features влияет и на
    цену вызова, и на ключ кеша — «что на картинке» и «прочитай текст» это два
    разных ответа на одну и ту же картинку.
    """
    source = _source(argument)
    if not source:
        return ActionResult(False, "Нужна картинка: путь к файлу или ссылка")
    connector, refusal = _build("azure-vision", ctx)
    if refusal is not None:
        return refusal
    return _record("прочитай текст", _result(connector.call(source=source, features=READ_FEATURES)))


# Откуда взять картинку, когда человек не назвал файл. Источник приходит
# аргументом из commands.yaml, а не отдельным действием на каждую фразу: набор
# фраз («что на экране», «что видишь», «посмотри в окно») будет расти, а кода
# на каждую новую — ноль.
#
# Здесь ИМЯ функции, а не сама функция: ссылка, взятая на импорте, навсегда
# указывает на ту версию, что была в момент загрузки модуля — подменить захват
# (в тестах или на другой платформе) стало бы нечем.
_SOURCES = {
    "screen": ("screen", "экран"),
    "region": ("region", "область"),
    "window": ("window", "активное окно"),
    "camera": ("camera", "камеру"),
}


def _record(question: str, result: ActionResult) -> ActionResult:
    """Удачный ответ — в короткую память, как реплику модели.

    Зачем: «что на экране» отвечает содержимым, а не «Готово», и следом почти
    всегда идёт «переведи это» или «а что там было». Без записи в буфер
    ссылаться там не на что: модель следующей фразы не видела ответа
    предыдущей. Обычные команды сюда не идут — их «Готово» вытеснило бы из
    5-слотового буфера единственный настоящий ответ.

    В долгую память (memory.remember) не пишем сознательно: на экране бывает
    переписка, и превращать каждый снимок в вечный факт нельзя. Для этого есть
    отдельное «запомни», которое человек говорит сам.
    """
    if result.ok and result.message:
        memory.record_turn(question, result.message)
    return result


def _capture_call(argument: str, features: str, ctx: dict):
    """Снять картинку и отдать её Azure. Возвращает (ConnectorResult, отказ).

    Ровно один из двух не None — та же идиома, что у `_build` выше. Сырой
    ConnectorResult нужен потому, что «запомни» кладёт в факт сам текст, а не
    проговариваемый ответ: «Прочитал: …» — обращение к человеку, и в списке
    фактов оно выглядит как чужая реплика.

    `finally` здесь не перестраховка: на снимке экрана может быть чужая
    переписка или документ, и он не должен пережить ответ ни при отказе
    коннектора, ни при исключении.
    """
    entry = _SOURCES.get((argument or "screen").strip().lower())
    if entry is None:
        return None, ActionResult(False, "Не знаю, откуда взять картинку")
    grab_name, what = entry
    path, refusal = getattr(capture, grab_name)()
    if not path:
        return None, ActionResult(False, f"Не могу снять {what}: {refusal}")
    try:
        connector, build_refusal = _build("azure-vision", ctx)
        if build_refusal is not None:
            return None, build_refusal
        return connector.call(source=path, features=features), None
    finally:
        capture.discard(path)


def _from_capture(argument: str, features: str, ctx: dict, question: str) -> ActionResult:
    """Снять картинку, распознать, проговорить."""
    result, refusal = _capture_call(argument, features, ctx)
    if refusal is not None:
        return refusal
    return _record(question, _result(result))


@registry.register("describe_capture")
def describe_capture(argument: str, ctx: dict) -> ActionResult:
    """«Что на экране», «что видит камера» — снять и описать.

    То же действие, что «что на картинке», но путь называть не нужно: раньше
    эта фраза не работала вовсе — назвать путь к тому, чего ещё нет на диске,
    нельзя.
    """
    return _from_capture(argument, DESCRIBE_FEATURES, ctx, "что на картинке")


@registry.register("read_capture")
def read_capture(argument: str, ctx: dict) -> ActionResult:
    """«Прочитай, что на экране» — OCR по своему же снимку."""
    return _from_capture(argument, READ_FEATURES, ctx, "прочитай текст")


@registry.register("remember_capture")
def remember_capture(argument: str, ctx: dict) -> ActionResult:
    """«Запомни, что на экране» — прочитать и положить в долгую память.

    Отдельная команда, а не автоматическая запись каждого распознавания — и это
    главное решение здесь. Складывать в вечные факты всё, что Джони прочитал с
    экрана, нельзя: там бывает чужая переписка, а факты живут до «забудь» и
    уезжают в промпт каждой следующей модели. Долгую память заводит человек, и
    признак этого — что он произнёс именно эту фразу.

    Почему OCR, а не описание сцены: запоминают с экрана номер заказа, адрес,
    код — то есть текст. Английское «a screenshot of a computer» вечным фактом
    быть не просит.
    """
    result, refusal = _capture_call(argument, READ_FEATURES, ctx)
    if refusal is not None:
        return refusal
    if not result.ok:
        return _result(result)
    text = " ".join((result.data or {}).get("lines") or []).strip()
    if not text:
        return ActionResult(False, "Текста не нашлось — запоминать нечего")
    memory.remember(text)
    # Проговариваем сам факт: OCR ошибается на мелком шрифте, и услышать это
    # надо сразу, а не через неделю в списке фактов.
    return ActionResult(True, f"Запомнил: {text}")


@registry.register("compare_faces")
def compare_faces(argument: str, ctx: dict) -> ActionResult:
    """«Сравни лица <файл1> и <файл2>» — Face++.

    Пришла на место «найди это лицо»: поиска по интернету Face++ не делает, он
    отвечает только на вопрос «один ли это человек» (см.
    connectors/faceplusplus.py). Файлы человек кладёт сам, сознательно.
    """
    first, second = _two_paths(argument)
    if not first or not second:
        return ActionResult(False, "Нужны два файла со снимками — назовите оба через «и»")
    connector, refusal = _build("faceplusplus", ctx)
    if refusal is not None:
        return refusal
    return _result(connector.call(mode="compare", first=first, second=second))


@registry.register("analyze_face")
def analyze_face(argument: str, ctx: dict) -> ActionResult:
    """«Что за лицо <файл>» — сколько лиц в кадре и признаки крупнейшего."""
    path = (argument or "").strip().strip('"')
    if not path:
        return ActionResult(False, "Нужен файл со снимком")
    connector, refusal = _build("faceplusplus", ctx)
    if refusal is not None:
        return refusal
    return _result(connector.call(mode="detect", path=path))


@registry.register("search_face_archive")
def search_face_archive(argument: str, ctx: dict) -> ActionResult:
    """«Есть ли этот человек в архиве <файл>» — локальный индекс лиц.

    Слово «архив» в фразе — не украшение, а граница обещания. Коннектор ищет
    среди снимков, которые человек сам отдал в индекс, и по интернету не ходит
    вовсе. Фразу вида «найди это лицо» сюда вешать нельзя: она обещает поиск по
    сети, которого у нас нет ни одним коннектором (см. commands.yaml).

    Вторая ступень — вердикт Face++ — зовётся ТОЛЬКО на спорного кандидата.
    Причина в цифрах: у разных людей косинус 0.026 при пороге 0.40, то есть
    уверенное совпадение локальная модель уже отличила, и переспрашивать про
    него значит потратить платный вызов и 10 секунд паузы (min_interval у
    Face++) на подтверждение того, что и так известно. Спорное же — ровно тот
    случай, где второе мнение и нужно.
    """
    path = (argument or "").strip().strip('"')
    if not path:
        return ActionResult(False, "Нужен снимок человека, которого искать")
    connector, refusal = _build("face-index", ctx)
    if refusal is not None:
        return refusal

    result = connector.call(path=path)
    if not result.ok:
        return _result(result)

    matches = (result.data or {}).get("matches") or []
    doubtful = matches and not matches[0]["same"]
    if doubtful:
        verdict = _second_opinion(path, matches[0]["path"], ctx)
        if verdict:
            return _record("есть ли в архиве", ActionResult(True, f"{result.message}. {verdict}"))
    return _record("есть ли в архиве", _result(result))


def _second_opinion(query: str, candidate: str, ctx: dict) -> str:
    """Вердикт Face++ по паре снимков или пустая строка.

    Пустая строка — это «промолчал», и молчание тут правильный ответ на любой
    отказ: нет согласия, нет ключа, кончилась квота. Локальный поиск уже дал
    результат, и глушить его сообщением про чужой сервис, которого человек не
    звал, незачем — причина отказа и так уйдёт в лог внутри коннектора.
    """
    connector, refusal = _build("faceplusplus", ctx)
    if refusal is not None:
        return ""
    result = connector.call(mode="compare", first=query, second=candidate)
    if not result.ok:
        return ""
    data = result.data or {}
    if data.get("same"):
        return "Face++ считает, что это один человек"
    return "Face++ считает, что это разные люди"


@registry.register("find_profiles")
def find_profiles(argument: str, ctx: dict) -> ActionResult:
    """«Найди профили <ник>» — social-analyzer по локальному пакету."""
    username = (argument or "").strip()
    if not username:
        return ActionResult(False, "Нужен ник для поиска")
    connector, refusal = _build("social-analyzer", ctx)
    if refusal is not None:
        return refusal
    return _result(connector.call(username=username))


# «переведи привет на английский» — язык назван в хвосте самой фразы. Отдельным
# правилом в commands.yaml это не разобрать: шаблон там один со «звёздочкой», а
# языков полтора десятка, и «переведи * на английский», «переведи * на
# немецкий»… — это полтора десятка почти одинаковых правил, которые ещё и
# перекрывают общее «переведи *» по порядку файла.
# «по-английски» пишется через дефис и приходит одним токеном, поэтому дефис
# здесь равноправен пробелу: «как будет привет по-английски» — самая обиходная
# форма вопроса, терять её нельзя.
_TARGET = re.compile(
    r"^(?P<text>.+?)[\s-]+(?:на|по)[\s-]+(?P<language>[а-яёa-z]+)\s*$", re.IGNORECASE
)


@registry.register("translate")
def translate(argument: str, ctx: dict) -> ActionResult:
    """«Переведи <текст> [на <язык>]» — офлайн-движок, иначе LibreTranslate.

    Без названия языка переводим в другую сторону от исходного: русский текст —
    на английский, любой другой — на русский. Это поведение настольных
    переводчиков, и оно избавляет от «на английский» в каждой второй фразе.
    """
    text = (argument or "").strip()
    if not text:
        return ActionResult(False, "Нужен текст для перевода")

    target = ""
    match = _TARGET.match(text)
    if match:
        target = translator.language_code(match.group("language"))
        if target:
            # Хвост «на английский» отрезаем только если это действительно
            # язык: «переведи деньги на карту» — не запрос перевода на «карту».
            text = match.group("text").strip()
    if not target:
        target = translator.opposite(translator.detect_language(text))

    connector, refusal = _build("translator", ctx)
    if refusal is not None:
        return refusal
    return _result(connector.call(text=text, target=target))
