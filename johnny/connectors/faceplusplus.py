"""Face++: разобрать лицо на снимке и сравнить два лица между собой.

Пришёл на место FaceCheck.ID, и подмена тут ещё существеннее, чем у картинок —
записываю, чтобы её не приняли за то же самое. FaceCheck искал лицо ПО ВСЕМУ
ИНТЕРНЕТУ: на входе фотография, на выходе страницы, где этот человек ещё
встречается. Это деанонимизация. Face++ по интернету не ходит вовсе. Он умеет
три вещи: найти лица в кадре и их признаки (detect), сказать, один ли человек
на двух снимках (compare), и поискать лицо в наборе, который вы загрузили сами.

Практически это значит: команда «найди это лицо» больше не существует, потому
что выполнить её нечем. Вместо неё — «сравни лица» и «что за лицо». Для
приватности это шаг вперёд: чужой человек по снимку больше не находится.

Решения, которые важно не «упростить» обратно:

  - requires_consent = True, как и у предшественника. Биометрия остаётся
    биометрией, даже когда сервис не ищет по индексу: лицо всё равно уезжает
    третьей стороне, и делать это молча нельзя.
  - Каждый вызов пишется в журнал доступа (log_path). Требование плана —
    «логирование доступа»; без него не ответить на вопрос «чьи лица и когда мы
    отправляли», а его рано или поздно задают.
  - Ключ И секрет уходят полями ФОРМЫ, не строкой запроса. Face++ принимает и
    так и так, но query string оседает в логах любого прокси по пути — это
    известный способ потерять секрет, не заметив.
  - Гендер у признаков не запрашивается сознательно. Face++ отдаёт его как
    выбор из двух, ни одной команде Джони он не нужен, а ошибка в нём — ошибка
    про живого человека. Возраст с эмоцией просят прямо, их и берём.
  - Вердикт «тот же человек» считается по ПОРОГУ из ответа, а не по confidence
    на глаз. Face++ присылает thresholds для трёх уровней ложных совпадений;
    «сходство 72» само по себе не значит ничего, и назвать его вслух без порога
    — предложить человеку сделать вывод по числу, которое он не с чем сравнить.

Ограничения сервиса: JPG или PNG, сторона от 48 до 4096 точек, файл до 2 МБ.
Проверяем размер сами — свой отказ понятнее чужого INVALID_IMAGE_SIZE и не
тратит квоту.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from ..http_client import post_form
from .base import Connector

API_BASE = "https://api-us.faceplusplus.com/facepp/v3"

DETECT = "detect"
COMPARE = "compare"

TIMEOUT_SECONDS = 30.0
MAX_IMAGE_BYTES = 2 * 1024 * 1024
KEEP_FACES = 5

# Признаки без гендера — см. шапку. facequality и blur нужны не для красоты:
# по мутному кадру сравнение выдаёт уверенное «не тот человек», и об этом надо
# предупредить, а не молча отдать вердикт.
DETECT_ATTRIBUTES = "age,emotion,facequality,blur"

# Уровень ложных совпадений, по которому судим. 1e-5 — самый строгий из трёх,
# что присылает Face++: один ложный положительный на сто тысяч сравнений.
# Брать более мягкий значило бы чаще называть разных людей одним.
STRICT_THRESHOLD = "1e-5"


class FacePlusPlusConnector(Connector):
    name = "faceplusplus"
    # Как и у предшественника, True не обсуждается.
    requires_consent = True
    ttl_seconds = 7 * 86400.0
    daily_quota = 50
    # Бесплатный ключ ограничен не месячной квотой, а одновременностью: на пачку
    # запросов подряд приходит CONCURRENCY_LIMIT_EXCEEDED. Выжидаем сами — отказ
    # сервиса ушёл бы в cooldown и заглушил следующий вызов.
    #
    # Замер на живом ключе 2026-08-08: секунды НЕ хватает, при паузе меньше 8 с
    # отказ приходит стабильно. Отсюда 10 — с запасом к верхней границе замера
    # (8–12 с), потому что цена ошибки несимметрична: лишние пара секунд стоят
    # ожидания, а отказ стоит ответа человеку и попадает в cooldown.
    #
    # Из этой цифры следует и потолок на пакетные сценарии: сравнение пяти
    # кандидатов — это минута, и «сравни всё со всем» на таком ключе не живёт
    # (см. details/integrate_local_face_index.md — отбор поэтому локальный).
    min_interval_seconds = 10.0

    def __init__(
        self,
        *,
        api_key: str = "",
        api_secret: str = "",
        endpoint: str = API_BASE,
        log_path=None,
        daily_quota=None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._api_key = api_key
        self._api_secret = api_secret
        self._endpoint = (endpoint or API_BASE).rstrip("/")
        self._log_path = Path(log_path) if log_path else self.cache_dir / "face-access.log"
        if daily_quota is not None:
            self.daily_quota = int(daily_quota)

    def available(self) -> tuple[bool, str]:
        # Ключ и секрет — две отдельные строки в консоли Face++, и забывают
        # обычно вторую. Причина отказа называет именно её, иначе человек будет
        # перепроверять первую.
        if not self._api_key or not self._api_secret:
            return False, "Для работы с лицами нужны ключ и секрет Face++ в secrets.yaml"
        return True, ""

    def cache_params(self, params: dict) -> dict:
        """Ключ — по содержимому снимков, не по путям.

        Причина та же, что была у предшественника: screenshot.png
        перезаписывается каждым новым снимком, и ключ по пути отдал бы вердикт
        про вчерашнего человека. В этом коннекторе это не «неточность», а ответ
        про другое лицо.
        """
        mode = str(params.get("mode") or DETECT)
        digests = [_digest(params.get(field, "")) for field in ("path", "first", "second")]
        return {"mode": mode, "images": digests}

    def _run(self, **params) -> dict:
        mode = str(params.get("mode") or DETECT)
        if mode == COMPARE:
            data = self._compare(params.get("first", ""), params.get("second", ""))
            self._log_access(mode, [params.get("first", ""), params.get("second", "")], data)
            return data
        data = self._detect(params.get("path", ""))
        self._log_access(mode, [params.get("path", "")], data)
        return data

    def _detect(self, source) -> dict:
        with _opened(source) as image:
            answer = post_form(
                f"{self._endpoint}/{DETECT}",
                {},
                {**self._credentials(), "return_attributes": DETECT_ATTRIBUTES},
                TIMEOUT_SECONDS,
                files={"image_file": image},
            ).json()
        _raise_on_error(answer)
        return _compact_detect(answer)

    def _compare(self, first, second) -> dict:
        if not first or not second:
            raise RuntimeError("нужны два снимка")
        # Оба файла открыты одновременно: Face++ сравнивает их одним запросом, и
        # разбивать его на два нельзя — face_token живёт ограниченное время, а
        # лишний вызов тратит квоту.
        with _opened(first) as one, _opened(second) as two:
            answer = post_form(
                f"{self._endpoint}/{COMPARE}",
                {},
                self._credentials(),
                TIMEOUT_SECONDS,
                files={"image_file1": one, "image_file2": two},
            ).json()
        _raise_on_error(answer)
        return _compact_compare(answer)

    def _credentials(self) -> dict:
        """Ключ и секрет полями формы — см. шапку про строку запроса."""
        return {"api_key": self._api_key, "api_secret": self._api_secret}

    def _log_access(self, mode: str, sources: list, data: dict) -> None:
        """Журнал доступа: какие лица отправляли и что получили.

        Пишем имя файла и хеш, а не сам снимок и не признаки лица. Падать из-за
        журнала нельзя, но и молча его терять тоже: ошибка уйдёт в общий лог
        через warn_once вызывающей стороны, здесь только не мешаем результату
        дойти до человека.
        """
        record = {
            "at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "mode": mode,
            "files": [Path(str(s)).name for s in sources if s],
            "hashes": [_digest(s)[:12] for s in sources if s],
            "faces": data.get("faces", data.get("total", 0)),
        }
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            with self._log_path.open("a", encoding="utf-8") as log:
                log.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def describe(self, data: dict) -> str:
        if data.get("kind") == COMPARE:
            return _describe_compare(data)
        return _describe_detect(data)


def _digest(source) -> str:
    """Хеш содержимого файла или сам путь, если файла нет.

    Внятная ошибка про отсутствующий файл приходит из _run: две одинаковые
    проверки в двух местах рано или поздно разойдутся.
    """
    text = str(source or "")
    if not text:
        return ""
    try:
        return hashlib.sha1(Path(text).read_bytes()).hexdigest()
    except OSError:
        return text


def _opened(source):
    """Открытый на чтение снимок с проверкой размера до отправки."""
    text = str(source or "").strip()
    # Пустую строку надо отбить здесь и явно: Path("") — это Path("."), то есть
    # текущий каталог. Он существует, размер у него есть, и проверки ниже его
    # пропускают — а open() падает с «Permission denied: '.'». Человек слышал бы
    # errno про каталог вместо «снимок не назван».
    if not text:
        raise RuntimeError("не указан снимок")
    path = Path(text)
    if not path.is_file():
        raise RuntimeError(f"файла нет: {path.name or source}")
    size = path.stat().st_size
    if not size:
        raise RuntimeError("файл пустой")
    if size > MAX_IMAGE_BYTES:
        raise RuntimeError(
            f"снимок больше {MAX_IMAGE_BYTES // (1024 * 1024)} МБ — Face++ его не примет"
        )
    return path.open("rb")


def _raise_on_error(answer: dict) -> None:
    """error_message Face++ → исключение, его переведёт в отказ base.call().

    Сообщения сервиса безопасны: ключ и секрет уходят полями формы и в ответе не
    повторяются. А различать их надо — INVALID_IMAGE_SIZE и
    CONCURRENCY_LIMIT_EXCEEDED требуют разных действий от человека.
    """
    message = (answer or {}).get("error_message")
    if message:
        raise RuntimeError(str(message))


def _compact_detect(answer: dict) -> dict:
    """Выжимка detect: сколько лиц и признаки самого крупного.

    Face++ отдаёт на каждое лицо прямоугольник, сотню точек разметки и все
    запрошенные признаки; в кеше это на порядки больше, чем нужно голосу.
    Схему разбираем мягко (get, а не []): падать посреди разговора из-за
    несовпадения схемы — худший исход.
    """
    faces = (answer or {}).get("faces") or []
    if not isinstance(faces, list):
        faces = []
    compact = []
    for face in faces[:KEEP_FACES]:
        if not isinstance(face, dict):
            continue
        attributes = face.get("attributes") or {}
        emotion = attributes.get("emotion") or {}
        compact.append(
            {
                "age": int((attributes.get("age") or {}).get("value") or 0),
                # Самая сильная эмоция, а не все семь: вслух перечислять
                # проценты по каждой незачем.
                "emotion": max(emotion, key=emotion.get) if emotion else "",
                "quality": round(float((attributes.get("facequality") or {}).get("value") or 0), 1),
                "blur": round(
                    float(
                        ((attributes.get("blur") or {}).get("blurness") or {}).get("value") or 0
                    ),
                    1,
                ),
            }
        )
    return {"kind": DETECT, "faces": len(faces), "details": compact}


def _compact_compare(answer: dict) -> dict:
    """Выжимка compare: сходство, строгий порог и готовый вердикт.

    Вердикт считаем здесь, а не в describe: он же нужен панели и журналу, а
    повторять сравнение с порогом в трёх местах — способ получить три разных
    ответа на один вопрос.
    """
    thresholds = (answer or {}).get("thresholds") or {}
    confidence = float((answer or {}).get("confidence") or 0.0)
    strict = float(thresholds.get(STRICT_THRESHOLD) or 0.0)
    return {
        "kind": COMPARE,
        "confidence": round(confidence, 2),
        "threshold": round(strict, 2),
        "same": bool(strict and confidence >= strict),
    }


def _describe_detect(data: dict) -> str:
    faces = int(data.get("faces") or 0)
    if not faces:
        return "Лиц на снимке не нашлось"
    details = data.get("details") or []
    first = details[0] if details else {}
    answer = f"Лиц на снимке: {faces}"
    if first.get("age"):
        answer += f". Возраст навскидку {first['age']}"
    if first.get("emotion"):
        answer += f", выражение — {first['emotion']}"
    if first.get("blur", 0) > 50:
        # Мутный кадр — причина не верить остальному, и сказать об этом надо
        # раньше, чем человек сделает вывод.
        answer += ". Кадр смазан, признакам верить не стоит"
    return answer


def _describe_compare(data: dict) -> str:
    confidence = data.get("confidence", 0)
    threshold = data.get("threshold", 0)
    # Порог называем всегда: «сходство 72» без него не значит ничего.
    verdict = "похоже, один человек" if data.get("same") else "скорее разные люди"
    return f"Сходство {confidence} при пороге {threshold} — {verdict}"
