"""Azure AI Vision: что изображено на картинке и какой на ней текст.

Пришёл на место TinEye, и это НЕ равнозначная замена — подмену легко не
заметить, поэтому она записана здесь. TinEye отвечал на вопрос «где ещё в
интернете встречается эта картинка» (происхождение). Azure отвечает на другой:
«что на этой картинке изображено и что на ней написано». Поиска по индексу
интернета у Azure нет вовсе; тот, что был у Microsoft (Bing Visual Search),
закрыт. Значит команда «откуда эта картинка» неисполнима ни одним нашим
коннектором, и обещать её больше нельзя — вместо неё появились «что на
картинке» и «прочитай текст».

Взамен закрывается другой пункт плана — OCR и распознавание объектов
(details/add_ocr_and_live_camera_recognition_support.md): features=read даёт
текст со скриншота, features=caption,tags,objects — описание и объекты.

Решения, которые важно не «упростить» обратно:

  - Согласие обязательно. TinEye получал ссылку на уже опубликованную картинку,
    а сюда уходит файл с ЭТОГО компьютера — скриншот с перепиской, документ,
    чужое лицо в кадре. Отправка содержимого экрана третьей стороне без явного
    разрешения — ровно то, от чего защищает consent.
  - Ключ кеша — по содержимому файла, а не по пути. screenshot.png
    перезаписывается каждым новым снимком, и ключ по пути отдал бы вчерашний
    текст на сегодняшнюю картинку.
  - Endpoint — из настроек, а не константа: у каждого ресурса Azure он свой
    (https://<имя>.cognitiveservices.azure.com). Без него коннектор честно
    отказывает, а не ходит в чужой.
  - Описание (caption) приходит по-английски. Русского у Azure нет: language
    поддерживает en/es/ja/pt/zh, и подставить ru нельзя. Текст со скриншота
    (read) при этом возвращается на своём языке, поэтому OCR по-русски работает.
  - Недоступная в регионе фича снимается и запрос повторяется остатком, а не
    падает целиком. Проверено на живом ресурсе: в swedencentral caption отвечает
    400 при исправном ключе, и без обхода «что на картинке» не работало бы
    вообще. Подробности у REGION_ERROR_MARKER.
"""

from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path

from ..http_client import post, post_bytes
from .base import Connector

logger = logging.getLogger(__name__)

# Версия зафиксирована: у Azure одновременно живут несколько, и «последняя»
# меняет схему ответа молча.
API_VERSION = "2024-02-01"
PATH = "/computervision/imageanalysis:analyze"

# Наборы под две команды. read отдельно от описания намеренно: OCR по скриншоту
# — самый частый запрос, а caption с tags к нему добавляют секунды и цену.
DESCRIBE_FEATURES = "caption,tags,objects"
READ_FEATURES = "read"

# Часть возможностей есть не во всех регионах, и это выяснилось на живом
# ресурсе: в swedencentral caption отвечает 400 «The feature 'Caption' is not
# supported in this region», хотя ключ, адрес и права в порядке. Без обхода
# команда «что на картинке» не работала бы НИКОГДА — не иногда, а вообще, потому
# что caption стоит первым в DESCRIBE_FEATURES и весь запрос уходит целиком.
#
# Поэтому недоступную фичу снимаем и переспрашиваем остатком: tags и objects
# работают там же и дают осмысленный ответ. Полное описание фразой останется
# недоступным, пока ресурс живёт в этом регионе, — это ограничение Azure, а не
# наше, и «Вижу: cat, laptop» честнее отказа.
REGION_ERROR_MARKER = "not supported in this region"
# По документации Azure регионом ограничены именно эти две. Список — запасной
# путь: обычно фича вычитывается из самого сообщения, а сюда мы попадаем, если
# Azure назвал её иначе, чем она пишется в запросе.
REGION_LIMITED_FEATURES = ("caption", "denseCaptions")

TIMEOUT_SECONDS = 30.0
# Azure отвергает всё крупнее 20 МБ. Проверяем сами: свой отказ понятнее, чем
# 400 InvalidRequest, и не тратит квоту.
MAX_IMAGE_BYTES = 20 * 1024 * 1024
KEEP_TAGS = 10
# Ниже этого порога теги — шум: Azure возвращает их десятками с уверенностью
# в районе нуля, и голосом они звучат как бред.
MIN_TAG_CONFIDENCE = 0.6
# Сколько строк текста произносить. Полный текст остаётся в data для панели:
# читать вслух страницу договора никто не просил.
KEEP_LINES = 40

# Адрес ресурса → фичи, которых у него в регионе нет. Ключ адрес, а не экземпляр
# коннектора: коннектор пересобирается фабрикой на КАЖДЫЙ вызов, и память внутри
# объекта не пережила бы даже второй запрос — лишний 400 платился бы каждый раз.
# Регион ресурса при этом не меняется, так что факт верен до конца процесса.
#
# На диск не сохраняем сознательно: ресурс могут пересоздать в другом регионе, и
# записанное «caption тут нет» тогда врало бы молча и навсегда. Цена памяти в
# процессе — один лишний запрос после запуска, цена ошибки на диске — потерянная
# возможность без единого следа.
_REGION_GAPS: dict[str, set[str]] = {}


def forget_region_gaps() -> None:
    """Забыть, каких фич не было в регионе. Нужно тестам и смене ресурса."""
    _REGION_GAPS.clear()


class AzureVisionConnector(Connector):
    name = "azure-vision"
    # См. шапку: сюда уходит файл с этого компьютера, а не публичная ссылка.
    requires_consent = True
    # Сутки: картинка не меняется, а разбор одной и той же стоит денег.
    ttl_seconds = 7 * 86400.0
    # Бесплатный тариф F0 — 20 вызовов в минуту и 5000 в месяц. Суточная доля
    # от месячной и есть эта цифра; при платном тарифе поднимается в настройках.
    daily_quota = 160
    min_interval_seconds = 3.0

    def __init__(
        self,
        *,
        api_key: str = "",
        endpoint: str = "",
        daily_quota=None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._api_key = api_key
        self._endpoint = (endpoint or "").rstrip("/")
        if daily_quota is not None:
            self.daily_quota = int(daily_quota)

    def available(self) -> tuple[bool, str]:
        # Две причины отказа, и различать их обязательно: человек с ключом без
        # адреса иначе пойдёт искать второй ключ.
        if not self._api_key:
            return False, "Для разбора картинок нужен ключ Azure Vision в secrets.yaml"
        if not self._endpoint:
            return (
                False,
                "Не задан адрес ресурса Azure: connectors.azure-vision.endpoint в settings.yaml",
            )
        return True, ""

    def cache_params(self, params: dict) -> dict:
        """Ключ — по содержимому файла и набору features.

        features в ключе обязателен: «прочитай текст» и «что на картинке» — два
        разных ответа на одну и ту же картинку, и общий ключ отдавал бы один
        вместо другого.
        """
        source = str(params.get("source", ""))
        features = str(params.get("features", DESCRIBE_FEATURES))
        if _looks_like_url(source):
            return {"url": source, "features": features}
        try:
            digest = hashlib.sha1(Path(source).read_bytes()).hexdigest()
        except OSError:
            # Файла нет — ключ по пути, а внятная ошибка придёт из _run: две
            # одинаковые проверки в двух местах рано или поздно разойдутся.
            digest = source
        return {"image": digest, "features": features}

    def _run(self, **params) -> dict:
        source = str(params.get("source", "")).strip()
        requested = str(params.get("features") or DESCRIBE_FEATURES)
        if not source:
            raise RuntimeError("не указана картинка")

        payload = self._payload(source)
        features = _without(requested, _REGION_GAPS.get(self._endpoint, set()))
        if not features:
            raise RuntimeError(f"в этом регионе Azure нет ни одной из: {requested}")

        response = self._ask(features, source, payload)
        if response.status_code == 200:
            return _compact(response.json(), features)

        # Второй заход — ровно один и только из-за региона. Повторять по любой
        # другой ошибке нельзя: 401 и 429 от повтора не чинятся, а квоту тратят.
        missing = _region_gap(response, features)
        if missing:
            _REGION_GAPS.setdefault(self._endpoint, set()).update(missing)
            logger.warning(
                "Azure Vision: в регионе ресурса нет %s, спрашиваю без них",
                ", ".join(sorted(missing)),
            )
            features = _without(features, missing)
            if not features:
                raise RuntimeError(_error_text(response))
            # Суточный счётчик прибавится один раз на оба запроса (base считает
            # в finally вокруг call). Расхождение в один вызов на запуск —
            # меньшее зло, чем сломанная команда.
            response = self._ask(features, source, payload)
            if response.status_code == 200:
                return _compact(response.json(), features)

        raise RuntimeError(_error_text(response))

    def _payload(self, source: str) -> bytes:
        """Байты локального файла (для ссылки — пусто).

        Читаем ДО запроса и один раз: при повторе после регионального отказа
        второе чтение с диска ничего не добавит, а файл к тому моменту может уже
        перезаписаться — скриншот на то и скриншот.
        """
        if _looks_like_url(source):
            return b""
        path = Path(source)
        try:
            payload = path.read_bytes()
        except OSError as error:
            raise RuntimeError(f"не читается файл: {error.strerror or error}") from error
        if not payload:
            raise RuntimeError("файл пустой")
        if len(payload) > MAX_IMAGE_BYTES:
            raise RuntimeError(
                f"картинка больше {MAX_IMAGE_BYTES // (1024 * 1024)} МБ — Azure её не примет"
            )
        return payload

    def _ask(self, features: str, source: str, payload: bytes):
        url = f"{self._endpoint}{PATH}?api-version={API_VERSION}&features={features}"
        headers = {"Ocp-Apim-Subscription-Key": self._api_key}
        if _looks_like_url(source):
            return post(url, headers, {"url": source}, TIMEOUT_SECONDS)
        return post_bytes(url, headers, payload, TIMEOUT_SECONDS, "application/octet-stream")

    def describe(self, data: dict) -> str:
        if data.get("kind") == "text":
            return _describe_text(data)
        return _describe_scene(data)


def _looks_like_url(source: str) -> bool:
    return source.startswith("http://") or source.startswith("https://")


def _without(features: str, drop: set[str]) -> str:
    """Список features без названных, порядок остальных сохраняется.

    Порядок держим, потому что features уходит в URL, а URL — часть ключа кеша у
    Azure на их стороне; переставлять без нужды незачем.
    """
    if not drop:
        return features
    lowered = {name.lower() for name in drop}
    kept = [f for f in features.split(",") if f.strip() and f.strip().lower() not in lowered]
    return ",".join(kept)


def _region_gap(response, features: str) -> set[str]:
    """Какие из запрошенных фич отвергнуты по региону. Пусто — причина другая.

    Сначала читаем имя из самого сообщения («The feature 'Caption' is not
    supported in this region»): так снимается ровно то, чего нет, и tags с
    objects не теряются заодно. Если Azure назвал фичу иначе, чем она пишется в
    запросе, — снимаем известные региональные, иначе повтор ушёл бы с тем же
    набором и получил тот же 400.
    """
    if response.status_code != 400:
        return set()
    try:
        message = str(((response.json() or {}).get("error") or {}).get("message") or "")
    except ValueError:
        return set()
    if REGION_ERROR_MARKER not in message.lower():
        return set()

    requested = {f.strip().lower(): f.strip() for f in features.split(",") if f.strip()}
    named = {word.lower() for word in re.findall(r"'([A-Za-z]+)'", message)}
    gap = {requested[name] for name in named if name in requested}
    if gap:
        return gap
    return {requested[f.lower()] for f in REGION_LIMITED_FEATURES if f.lower() in requested}


def _error_text(response) -> str:
    """Понятная причина отказа Azure.

    Тело здесь безопасно: ключ уходит заголовком и в ответе не повторяется (в
    отличие от сервисов, которые эхом возвращают запрос). А сообщение Azure
    стоит того, чтобы его прочитать: самая частая ошибка — «feature Caption is
    not supported in this region», и по одному коду её не опознать.
    """
    try:
        error = (response.json() or {}).get("error") or {}
    except ValueError:
        error = {}
    code = error.get("code") or ""
    message = error.get("message") or ""
    detail = f"{code}: {message}".strip(": ")
    return f"HTTP {response.status_code}" + (f" ({detail})" if detail else "")


def _compact(raw: dict, features: str) -> dict:
    """Выжимка. Полный ответ Azure — это боксы и полигоны на каждое слово,
    мегабайты JSON на один скриншот; голосом называются два-три факта."""
    if "read" in features:
        return _compact_text(raw)
    return _compact_scene(raw)


def _section(raw: dict, name: str) -> dict:
    """Раздел ответа словарём, что бы там ни лежало на самом деле.

    Мягко не только про отсутствие ключа, но и про его тип: у Azure
    одновременно живут несколько версий API, и раздел, который вчера был
    объектом, завтра приходит строкой. `(raw.get(x) or {}).get(...)` от этого не
    спасает — падает на .get у строки, посреди разговора.
    """
    value = (raw or {}).get(name)
    return value if isinstance(value, dict) else {}


def _values(raw: dict, name: str) -> list:
    """Список values из раздела — по той же причине, что и _section."""
    value = _section(raw, name).get("values")
    return value if isinstance(value, list) else []


def _compact_text(raw: dict) -> dict:
    """OCR: строки текста без координат.

    Полигоны каждого слова панели не нужны, а в кеше они занимают в сотни раз
    больше самого текста. Схему разбираем мягко (см. _section): у Azure живут
    несколько версий API одновременно, и «упало на KeyError посреди разговора»
    — худший исход несовпадения.
    """
    blocks = _section(raw, "readResult").get("blocks")
    lines: list[str] = []
    for block in blocks if isinstance(blocks, list) else []:
        if not isinstance(block, dict):
            continue
        block_lines = block.get("lines")
        for line in block_lines if isinstance(block_lines, list) else []:
            if isinstance(line, dict) and line.get("text"):
                lines.append(str(line["text"]))
    return {
        "kind": "text",
        "lines": lines[:KEEP_LINES],
        "truncated": len(lines) > KEEP_LINES,
        "text": "\n".join(lines[:KEEP_LINES]),
    }


def _compact_scene(raw: dict) -> dict:
    caption_result = _section(raw, "captionResult")
    caption = caption_result.get("text") or ""
    confidence = float(caption_result.get("confidence") or 0.0)

    tags = []
    for tag in _values(raw, "tagsResult"):
        if not isinstance(tag, dict):
            continue
        if float(tag.get("confidence") or 0.0) < MIN_TAG_CONFIDENCE:
            continue
        if tag.get("name"):
            tags.append(str(tag["name"]))

    objects = []
    for item in _values(raw, "objectsResult"):
        if not isinstance(item, dict):
            continue
        names = item.get("tags") or []
        if names and isinstance(names[0], dict) and names[0].get("name"):
            objects.append(str(names[0]["name"]))

    people = len(_values(raw, "peopleResult"))
    return {
        "kind": "scene",
        "caption": caption,
        "confidence": round(confidence, 3),
        "tags": tags[:KEEP_TAGS],
        "objects": objects[:KEEP_TAGS],
        "people": people,
    }


def _describe_text(data: dict) -> str:
    lines = data.get("lines") or []
    if not lines:
        return "Текста на картинке не нашлось"
    body = " ".join(lines)
    answer = f"Прочитал: {body}"
    if data.get("truncated"):
        # Иначе обрезанный текст звучит как весь текст, и человек сделает вывод
        # по половине документа.
        answer += ". Это только начало, остальное в панели"
    return answer


def _describe_scene(data: dict) -> str:
    """Ответ голосом. Про язык предупреждаем всегда: русского описания у Azure
    нет, и английская фраза посреди русского ответа иначе выглядит как сбой.

    Две формы, и вторая не хуже первой, а короче. Есть caption — звучит фраза.
    Нет (регион без caption, см. REGION_ERROR_MARKER) — звучит перечисление
    того, что видно. Писать в этом случае «Описания нет» неправильно: человек
    слышит отказ, хотя ответ есть, просто он списком.
    """
    caption = data.get("caption") or ""
    tags = data.get("tags") or []
    # objects — запасной путь: у пустого кадра теги отсеиваются порогом, а
    # объект остаётся, и промолчать при этом было бы неправдой.
    seen = tags or data.get("objects") or []
    if caption:
        answer = f"На картинке (по-английски): {caption}"
        if tags:
            answer += f". Теги: {', '.join(tags[:5])}"
        return answer
    if seen:
        return f"Вижу на картинке (по-английски): {', '.join(seen[:5])}"
    return "Не смог разобрать, что на картинке"
