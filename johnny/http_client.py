"""Тонкая обёртка над requests для облачных провайдеров (fish.audio, Groq):
общий браузерный User-Agent (иначе Cloudflare режет запрос), разовое
предупреждение об ошибке в лог (warn_once) и короткая пауза после отказа
(cooldown) — чтобы при пропавшем интернете не ждать полный сетевой таймаут
на КАЖДУЮ произнесённую фразу."""

import logging
import time

import requests

logger = logging.getLogger(__name__)

# И fish.audio, и Groq стоят за Cloudflare, который отдаёт «error code: 1010»
# любому клиенту без браузерного User-Agent. Заголовок ОБЯЗАТЕЛЕН — на этом
# уже потеряли час, не удалять как лишний.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0 Safari/537.36"
)

# Ключи ошибок, о которых уже предупреждали в этом запуске.
_warned: set[str] = set()


def warn_once(key: str, message: str) -> None:
    """Первую ошибку каждого вида — в WARNING, повторы — в DEBUG.

    Пропавший интернет иначе заливает johnny.log одной и той же строкой на
    каждую произнесённую фразу; на это уже наступали в цикле прослушивания.
    """
    if key in _warned:
        logger.debug("%s", message)
        return
    _warned.add(key)
    logger.warning("%s", message)


def post(url: str, headers: dict, payload: dict, timeout: float) -> requests.Response:
    """POST с JSON-телом и браузерным User-Agent. Возвращает Response как есть:
    fish отдаёт mp3 (.content), Groq — JSON (.json())."""
    return requests.post(
        url, headers={**headers, "User-Agent": USER_AGENT}, json=payload, timeout=timeout
    )


def post_stream(url: str, headers: dict, payload: dict, timeout: float) -> requests.Response:
    """POST, ответ которого читается по мере поступления (SSE у Groq).

    Отличие от post ровно одно — stream=True, и оно принципиально: без него
    requests скачивает тело целиком прежде, чем вернуть управление, то есть
    весь смысл стриминга пропадает МОЛЧА — код при этом выглядит рабочим.

    timeout здесь — время до ПЕРВОГО байта, а не на весь ответ: длинный поток
    законно идёт дольше, и общего потолка на него нет.
    """
    return requests.post(
        url,
        headers={**headers, "User-Agent": USER_AGENT},
        json=payload,
        timeout=timeout,
        stream=True,
    )


def post_form(
    url: str, headers: dict, data: dict, timeout: float, files: dict | None = None
) -> requests.Response:
    """POST multipart/form-data — для сервисов, которые не принимают JSON.

    Face++ ждёт именно форму: и ключ с секретом, и снимки уходят полями, а
    JSON-тело он отвергает. files оставлен необязательным, чтобы тем же вызовом
    уходила и одна строка, и файл — иначе у каждого коннектора появилась бы своя
    почти одинаковая обёртка над requests.
    """
    return requests.post(
        url,
        headers={**headers, "User-Agent": USER_AGENT},
        data=data,
        files=files,
        timeout=timeout,
    )


def post_bytes(
    url: str, headers: dict, payload: bytes, timeout: float, content_type: str
) -> requests.Response:
    """POST с сырым телом — файл уходит как есть, без multipart-обёртки.

    Нужен Azure AI Vision: локальную картинку он принимает только телом с
    Content-Type: application/octet-stream, а post_form завернул бы её в
    multipart и сервис ответил бы «content type not supported». Тип передаётся
    параметром, а не константой: тот же путь годится любому сервису, который
    ждёт сырые байты с собственным типом.
    """
    return requests.post(
        url,
        headers={**headers, "User-Agent": USER_AGENT, "Content-Type": content_type},
        data=payload,
        timeout=timeout,
    )


def get(url: str, headers: dict, timeout: float) -> requests.Response:
    """GET с тем же браузерным User-Agent — для health-check'ов (TTS-сервис).

    Заголовок нужен по той же причине, что и в post: за облачным сервисом
    может стоять Cloudflare или похожий прокси, который режет клиентов без
    User-Agent, и health молча выглядел бы как «сервис лежит».
    """
    return requests.get(url, headers={**headers, "User-Agent": USER_AGENT}, timeout=timeout)


# Момент последнего отказа каждого провайдера (по time.monotonic()). 10-секундный
# сетевой таймаут на КАЖДУЮ фразу при пропавшем интернете превращает голосового
# ассистента в неотзывчивого болвана; а раз сеть не чинится сама за секунды,
# нет смысла пытаться снова раньше, чем через _COOLDOWN_SECONDS.
_COOLDOWN_SECONDS = 60.0
_last_failure: dict[str, float] = {}


def mark_failure(key: str) -> None:
    """Запомнить момент отказа провайдера key — с него отсчитывается пауза."""
    _last_failure[key] = time.monotonic()


def in_cooldown(key: str) -> bool:
    """Не остыл ли провайдер key после последнего отказа (см. mark_failure)."""
    last = _last_failure.get(key)
    return last is not None and (time.monotonic() - last) < _COOLDOWN_SECONDS
