"""Локальный индекс лиц: границы обещания, отказы и приватность.

Настоящая модель здесь не поднимается ни разу, и это не экономия на тестах, а
необходимость: импорт onnxruntime на этой машине стоит 24–25 секунд (замер в
шапке johnny/connectors/face_index.py), то есть один честный прогон удлинил бы
набор больше, чем все остальные тесты вместе. Распознавание — дело insightface,
и проверять надо не его, а наши решения вокруг: что «нет лица» звучит иначе,
чем «нет в архиве», что обрезанный аватар получает второй шанс, что пополнение
архива сбрасывает кеш, и что порченый индекс отказывает, а не выдаёт чужое имя.

Снимки создаются настоящими файлами (cv2 весит 0.1 с на импорт), потому что
чтение с диска — тоже наше решение: путь с кириллицей проверяется вживую.
"""

from __future__ import annotations

import json
import sys

import cv2
import numpy as np
import pytest

import johnny.connectors.base as base
import johnny.connectors.face_index as fi


class FakeFace:
    """Лицо от insightface — нам от него нужен только нормированный вектор."""

    def __init__(self, vector):
        self.normed_embedding = np.asarray(vector, dtype="float32")


class FakeApp:
    """Подмена FaceAnalysis. Отдаёт лица по размеру кадра.

    Размер, а не порядок вызова: так проверяется второй заход с полями —
    отличить его от первого можно ровно по тому, что кадр стал больше.
    """

    def __init__(self, small=(), big=None):
        self.small = list(small)
        self.big = list(small if big is None else big)
        self.seen = []

    def get(self, image):
        self.seen.append(image.shape[:2])
        first = self.seen[0]
        return self.big if image.shape[:2] != first else self.small


# Векторы четырёхмерные, а не 512: размерность код нигде не проверяет, а
# читаемость нужна — из [1, 0, 0, 0] и [c, √(1-c²), 0, 0] косинус виден глазом.
def _pair(cosine: float):
    """Запрос и вектор в архиве с заданным косинусом между ними."""
    query = np.array([1.0, 0.0, 0.0, 0.0], dtype="float32")
    other = np.array([cosine, float(np.sqrt(1 - cosine**2)), 0.0, 0.0], dtype="float32")
    return query, other


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(base, "DEFAULT_CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(base.time, "sleep", lambda s: None)
    # Модель на весь модуль кешируется — без сброса первый же тест раздал бы
    # свою подмену всем остальным.
    monkeypatch.setattr(fi, "_apps", {})


def _photo(tmp_path, name="снимок.png", size=(200, 200)):
    """Настоящий файл картинки. Имя по умолчанию кириллическое — на нём и
    ломался бы cv2.imread, ради чего в коде стоит fromfile+imdecode."""
    image = np.full((size[0], size[1], 3), 128, dtype=np.uint8)
    path = tmp_path / name
    cv2.imencode(".png", image)[1].tofile(str(path))
    return path


def _archive(tmp_path, vectors, paths):
    index = tmp_path / "index"
    index.mkdir(parents=True, exist_ok=True)
    np.save(index / fi.VECTORS_NAME, np.asarray(vectors, dtype="float32"))
    (index / fi.PATHS_NAME).write_text(
        json.dumps({"paths": list(paths)}, ensure_ascii=False), encoding="utf-8"
    )
    return index


def _connector(tmp_path, index_dir=None, **kwargs):
    return fi.FaceIndexConnector(
        index_dir=index_dir if index_dir is not None else tmp_path / "index",
        cache_dir=tmp_path / "cache",
        state_path=tmp_path / "cache" / "state.json",
        **kwargs,
    )


# --- готовность: два разных отказа ---


def test_empty_archive_names_the_way_to_fill_it(tmp_path):
    """Пустой архив — не «поиск сломался», а «положите туда снимки»."""
    ready, why = _connector(tmp_path).available()
    assert ready is False
    assert "пуст" in why
    # Человеку нужен не диагноз, а следующий шаг.
    assert "face_index" in why


def test_missing_package_sounds_different_from_empty_archive(tmp_path, monkeypatch):
    """Установить пакет и пополнить архив — разные починки, и путать их нельзя."""
    monkeypatch.setattr(fi, "_package_present", lambda: False)
    _, no_package = _connector(tmp_path).available()

    monkeypatch.setattr(fi, "_package_present", lambda: True)
    _, no_archive = _connector(tmp_path).available()

    assert "insightface" in no_package
    assert "insightface" not in no_archive
    assert no_package != no_archive


def test_filled_archive_is_ready(tmp_path, monkeypatch):
    monkeypatch.setattr(fi, "_package_present", lambda: True)
    index = _archive(tmp_path, [[1, 0, 0, 0]], ["a.jpg"])
    assert _connector(tmp_path, index).available() == (True, "")


def test_readiness_does_not_import_the_package(tmp_path):
    """available() живёт в голосовом пути и не смеет тянуть onnxruntime.

    Тот инициализируется 24–25 секунд, то есть импорт здесь означал бы, что
    Джони молчит полминуты даже перед ответом «архив пуст». Проверяем по
    факту: список загруженных модулей после вызова не изменился.
    """
    before = "insightface" in sys.modules
    _connector(tmp_path).available()
    assert ("insightface" in sys.modules) is before


def test_nothing_leaves_the_machine_so_no_consent_is_asked(tmp_path, monkeypatch):
    """Согласие значит «разрешаю отправить третьей стороне». Отправки нет.

    Спрашивать его тут — приучать человека жать «да» не глядя, и тогда
    настоящий вопрос (Face++) он тоже пропустит.
    """
    assert fi.FaceIndexConnector.requires_consent is False
    index = _archive(tmp_path, [[1, 0, 0, 0]], ["a.jpg"])
    connector = _connector(tmp_path, index)  # consent не передаём вовсе
    monkeypatch.setattr(fi, "_package_present", lambda: True)
    monkeypatch.setattr(fi, "_app", lambda root: FakeApp([FakeFace([1, 0, 0, 0])]))
    assert connector.call(path=str(_photo(tmp_path))).ok is True


# --- что мы говорим про сам снимок ---


def _ready(tmp_path, monkeypatch, faces, vectors, paths):
    monkeypatch.setattr(fi, "_package_present", lambda: True)
    monkeypatch.setattr(fi, "_app", lambda root: FakeApp(faces))
    index = _archive(tmp_path, vectors, paths)
    return _connector(tmp_path, index)


def test_no_face_is_an_answer_about_the_frame_not_a_breakdown(tmp_path, monkeypatch):
    """Кадр без лица — не сбой сервиса.

    Будь это исключением, base перевёл бы его в глухое «face-index не ответил»
    и вдобавок поставил cooldown — то есть заглушил бы следующий, нормальный
    снимок.
    """
    connector = _ready(tmp_path, monkeypatch, [], [[1, 0, 0, 0]], ["a.jpg"])
    result = connector.call(path=str(_photo(tmp_path)))
    assert result.ok is True
    assert result.data["faces"] == 0
    assert "нет лица" in result.message
    assert "архив" not in result.message.lower()


def test_several_faces_ask_which_one(tmp_path, monkeypatch):
    """Молча взять самое крупное значило бы искать не того человека."""
    faces = [FakeFace([1, 0, 0, 0]), FakeFace([0, 1, 0, 0])]
    connector = _ready(tmp_path, monkeypatch, faces, [[1, 0, 0, 0]], ["a.jpg"])
    result = connector.call(path=str(_photo(tmp_path)))
    assert result.ok is True
    assert result.data["faces"] == 2
    assert "2 лица" in result.message
    assert result.data["matches"] == []


# --- что мы говорим про архив ---


def test_confident_match_is_named(tmp_path, monkeypatch):
    query, stored = _pair(0.9)
    connector = _ready(tmp_path, monkeypatch, [FakeFace(query)], [stored], ["D:/фото/ира.jpg"])
    result = connector.call(path=str(_photo(tmp_path)))
    assert result.data["matches"][0]["same"] is True
    assert "ира.jpg" in result.message
    # Путь целиком не произносим: голосом «D двоеточие слэш фото слэш» — шум.
    assert "D:/фото" not in result.message


def test_doubtful_match_is_not_called_a_find(tmp_path, monkeypatch):
    """Ниже порога — повод посмотреть, а не вывод. Разница слышимая."""
    query, stored = _pair(0.33)  # между CANDIDATE_THRESHOLD и MATCH_THRESHOLD
    connector = _ready(tmp_path, monkeypatch, [FakeFace(query)], [stored], ["ира.jpg"])
    result = connector.call(path=str(_photo(tmp_path)))
    assert result.data["matches"][0]["same"] is False
    assert "Уверенного совпадения нет" in result.message


def test_noise_is_not_offered_as_a_candidate(tmp_path, monkeypatch):
    """У разных людей косинус 0.026 — называть такое кандидатом значит врать
    числом. Ответ должен быть «нет», а не «вот, но не очень»."""
    query, stored = _pair(0.05)
    connector = _ready(tmp_path, monkeypatch, [FakeFace(query)], [stored], ["чужой.jpg"])
    result = connector.call(path=str(_photo(tmp_path)))
    assert result.data["matches"] == []
    assert "В архиве такого человека нет" in result.message
    # Размер архива называем всегда: «не нашёл» по одному снимку и по тысяче —
    # утверждения разной силы.
    assert "1" in result.message


def test_a_group_photo_answers_once(tmp_path, monkeypatch):
    """Вопрос звучит «на каких СНИМКАХ», поэтому шесть лиц с одного фото — это
    одна строка ответа, иначе групповое фото вытеснит из пятёрки всё остальное."""
    query, close = _pair(0.9)
    _, closer = _pair(0.95)
    connector = _ready(
        tmp_path,
        monkeypatch,
        [FakeFace(query)],
        [close, closer, close],
        ["группа.jpg", "группа.jpg", "группа.jpg"],
    )
    result = connector.call(path=str(_photo(tmp_path)))
    assert len(result.data["matches"]) == 1
    # Из трёх лиц одного снимка остаётся ЛУЧШЕЕ.
    assert result.data["matches"][0]["score"] == pytest.approx(0.95, abs=0.01)
    assert result.data["searched"] == 3


def test_top_is_limited(tmp_path, monkeypatch):
    query, stored = _pair(0.9)
    connector = _ready(
        tmp_path, monkeypatch, [FakeFace(query)], [stored] * 8, [f"{i}.jpg" for i in range(8)]
    )
    assert len(connector.call(path=str(_photo(tmp_path))).data["matches"]) == fi.TOP_N


# --- порченый архив ---


def test_broken_archive_refuses_instead_of_shifting_names(tmp_path, monkeypatch):
    """Рассинхрон векторов и путей даёт ЧУЖОЕ имя с высоким сходством — то есть
    уверенно неверный ответ. Он хуже отказа, и поэтому отказываем."""
    monkeypatch.setattr(fi, "_package_present", lambda: True)
    monkeypatch.setattr(fi, "_app", lambda root: FakeApp([FakeFace([1, 0, 0, 0])]))
    index = _archive(tmp_path, [[1, 0, 0, 0], [0, 1, 0, 0]], ["только-один.jpg"])
    result = _connector(tmp_path, index).call(path=str(_photo(tmp_path)))
    assert result.ok is False
    assert result.reason == "error"
    assert "пересоберите" in result.data["error"]


def test_unreadable_index_counts_as_empty(tmp_path):
    """Битый json — состояние «архива нет», а не падение при старте панели."""
    index = tmp_path / "index"
    index.mkdir()
    (index / fi.PATHS_NAME).write_text("{ это не json", encoding="utf-8")
    assert _connector(tmp_path, index).index_size() == 0


# --- кеш ---


def test_filling_the_archive_invalidates_the_cache(tmp_path, monkeypatch):
    """Иначе новый человек в архиве не нашёлся бы никогда: ответ на тот же
    снимок отдавался бы из вчерашнего кеша."""
    monkeypatch.setattr(fi, "_package_present", lambda: True)
    photo = _photo(tmp_path)
    index = _archive(tmp_path, [[1, 0, 0, 0]], ["a.jpg"])
    connector = _connector(tmp_path, index)
    before = connector.cache_params({"path": str(photo)})

    _archive(tmp_path, [[1, 0, 0, 0], [0, 1, 0, 0]], ["a.jpg", "b.jpg"])
    assert connector.cache_params({"path": str(photo)}) != before


def test_cache_key_follows_the_picture_not_the_path(tmp_path):
    """screenshot.png перезаписывается каждым новым снимком — ключ по пути
    отдавал бы вчерашний ответ на сегодняшнюю картинку."""
    connector = _connector(tmp_path)
    same = connector.cache_params({"path": str(_photo(tmp_path, "один.png"))})
    copy = connector.cache_params({"path": str(_photo(tmp_path, "другой.png"))})
    assert same["image"] == copy["image"]
    other = connector.cache_params({"path": str(_photo(tmp_path, "третий.png", (40, 40)))})
    assert other["image"] != same["image"]


# --- чтение снимка ---


def test_cyrillic_path_is_read(tmp_path, monkeypatch):
    """cv2.imread на Windows молча вернул бы None, и «лица нет» прозвучало бы
    вместо «файл не прочитался». Архив семейных фото — ровно такие пути."""
    photo = _photo(tmp_path, "Ира и Дима, 2019.png")
    image = fi._read_image(str(photo))
    assert image.shape[:2] == (200, 200)


@pytest.mark.parametrize(
    "make, expected",
    [
        (lambda p: "", "не указан снимок"),
        (lambda p: str(p / "нет-такого.jpg"), "файла нет"),
    ],
)
def test_bad_input_says_what_exactly(tmp_path, make, expected):
    with pytest.raises(RuntimeError, match=expected):
        fi._read_image(make(tmp_path))


def test_not_a_picture_is_named_as_such(tmp_path):
    fake = tmp_path / "документ.png"
    fake.write_bytes(b"\x00\x01\x02\x03")
    with pytest.raises(RuntimeError, match="не похоже на картинку"):
        fi._read_image(str(fake))


# --- обрезанный аватар ---


def test_a_tight_crop_gets_a_second_try_with_borders(tmp_path, monkeypatch):
    """Проверено на сэмпле insightface 112×112: как есть — 0 лиц, с полями — 1.

    Аватарка из мессенджера обрезана ровно так, и без второго захода Джони
    отвечал бы «на снимке нет лица» на снимок, где лицо занимает весь кадр.
    """
    app = FakeApp(small=[], big=[FakeFace([1, 0, 0, 0])])
    monkeypatch.setattr(fi, "_app", lambda root: app)
    faces = fi.detect_faces(str(_photo(tmp_path, "аватар.png", (112, 112))))
    assert len(faces) == 1
    assert len(app.seen) == 2
    # Второй кадр именно БОЛЬШЕ — это и есть поля.
    assert app.seen[1] > app.seen[0]


def test_a_found_face_is_not_looked_for_twice(tmp_path, monkeypatch):
    app = FakeApp(small=[FakeFace([1, 0, 0, 0])])
    monkeypatch.setattr(fi, "_app", lambda root: app)
    fi.detect_faces(str(_photo(tmp_path, "портрет.png", (112, 112))))
    assert len(app.seen) == 1


def test_a_big_photo_without_faces_is_not_retried(tmp_path, monkeypatch):
    """Пейзаж лицом в край не упирается. Второй заход там удвоил бы время
    обхода архива на каждом снимке без людей."""
    app = FakeApp(small=[])
    monkeypatch.setattr(fi, "_app", lambda root: app)
    big = fi.CROP_LIMIT + 100
    fi.detect_faces(str(_photo(tmp_path, "пейзаж.png", (big, big))))
    assert len(app.seen) == 1


# --- пополнение ---


def test_the_disk_is_not_walked_by_itself(tmp_path, monkeypatch):
    """Индекс — биометрия, и «Джони обошёл все ваши папки» не та функция,
    которую включают молча. Берём только названное."""
    named = tmp_path / "названная"
    named.mkdir()
    _photo(named, "внутри.png")
    _photo(tmp_path, "рядом.png")  # сосед, которого не называли

    found = [p.name for p in fi._collect([named])]
    assert found == ["внутри.png"]


def test_named_folder_is_walked_deep(tmp_path):
    """Архив снимков почти всегда разложен по годам и событиям."""
    deep = tmp_path / "архив" / "2019" / "лето"
    deep.mkdir(parents=True)
    _photo(deep, "море.png")
    assert [p.name for p in fi._collect([tmp_path / "архив"])] == ["море.png"]


def test_non_pictures_are_ignored(tmp_path):
    (tmp_path / "заметка.txt").write_text("не картинка", encoding="utf-8")
    _photo(tmp_path, "снимок.png")
    assert [p.name for p in fi._collect([tmp_path])] == ["снимок.png"]


def test_all_faces_of_a_photo_go_in(tmp_path, monkeypatch):
    """На групповом фото человек может быть и не в центре, а вопрос звучит
    «есть ли он в архиве»."""
    faces = [FakeFace([1, 0, 0, 0]), FakeFace([0, 1, 0, 0]), FakeFace([0, 0, 1, 0])]
    monkeypatch.setattr(fi, "_app", lambda root: FakeApp(faces))
    _photo(tmp_path, "группа.png")
    report = fi.add_to_index([tmp_path], index_dir=tmp_path / "index")
    assert report["added_faces"] == 3
    assert report["added_photos"] == 1


def test_a_photo_is_not_indexed_twice(tmp_path, monkeypatch):
    monkeypatch.setattr(fi, "_app", lambda root: FakeApp([FakeFace([1, 0, 0, 0])]))
    _photo(tmp_path, "снимок.png")
    index = tmp_path / "index"
    fi.add_to_index([tmp_path], index_dir=index)
    second = fi.add_to_index([tmp_path], index_dir=index)
    assert second["added_faces"] == 0
    assert second["total_faces"] == 1


def test_indexing_appends_instead_of_replacing(tmp_path, monkeypatch):
    monkeypatch.setattr(fi, "_app", lambda root: FakeApp([FakeFace([1, 0, 0, 0])]))
    index = tmp_path / "index"
    _photo(tmp_path, "первый.png")
    fi.add_to_index([tmp_path / "первый.png"], index_dir=index)
    _photo(tmp_path, "второй.png")
    report = fi.add_to_index([tmp_path / "второй.png"], index_dir=index)
    assert report["total_faces"] == 2
    assert len(np.load(index / fi.VECTORS_NAME)) == 2


def test_one_broken_file_does_not_stop_a_thousand(tmp_path, monkeypatch):
    """В архиве на тысячу снимков десяток нечитаемых — норма. Падать на них
    значит не собрать индекс никогда."""
    monkeypatch.setattr(fi, "_app", lambda root: FakeApp([FakeFace([1, 0, 0, 0])]))
    (tmp_path / "битый.png").write_bytes(b"\x00\x01")
    _photo(tmp_path, "хороший.png")
    report = fi.add_to_index([tmp_path], index_dir=tmp_path / "index")
    assert report["added_faces"] == 1
    assert [name for name, _ in report["skipped"]] == ["битый.png"]


def test_a_photo_without_faces_is_counted_not_hidden(tmp_path, monkeypatch):
    monkeypatch.setattr(fi, "_app", lambda root: FakeApp([]))
    _photo(tmp_path, "пейзаж.png")
    report = fi.add_to_index([tmp_path], index_dir=tmp_path / "index")
    assert report["no_face"] == 1
    assert report["added_faces"] == 0
    # Пустой индекс на диск не пишем — иначе available() соврал бы «готов».
    assert not (tmp_path / "index" / fi.VECTORS_NAME).exists()


# --- команда пополнения ---


def test_command_without_arguments_explains_instead_of_guessing(tmp_path, capsys):
    """Без пути пополнять нечего, и угадывать нельзя — см. выше про обход диска."""
    assert fi.main([]) == 2
    printed = capsys.readouterr().out
    assert "биометри" in printed
    assert "face_index" in printed


def test_command_reports_what_it_did(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(fi, "_app", lambda root: FakeApp([FakeFace([1, 0, 0, 0])]))
    monkeypatch.setattr(fi, "DEFAULT_INDEX_DIR", tmp_path / "index")
    _photo(tmp_path, "снимок.png")
    assert fi.main([str(tmp_path)]) == 0
    printed = capsys.readouterr().out
    assert "Добавлено лиц: 1" in printed


# --- прогрев ---


def test_prewarm_never_throws(monkeypatch):
    """Его крутит фоновый поток: исключение оттуда никто не поймает, а видимого
    вреда от неудачи нет — коннектор скажет про неё сам, когда его позовут."""

    def boom(root):
        raise RuntimeError("нет модели")

    monkeypatch.setattr(fi, "_app", boom)
    assert fi.prewarm() is False


def test_prewarm_reports_success(monkeypatch):
    monkeypatch.setattr(fi, "_app", lambda root: FakeApp([]))
    assert fi.prewarm() is True
