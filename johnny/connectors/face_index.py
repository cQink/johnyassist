"""Локальный индекс лиц: «есть ли этот человек в МОЁМ архиве и на каких снимках».

Границу надо назвать сразу, потому что имя пункта в плане обещает больше, чем
код умеет, и один раз мы на этом уже обожглись (TinEye → Azure). Здесь ищется
похожее лицо среди снимков, которые человек **сам** отдал в индекс. По
интернету коннектор не ходит вовсе, и в пустом индексе находит ноль. Вопрос
«кто этот незнакомец» он не закрывает — на него отвечали FaceCheck.ID и
PimEyes, и без обхода чужих площадок он не решается.

Решения, которые важно не «упростить» обратно:

  - `requires_consent = False`, и это не послабление: наружу не уходит ничего,
    вектор считается на этой машине. Согласие у нас значит «разрешаю отправить
    третьей стороне», и требовать его там, где отправки нет, — приучать
    человека нажимать «да» не глядя.
  - Зато **сам индекс — биометрия на диске**: чужие лица, сведённые в один
    файл, без снимков, но пригодные для опознания. Поэтому он лежит в
    игнорируемом git'ом `models/`, наравне с ключами, а пополняется только по
    явно названному человеком пути — сам по диску не ходит.
  - Отказ «нет пакета» и отказ «индекс пуст» — РАЗНЫЕ. Первый чинится
    установкой, второй пополнением, и слить их в одно «поиск недоступен»
    значит отправить человека чинить не то.
  - Снимок читаем через `np.fromfile` + `cv2.imdecode`, а не `cv2.imread`: тот
    на Windows молча возвращает None для пути с кириллицей, и «лица не нашлось»
    прозвучало бы вместо «файл не прочитался».
  - Больше одного лица в запросе — отдельный ответ, а не «берём самое крупное».
    Молчаливый выбор здесь означает поиск не того человека, и узнать об этом
    человек сможет только по неверному ответу.

Замеры. Косинус двух РАЗНЫХ людей — 0.026 при пороге сходства 0.35–0.40, то
есть разделение чистое, вектор 512-мерный, а полный перебор по 10 000 лиц
(20 МБ) уходит в доли миллисекунды. Ни faiss, ни векторная база не нужны, и
заводить их без замера, который показал бы обратное, не надо.

А вот «модель грузится 4.1 с» из спеки оказалось неправдой (перемерено
2026-08-08). Настоящая раскладка первого поиска: **импорт onnxruntime 24–25 с**
(пять запусков подряд, разброс меньше секунды), сама модель — construct 1.3 с
плюс prepare 1.0 с, снимок 0.8 с. Загрузка .dll при этом занимает 0.8 с, то
есть время уходит в инициализацию расширения, а не в диск. Отсюда два решения
ниже: `available()` не смеет импортировать пакет, а `prewarm()` существует
затем, чтобы эти 25 секунд заплатил фоновый поток, а не молчащий Джони.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

from .base import Connector

# Индекс — рядом с моделями, в уже игнорируемом git'ом каталоге. Это требование
# приватности, а не удобства: см. шапку.
DEFAULT_INDEX_DIR = Path("models") / "face-index"

VECTORS_NAME = "vectors.npy"
PATHS_NAME = "paths.json"

MODEL_NAME = "buffalo_l"

# Корень для InsightFace. Он дописывает "models/<name>" сам, поэтому "." даёт
# models/buffalo_l — конвенция проекта. По умолчанию пакет качает в
# ~/.insightface/models, мимо неё.
MODEL_ROOT = "."

# Сколько кандидатов возвращаем. Пять — не вкус, а следствие чужого лимита:
# вердикт по ним выносит Face++, а у него пауза 10 с между вызовами, то есть
# пятёрка это минута ожидания (см. connectors/faceplusplus.py).
TOP_N = 5

# Порог уверенного совпадения. Ниже — «похоже, но проверьте», и именно такие
# кандидаты имеет смысл отдавать Face++ вторым мнением.
MATCH_THRESHOLD = 0.40

# Ниже этого не показываем вовсе: при косинусе разных людей 0.026 всё, что
# около нуля, — шум, и называть его «кандидатом» значит врать числом.
CANDIDATE_THRESHOLD = 0.28

# Размер картинки для детектора. 640 — значение по умолчанию у InsightFace, на
# нём и меряли; менять его значит обесценить замеры выше.
DET_SIZE = (640, 640)

# Модель и сессия onnxruntime живут секунды и десятки мегабайт, а коннектор
# собирается factory.build() на КАЖДЫЙ вызов — тот же капкан, что у клиента SDK
# в brain_anthropic. Держим одну на процесс, ключ — корень модели.
_apps: dict[str, object] = {}


class FaceIndexConnector(Connector):
    name = "face-index"
    # Наружу не уходит ничего — см. шапку.
    requires_consent = False
    # Ответ зависит от содержимого индекса, а оно меняется пополнением. Отпечаток
    # индекса входит в ключ кеша (cache_params), так что долгий TTL безопасен:
    # пополнили — ключ другой, старый ответ не всплывёт.
    ttl_seconds = 7 * 86400.0
    daily_quota = 0
    min_interval_seconds = 0.0

    def __init__(self, *, index_dir=None, model_root: str = MODEL_ROOT, top: int = TOP_N, **kwargs):
        super().__init__(**kwargs)
        self._index_dir = Path(index_dir) if index_dir else DEFAULT_INDEX_DIR
        self._model_root = str(model_root or MODEL_ROOT)
        self._top = max(1, int(top or TOP_N))

    # --- готовность ---

    def available(self) -> tuple[bool, str]:
        if not _package_present():
            return False, (
                "Для поиска по архиву лиц нужен пакет insightface: pip install insightface"
            )
        if not self.index_size():
            return False, (
                "Архив лиц пуст — сначала пополните его: "
                "python -m johnny.connectors.face_index <папка со снимками>"
            )
        return True, ""

    def index_size(self) -> int:
        """Сколько лиц в индексе. 0 и «файла нет» — это одно и то же состояние."""
        try:
            return len(_read_paths(self._index_dir))
        except (OSError, ValueError):
            return 0

    # --- кеш ---

    def cache_params(self, params: dict) -> dict:
        """Снимок — по содержимому, индекс — по отпечатку.

        Путь в ключ не годится по той же причине, что у Azure и Face++:
        screenshot.png перезаписывается каждым новым снимком. А отпечаток
        индекса нужен потому, что ответ зависит и от него: пополнили архив —
        тот же снимок обязан искаться заново, иначе новый человек в нём никогда
        не найдётся.
        """
        return {
            "image": _digest(params.get("path", "")),
            "index": _index_fingerprint(self._index_dir),
            "top": self._top,
        }

    # --- вызов ---

    def _run(self, **params) -> dict:
        # Индекс первым, хотя он и не нужен для детекции: он дешёвый, а модель
        # стоит 4.1 с загрузки. Порченый архив должен отказать сразу, а не
        # после того, как человек уже подождал.
        vectors, paths = _load_index(self._index_dir)
        faces = detect_faces(str(params.get("path", "")), self._model_root)

        # Ноль лиц и несколько лиц — НЕ исключения, хотя соблазн велик: у
        # соседних коннекторов плохой ввод бросается RuntimeError'ом. Но base
        # переводит любое исключение в глухое «face-index не ответил» (текст
        # ошибки прячется сознательно — у сетевых коннекторов в нём бывает
        # адрес с ключом) и вдобавок ставит cooldown. Здесь и сети нет, и
        # причина безобидная: человек дал не тот кадр, и услышать он должен
        # именно это. Поэтому — обычный ответ с полем faces.
        if len(faces) != 1:
            return {"faces": len(faces), "matches": [], "searched": len(paths)}
        vector = faces[0].normed_embedding

        # Оба вектора нормированы (normed_embedding), поэтому скалярное
        # произведение и есть косинус — отдельная нормировка тут была бы
        # лишней работой на каждом лице.
        scores = vectors @ vector

        # Лучший результат на снимок: одно фото может дать несколько лиц, и
        # вопрос звучит «на каких СНИМКАХ», а не «на каких лицах». Без этого
        # групповое фото вытеснило бы из пятёрки все остальные.
        best: dict[str, float] = {}
        for path, score in zip(paths, scores.tolist()):
            if score > best.get(path, -1.0):
                best[path] = score

        ranked = sorted(best.items(), key=lambda pair: pair[1], reverse=True)
        matches = [
            {"path": path, "score": round(float(score), 3), "same": score >= MATCH_THRESHOLD}
            for path, score in ranked[: self._top]
            if score >= CANDIDATE_THRESHOLD
        ]
        return {
            "faces": 1,
            "matches": matches,
            "searched": len(paths),
            "threshold": MATCH_THRESHOLD,
        }

    # --- голос ---

    def describe(self, data: dict) -> str:
        searched = int(data.get("searched") or 0)

        # Про снимок — раньше, чем про архив: «не нашёл» по кадру без лица это
        # не утверждение о людях в архиве, и путать эти два ответа нельзя.
        faces = data.get("faces")
        if faces == 0:
            return "На снимке нет лица — нужен кадр, где человека видно"
        if faces is not None and faces > 1:
            return (
                f"На снимке {faces} лица — непонятно, кого искать. "
                "Нужен кадр с одним человеком"
            )

        matches = data.get("matches") or []
        if not matches:
            # Про размер архива говорим всегда: «не нашёл» по двум снимкам и по
            # двум тысячам — разной силы утверждения, и человек должен слышать,
            # какое из них прозвучало.
            return f"В архиве такого человека нет. Просмотрено лиц: {searched}"

        top = matches[0]
        name = Path(top["path"]).name
        if top["same"]:
            answer = f"Похоже, это он: {name}, сходство {top['score']}"
        else:
            # Ниже порога — не вердикт, а повод посмотреть. Называть это
            # находкой значит подсунуть человеку вывод, которого мы не сделали.
            answer = f"Уверенного совпадения нет. Ближе всего {name}, сходство {top['score']}"
        others = len(matches) - 1
        if others:
            answer += f", ещё {others} на проверку"
        return answer


# --- модель -----------------------------------------------------------------


def _package_present() -> bool:
    """Есть ли insightface — БЕЗ его импорта.

    find_spec, а не try/import, и это не стилистика: импорт insightface тянет
    onnxruntime, а тот на этой машине инициализируется 24–25 секунд (замер
    2026-08-08, пять запусков; сама загрузка .dll при этом 0.8 с, время уходит
    в инициализацию расширения). available() зовётся на КАЖДЫЙ вызов
    коннектора и живёт в голосовом пути — импорт там означал бы, что Джони
    молчит полминуты, даже когда собирался ответить «архив пуст». find_spec
    стоит 2 мс и на вопрос «пакет установлен?» отвечает так же точно.
    """
    try:
        return importlib.util.find_spec("insightface") is not None
    except (ImportError, ValueError):
        # Битая установка: пакет числится, но грузить нечем. Для нас это то же
        # самое, что «нет пакета», — и починка та же.
        return False


def _app(model_root: str = MODEL_ROOT):
    """Готовая модель, одна на процесс.

    Первый вызов стоит ~26 с (из них 24–25 — импорт onnxruntime), и держать её
    не в кеше значило бы платить это за каждое произнесённое «найди».
    """
    if model_root in _apps:
        return _apps[model_root]
    from insightface.app import FaceAnalysis

    app = FaceAnalysis(
        name=MODEL_NAME,
        root=model_root,
        # Только детектор и признаки: возраст, пол и разметка нам не нужны, а
        # каждая лишняя модель — своя сессия onnxruntime и свои секунды старта.
        allowed_modules=["detection", "recognition"],
        providers=["CPUExecutionProvider"],
    )
    # ctx_id=-1 — CPU. cuda на этой машине не работает (замер 2026-08-08,
    # см. settings.yaml), и просить её значит тратить секунды на откат.
    app.prepare(ctx_id=-1, det_size=DET_SIZE)
    _apps[model_root] = app
    return app


def detect_faces(path: str, model_root: str = MODEL_ROOT) -> list:
    """Лица на снимке. Пустой список — лиц нет; исключение — файл не прочитался."""
    image = _read_image(path)
    app = _app(model_root)
    faces = list(app.get(image))
    if faces or not _looks_cropped(image):
        return faces
    # Второй заход с полями — см. _padded. Платим за него только там, где иначе
    # уже собрались сказать «лица нет».
    return list(app.get(_padded(image)))


# Выше какого размера второй заход не делаем. Обрезанный под лицо кадр — это
# аватарка, а она мельче: у пейзажа на 4000 px лицо в край не упирается, и
# лишняя детекция там просто удвоила бы время обхода архива на каждом снимке
# без людей.
CROP_LIMIT = 1024


def _looks_cropped(image) -> bool:
    """Похоже ли на кадр, обрезанный вплотную по лицу."""
    height, width = image.shape[:2]
    return max(height, width) <= CROP_LIMIT


def _padded(image):
    """Тот же снимок с полями по краям.

    Зачем: детектор ищет лицо В кадре, и на вплотную обрезанном портрете ему не
    за что зацепиться — контекста вокруг лица нет. Проверено на родном сэмпле
    insightface (`Tom_Hanks_54745.png`, 112×112): как есть — 0 лиц, увеличенный
    вчетверо — тоже 0, с полями в половину стороны — 1. То есть дело не в
    разрешении, а именно в отсутствии полей.

    Случай не редкий: аватарка из мессенджера обрезана ровно так, и без этого
    Джони отвечал бы «на снимке нет лица» на снимок, где лицо занимает весь
    кадр — то есть выглядел бы сломанным.

    BORDER_REPLICATE, а не чёрные поля: резкая рамка сама по себе даёт контур,
    который детектор иногда принимает за край объекта.

    Координаты найденных лиц после этого сдвинуты на ширину поля. Нам всё равно
    — мы берём только `normed_embedding`, он считается по выровненному вырезу.
    Кто станет читать отсюда bbox, про сдвиг обязан помнить.
    """
    import cv2

    height, width = image.shape[:2]
    pad = max(height, width) // 2
    return cv2.copyMakeBorder(image, pad, pad, pad, pad, cv2.BORDER_REPLICATE)


def prewarm(model_root: str = MODEL_ROOT) -> bool:
    """Заплатить за импорт onnxruntime заранее, ВНЕ голосового пути.

    Первый поиск по архиву на этой машине стоит ~26 секунд, и почти всё это —
    инициализация onnxruntime при импорте (24–25 с), а вовсе не модель
    (construct 1.3 с + prepare 1.0 с). Второй и дальше — доли секунды на
    поиск плюс 0.8 с на снимок.

    То есть цена платится один раз за процесс, и вопрос только в том, слышит
    ли её человек. Вызывать из фонового потока-демона; никогда не бросает.
    """
    try:
        _app(model_root)
        return True
    except Exception:
        # Молча: нет пакета, нет модели, нет диска — коннектор сам скажет об
        # этом человеку через available(), когда его позовут.
        return False


def _read_image(path: str):
    """Снимок как массив BGR.

    Через fromfile+imdecode, а не cv2.imread: тот на Windows не понимает путей
    с кириллицей и возвращает None молча — «лица не нашлось» вместо «файл не
    прочитался». Архив семейных фото это ровно тот случай, где такие пути и
    водятся.
    """
    import cv2
    import numpy as np

    text = str(path or "").strip()
    if not text:
        raise RuntimeError("не указан снимок")
    file = Path(text)
    if not file.is_file():
        raise RuntimeError(f"файла нет: {file.name or text}")
    raw = np.fromfile(file, dtype=np.uint8)
    if not raw.size:
        raise RuntimeError("файл пустой")
    image = cv2.imdecode(raw, cv2.IMREAD_COLOR)
    if image is None:
        raise RuntimeError(f"не похоже на картинку: {file.name}")
    return image


# --- индекс -----------------------------------------------------------------


def _read_paths(index_dir: Path) -> list[str]:
    file = Path(index_dir) / PATHS_NAME
    if not file.is_file():
        return []
    data = json.loads(file.read_text(encoding="utf-8"))
    paths = data.get("paths") if isinstance(data, dict) else data
    return list(paths or [])


def _load_index(index_dir: Path):
    """Векторы и пути. Рассинхрон между ними — отказ, а не «поищем по меньшему»:
    поиск по сдвинутым парам вернёт чужое имя с высоким сходством, то есть
    уверенно неверный ответ."""
    import numpy as np

    index_dir = Path(index_dir)
    vectors_file = index_dir / VECTORS_NAME
    if not vectors_file.is_file():
        raise RuntimeError("архив лиц пуст")
    vectors = np.load(vectors_file)
    paths = _read_paths(index_dir)
    if len(vectors) != len(paths):
        raise RuntimeError(
            f"архив повреждён: {len(vectors)} векторов на {len(paths)} записей — пересоберите его"
        )
    return vectors, paths


def _index_fingerprint(index_dir: Path) -> str:
    """Короткий отпечаток индекса для ключа кеша.

    Размер и время правки, а не хеш содержимого: файл на 10 000 лиц — 20 МБ, и
    читать их целиком ради ключа кеша на каждый вызов дороже самого поиска.
    """
    file = Path(index_dir) / VECTORS_NAME
    try:
        stat = file.stat()
    except OSError:
        return "empty"
    return f"{stat.st_size}-{int(stat.st_mtime)}"


def add_to_index(sources, index_dir=None, model_root: str = MODEL_ROOT) -> dict:
    """Добавить снимки в индекс. sources — файлы и/или папки, названные ЯВНО.

    Сам по диску не ходим: индекс — биометрия, и «Джони обошёл все ваши папки»
    это не та функция, которую включают молча.

    Возвращает сводку, а не бросает на первом плохом файле: в архиве из тысячи
    снимков десяток нечитаемых — норма, и падать на них значит не собрать
    индекс никогда.
    """
    import numpy as np

    index_dir = Path(index_dir) if index_dir else DEFAULT_INDEX_DIR
    files = _collect(sources)

    known = _read_paths(index_dir)
    seen = set(known)
    vectors: list = []
    paths: list[str] = []
    skipped: list[tuple[str, str]] = []
    no_face = 0

    for file in files:
        key = str(file)
        if key in seen:
            continue
        try:
            faces = detect_faces(key, model_root)
        except Exception as error:
            skipped.append((file.name, str(error)))
            continue
        if not faces:
            no_face += 1
            continue
        # Все лица со снимка, а не только главное: на групповом фото человек
        # может быть и не в центре, а вопрос звучит «есть ли он в архиве».
        for face in faces:
            vectors.append(face.normed_embedding)
            paths.append(key)

    if vectors:
        fresh = np.asarray(vectors, dtype="float32")
        old_file = index_dir / VECTORS_NAME
        if old_file.is_file() and known:
            fresh = np.vstack([np.load(old_file), fresh])
        index_dir.mkdir(parents=True, exist_ok=True)
        np.save(index_dir / VECTORS_NAME, fresh)
        (index_dir / PATHS_NAME).write_text(
            json.dumps({"paths": known + paths}, ensure_ascii=False, indent=1),
            encoding="utf-8",
        )

    return {
        "added_faces": len(vectors),
        "added_photos": len(set(paths)),
        "total_faces": len(known) + len(paths),
        "no_face": no_face,
        "skipped": skipped,
    }


def _collect(sources) -> list[Path]:
    """Файлы из явно названных путей. Папка — только её содержимое, вглубь
    рекурсивно: архив снимков почти всегда разложен по годам и событиям."""
    if isinstance(sources, (str, Path)):
        sources = [sources]
    suffixes = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
    found: list[Path] = []
    for source in sources or []:
        path = Path(str(source))
        if path.is_dir():
            found.extend(
                sorted(p for p in path.rglob("*") if p.is_file() and p.suffix.lower() in suffixes)
            )
        elif path.is_file():
            found.append(path)
    return found


def _digest(source) -> str:
    """Хеш содержимого снимка или сам путь, если файла нет. Точная копия приёма
    из faceplusplus.py — по той же причине: ключ кеша должен описывать картинку,
    а не имя, под которым её сохранили."""
    text = str(source or "")
    if not text:
        return ""
    try:
        return hashlib.sha1(Path(text).read_bytes()).hexdigest()
    except OSError:
        return text


# --- пополнение руками ---------------------------------------------------------


def main(argv=None) -> int:
    """`python -m johnny.connectors.face_index <путь> [<путь>…]`.

    Почему командой в терминале, а не голосом: пополнение — это разовая
    операция на тысячи файлов, она идёт минутами и выводит список пропущенных.
    Голосом такое не отдать, а главное — «Джони, проиндексируй мои фото»
    слишком легко сказать, не задумавшись, что именно ляжет на диск.
    """
    import sys

    sources = list(argv if argv is not None else sys.argv[1:])
    if not sources:
        print(
            "Укажите файлы или папки со снимками:\n"
            "  python -m johnny.connectors.face_index D:/фото/семья\n\n"
            "Индекс лежит в models/face-index и содержит биометрию — "
            "он не попадает ни в git, ни куда-либо наружу."
        )
        return 2

    report = add_to_index(sources)
    print(
        f"Добавлено лиц: {report['added_faces']} "
        f"со снимков: {report['added_photos']}. "
        f"Всего в архиве: {report['total_faces']}"
    )
    if report["no_face"]:
        print(f"Без лиц (пропущены): {report['no_face']}")
    for name, why in report["skipped"]:
        print(f"Не прочитался {name}: {why}")
    return 0


if __name__ == "__main__":  # pragma: no cover - точка входа
    raise SystemExit(main())
