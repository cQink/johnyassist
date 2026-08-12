"""Face++: согласие, журнал доступа, порог сравнения, секрет в теле запроса.

Сети нет: подменяется post_form. Проверяется не то, что Face++ узнаёт лица (это
его дело), а наши решения вокруг: без согласия снимок не уходит, ключ не
оказывается в строке запроса, вердикт считается по порогу из ответа, а в журнал
не попадает ни сам снимок, ни признаки лица.
"""

import json

import pytest

import johnny.connectors.base as base
import johnny.connectors.faceplusplus as fpp
from johnny import http_client

DETECT_RAW = {
    "faces": [
        {
            "face_token": "t1",
            "face_rectangle": {"top": 1, "left": 2, "width": 3, "height": 4},
            "landmark": {f"p{i}": {"x": i, "y": i} for i in range(83)},
            "attributes": {
                "age": {"value": 31},
                "emotion": {"happiness": 87.2, "neutral": 10.0, "sadness": 2.8},
                "facequality": {"value": 78.4, "threshold": 70.1},
                "blur": {"blurness": {"value": 3.2, "threshold": 50.0}},
                # Гендер сознательно не запрашиваем — но если сервис пришлёт его
                # сам, в выжимку он попасть не должен.
                "gender": {"value": "Female"},
            },
        },
        {"face_token": "t2", "attributes": {"age": {"value": 12}}},
    ]
}

COMPARE_RAW = {
    "confidence": 82.4,
    "thresholds": {"1e-3": 62.3, "1e-4": 69.1, "1e-5": 73.9},
}


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(http_client, "_last_failure", {})
    monkeypatch.setattr(base, "DEFAULT_CACHE_DIR", tmp_path)
    monkeypatch.setattr(base.time, "sleep", lambda s: None)


def _connector(tmp_path, consent=True, api_key="k", api_secret="s", **kwargs):
    connector = fpp.FacePlusPlusConnector(
        api_key=api_key,
        api_secret=api_secret,
        cache_dir=tmp_path,
        state_path=tmp_path / "state.json",
        log_path=tmp_path / "face-access.log",
        consent=consent,
        **kwargs,
    )
    # Секунда ожидания на каждый вызов удлиняла бы прогон без пользы: сама пауза
    # проверена в тестах base.
    connector.min_interval_seconds = 0
    return connector


def _calls(monkeypatch, payload=DETECT_RAW):
    seen = []

    def fake_post_form(url, headers, data, timeout, files=None):
        # Файлы читаем прямо здесь: снаружи они уже будут закрыты (with в _run).
        seen.append(
            {
                "url": url,
                "data": data,
                "files": {name: handle.read() for name, handle in (files or {}).items()},
            }
        )
        return FakeResponse(payload)

    monkeypatch.setattr(fpp, "post_form", fake_post_form)
    return seen


def _face(tmp_path, name="face.jpg", data=b"\xff\xd8\xff jpeg"):
    path = tmp_path / name
    path.write_bytes(data)
    return path


def test_without_consent_the_photo_is_not_sent(tmp_path, monkeypatch):
    """Face++ по интернету не ищет, но лицо всё равно уезжает третьей стороне.
    Биометрия остаётся биометрией."""
    seen = _calls(monkeypatch)
    result = _connector(tmp_path, consent=False).call(mode="detect", path=str(_face(tmp_path)))
    assert not result.ok and result.reason == "no-consent"
    assert seen == []
    assert not (tmp_path / "face-access.log").exists()


def test_half_of_the_credentials_is_not_ready(tmp_path):
    """Доступ — пара; забывают обычно секрет, и отказ обязан назвать оба."""
    ready, why = _connector(tmp_path, api_secret="").available()
    assert not ready and "секрет" in why.lower()


def test_credentials_go_in_the_body_not_the_query(tmp_path, monkeypatch):
    """Строка запроса оседает в логах любого прокси по пути — известный способ
    потерять секрет, не заметив."""
    seen = _calls(monkeypatch)
    _connector(tmp_path, api_key="КЛЮЧ", api_secret="СЕКРЕТ").call(
        mode="detect", path=str(_face(tmp_path))
    )
    assert "КЛЮЧ" not in seen[0]["url"] and "СЕКРЕТ" not in seen[0]["url"]
    assert seen[0]["data"]["api_key"] == "КЛЮЧ"
    assert seen[0]["data"]["api_secret"] == "СЕКРЕТ"


def test_gender_is_never_requested(tmp_path, monkeypatch):
    """Face++ отдаёт его как выбор из двух, ни одной команде он не нужен, а
    ошибка в нём — ошибка про живого человека."""
    seen = _calls(monkeypatch)
    _connector(tmp_path).call(mode="detect", path=str(_face(tmp_path)))
    assert "gender" not in seen[0]["data"]["return_attributes"]


def test_gender_does_not_survive_into_the_answer(tmp_path, monkeypatch):
    _calls(monkeypatch)
    result = _connector(tmp_path).call(mode="detect", path=str(_face(tmp_path)))
    assert "gender" not in json.dumps(result.data, ensure_ascii=False).lower()


def test_detect_counts_faces_and_keeps_the_strongest_emotion(tmp_path, monkeypatch):
    """Перечислять вслух проценты по семи эмоциям незачем."""
    _calls(monkeypatch)
    result = _connector(tmp_path).call(mode="detect", path=str(_face(tmp_path)))
    assert result.data["faces"] == 2
    assert result.data["details"][0]["emotion"] == "happiness"
    assert result.data["details"][0]["age"] == 31
    assert "2" in result.message


def test_detect_drops_landmarks(tmp_path, monkeypatch):
    """83 точки разметки на лицо — на порядки больше, чем нужно голосу, и всё
    это иначе оседает в кеше."""
    _calls(monkeypatch)
    result = _connector(tmp_path).call(mode="detect", path=str(_face(tmp_path)))
    assert "landmark" not in json.dumps(result.data)


def test_blurry_frame_is_flagged(tmp_path, monkeypatch):
    """По мутному кадру сравнение выдаёт уверенное «не тот человек» — сказать
    об этом надо раньше, чем человек сделает вывод."""
    blurry = {"faces": [{"attributes": {"blur": {"blurness": {"value": 91.0}}}}]}
    _calls(monkeypatch, payload=blurry)
    result = _connector(tmp_path).call(mode="detect", path=str(_face(tmp_path)))
    assert "смазан" in result.message


def test_no_faces_is_an_honest_answer(tmp_path, monkeypatch):
    _calls(monkeypatch, payload={"faces": []})
    result = _connector(tmp_path).call(mode="detect", path=str(_face(tmp_path)))
    assert result.ok and "не нашлось" in result.message


def test_broken_schema_does_not_crash(tmp_path, monkeypatch):
    _calls(monkeypatch, payload={"faces": "вовсе не список"})
    result = _connector(tmp_path).call(mode="detect", path=str(_face(tmp_path)))
    assert result.ok and result.data["faces"] == 0


def test_compare_sends_both_images_at_once(tmp_path, monkeypatch):
    """face_token живёт ограниченное время, а лишний вызов тратит квоту."""
    seen = _calls(monkeypatch, payload=COMPARE_RAW)
    one, two = _face(tmp_path, "1.jpg", b"one"), _face(tmp_path, "2.jpg", b"two")
    _connector(tmp_path).call(mode="compare", first=str(one), second=str(two))
    assert len(seen) == 1
    assert seen[0]["files"] == {"image_file1": b"one", "image_file2": b"two"}


def test_compare_verdict_uses_the_strict_threshold(tmp_path, monkeypatch):
    """«Сходство 82» само по себе не значит ничего: судим по порогу из ответа,
    и по самому строгому из трёх."""
    _calls(monkeypatch, payload=COMPARE_RAW)
    result = _connector(tmp_path).call(
        mode="compare", first=str(_face(tmp_path, "1.jpg")), second=str(_face(tmp_path, "2.jpg"))
    )
    assert result.data["threshold"] == 73.9
    assert result.data["same"] is True
    assert "один человек" in result.message


def test_confidence_below_threshold_is_different_people(tmp_path, monkeypatch):
    payload = {**COMPARE_RAW, "confidence": 70.0}
    _calls(monkeypatch, payload=payload)
    result = _connector(tmp_path).call(
        mode="compare", first=str(_face(tmp_path, "1.jpg")), second=str(_face(tmp_path, "2.jpg"))
    )
    assert result.data["same"] is False and "разные" in result.message


def test_threshold_is_always_spoken(tmp_path, monkeypatch):
    """Назвать сходство без порога — предложить сделать вывод по числу, которое
    не с чем сравнить."""
    _calls(monkeypatch, payload=COMPARE_RAW)
    result = _connector(tmp_path).call(
        mode="compare", first=str(_face(tmp_path, "1.jpg")), second=str(_face(tmp_path, "2.jpg"))
    )
    assert "73.9" in result.message and "82.4" in result.message


def test_access_is_logged_without_the_face(tmp_path, monkeypatch):
    """Журнал отвечает на вопрос «чьи лица и когда мы отправляли». Ни снимка,
    ни признаков лица в нём быть не должно."""
    _calls(monkeypatch)
    _connector(tmp_path).call(mode="detect", path=str(_face(tmp_path, "ира.jpg")))
    record = json.loads((tmp_path / "face-access.log").read_text(encoding="utf-8").strip())
    assert record["mode"] == "detect" and record["files"] == ["ира.jpg"]
    assert record["faces"] == 2
    # Проверяем набор полей целиком, а не поиском подстрок: «31» встречается
    # внутри хеша файла и такая проверка врала бы в обе стороны.
    assert set(record) == {"at", "mode", "files", "hashes", "faces"}


def test_every_call_appends_to_the_log(tmp_path, monkeypatch):
    _calls(monkeypatch)
    c = _connector(tmp_path)
    c.call(mode="detect", path=str(_face(tmp_path, "1.jpg", b"aaa")))
    c.call(mode="detect", path=str(_face(tmp_path, "2.jpg", b"bbb")))
    lines = (tmp_path / "face-access.log").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2


def test_broken_log_does_not_block_the_answer(tmp_path, monkeypatch):
    """Журнал не должен стоить человеку ответа."""
    _calls(monkeypatch)
    c = _connector(tmp_path)
    # Каталог вместо файла: открыть его на запись нельзя.
    (tmp_path / "занято").mkdir()
    c._log_path = tmp_path / "занято"
    assert c.call(mode="detect", path=str(_face(tmp_path))).ok


def test_cache_key_is_the_file_content_not_the_path(tmp_path, monkeypatch):
    """Ключ по пути отдал бы вердикт про вчерашнего человека — здесь это не
    неточность, а ответ про другое лицо."""
    seen = _calls(monkeypatch)
    path = _face(tmp_path, "shot.jpg", b"first person")
    c = _connector(tmp_path)
    c.call(mode="detect", path=str(path))
    path.write_bytes(b"someone else entirely")
    c.call(mode="detect", path=str(path))
    assert len(seen) == 2


def test_repeat_call_is_cached(tmp_path, monkeypatch):
    seen = _calls(monkeypatch)
    path = _face(tmp_path)
    c = _connector(tmp_path)
    c.call(mode="detect", path=str(path))
    second = c.call(mode="detect", path=str(path))
    assert second.cached and len(seen) == 1


def test_detect_and_compare_are_different_cache_entries(tmp_path, monkeypatch):
    seen = _calls(monkeypatch)
    one, two = _face(tmp_path, "1.jpg", b"one"), _face(tmp_path, "2.jpg", b"two")
    c = _connector(tmp_path)
    c.call(mode="detect", path=str(one))
    c.call(mode="compare", first=str(one), second=str(two))
    assert len(seen) == 2


def test_missing_file_is_a_refusal_not_a_crash(tmp_path, monkeypatch):
    seen = _calls(monkeypatch)
    result = _connector(tmp_path).call(mode="detect", path=str(tmp_path / "нет.jpg"))
    assert not result.ok and result.reason == "error" and seen == []


def test_empty_path_says_so_instead_of_opening_a_directory(tmp_path, monkeypatch):
    """Path("") — это Path("."), то есть КАТАЛОГ: существует, размер есть, все
    проверки проходит, а open() падает «Permission denied: '.'». Человек слышал
    бы errno про каталог вместо «снимок не назван». Наступили на это живьём."""
    seen = _calls(monkeypatch)
    result = _connector(tmp_path).call(mode="detect", path="")
    assert not result.ok and seen == []
    assert "не указан снимок" in result.data["error"]


def test_directory_instead_of_a_photo_is_refused(tmp_path, monkeypatch):
    """Та же ошибка с другой стороны: каталог существует, но снимком не является,
    и exists() этого не различает."""
    seen = _calls(monkeypatch)
    result = _connector(tmp_path).call(mode="detect", path=str(tmp_path))
    assert not result.ok and seen == []


def test_oversized_photo_is_refused_before_the_network(tmp_path, monkeypatch):
    """Свой отказ понятнее чужого INVALID_IMAGE_SIZE и не тратит квоту."""
    seen = _calls(monkeypatch)
    big = _face(tmp_path, "big.jpg", b"x" * (fpp.MAX_IMAGE_BYTES + 1))
    result = _connector(tmp_path).call(mode="detect", path=str(big))
    assert not result.ok and seen == []
    assert "МБ" in result.data["error"]


def test_compare_with_one_photo_is_refused(tmp_path, monkeypatch):
    seen = _calls(monkeypatch)
    result = _connector(tmp_path).call(mode="compare", first=str(_face(tmp_path)), second="")
    assert not result.ok and seen == []


def test_service_error_is_kept_verbatim(tmp_path, monkeypatch):
    """INVALID_IMAGE_SIZE и CONCURRENCY_LIMIT_EXCEEDED требуют разных действий
    от человека — различать их надо."""
    _calls(monkeypatch, payload={"error_message": "CONCURRENCY_LIMIT_EXCEEDED"})
    result = _connector(tmp_path).call(mode="detect", path=str(_face(tmp_path)))
    assert not result.ok and "CONCURRENCY_LIMIT_EXCEEDED" in result.data["error"]


def test_secret_never_leaks_into_the_error(tmp_path, monkeypatch):
    _calls(monkeypatch, payload={"error_message": "AUTHENTICATION_ERROR"})
    c = _connector(tmp_path, api_key="КЛЮЧ777", api_secret="СЕКРЕТ777")
    result = c.call(mode="detect", path=str(_face(tmp_path)))
    written = result.message + json.dumps(result.data, ensure_ascii=False)
    assert "КЛЮЧ777" not in written and "СЕКРЕТ777" not in written


def test_quota_survives_and_then_refuses(tmp_path, monkeypatch):
    _calls(monkeypatch)
    c = _connector(tmp_path, daily_quota=1)
    c.call(mode="detect", path=str(_face(tmp_path, "1.jpg", b"aaa")))
    second = c.call(mode="detect", path=str(_face(tmp_path, "2.jpg", b"bbb")))
    assert not second.ok and second.reason == "quota"
