"""Azure AI Vision: согласие, ключ кеша по содержимому, выжимка ответа.

Сети здесь нет: подменяются post и post_bytes. Проверяется не то, что Azure
отвечает (это его дело), а что мы правильно спрашиваем и правильно молчим —
без согласия и без адреса ресурса запроса быть не должно вовсе.
"""

import pytest

import johnny.connectors.azure_vision as av
import johnny.connectors.base as base
from johnny import http_client

ENDPOINT = "https://ресурс.cognitiveservices.azure.com"

SCENE = {
    "captionResult": {"text": "a cat sitting on a laptop", "confidence": 0.83},
    "tagsResult": {
        "values": [
            {"name": "cat", "confidence": 0.99},
            {"name": "laptop", "confidence": 0.91},
            # Ниже порога: Azure отдаёт такие десятками, вслух это бред.
            {"name": "abstract", "confidence": 0.31},
        ]
    },
    "objectsResult": {"values": [{"tags": [{"name": "cat", "confidence": 0.7}]}]},
    "peopleResult": {"values": []},
}

TEXT = {
    "readResult": {
        "blocks": [
            {
                "lines": [
                    {"text": "Итого к оплате", "words": [{"text": "Итого", "polygon": [1, 2]}]},
                    {"text": "1 240 рублей"},
                ]
            }
        ]
    }
}


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        if self._payload is None:
            raise ValueError("не JSON")
        return self._payload


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(http_client, "_last_failure", {})
    monkeypatch.setattr(base, "DEFAULT_CACHE_DIR", tmp_path)
    monkeypatch.setattr(base.time, "sleep", lambda s: None)
    # Память про недоступные в регионе фичи живёт на уровне модуля (коннектор
    # пересобирается на каждый вызов). Без сброса первый же тест про регион
    # менял бы поведение всех следующих, и падал бы не он.
    av.forget_region_gaps()


def _connector(tmp_path, consent=True, endpoint=ENDPOINT, api_key="k", **kwargs):
    connector = av.AzureVisionConnector(
        api_key=api_key,
        endpoint=endpoint,
        cache_dir=tmp_path,
        state_path=tmp_path / "state.json",
        consent=consent,
        **kwargs,
    )
    # Пауза между вызовами тут только удлиняет прогон: сама она проверена в
    # тестах base, а здесь мешала бы каждому второму тесту.
    connector.min_interval_seconds = 0
    return connector


def _calls(monkeypatch, payload=SCENE, status=200):
    """Ловушка на оба пути отправки: ссылка идёт JSON-ом, файл — сырым телом."""
    seen = []

    def fake_post(url, headers, data, timeout):
        seen.append({"url": url, "headers": headers, "json": data})
        return FakeResponse(payload, status)

    def fake_post_bytes(url, headers, body, timeout, content_type):
        seen.append(
            {"url": url, "headers": headers, "bytes": body, "content_type": content_type}
        )
        return FakeResponse(payload, status)

    monkeypatch.setattr(av, "post", fake_post)
    monkeypatch.setattr(av, "post_bytes", fake_post_bytes)
    return seen


REGION_ERROR = {
    "error": {
        "code": "InvalidRequest",
        "message": "The feature 'Caption' is not supported in this region.",
    }
}


def _sequence(monkeypatch, answers):
    """Ловушка, отвечающая по списку: (payload, status) на каждый вызов подряд.

    Нужна там, где важен ИМЕННО второй запрос: регион отвергает caption, и
    проверяется, что повтор ушёл без него и с тем же файлом.
    """
    seen = []
    queue = list(answers)

    def reply(url, headers, **rest):
        payload, status = queue.pop(0) if queue else (SCENE, 200)
        seen.append({"url": url, "headers": headers, **rest})
        return FakeResponse(payload, status)

    monkeypatch.setattr(av, "post", lambda url, h, data, timeout: reply(url, h, json=data))
    monkeypatch.setattr(
        av,
        "post_bytes",
        lambda url, h, body, timeout, content_type: reply(url, h, bytes=body),
    )
    return seen


def _image(tmp_path, name="pic.png", data=b"\x89PNG\r\n---"):
    path = tmp_path / name
    path.write_bytes(data)
    return path


def test_without_consent_nothing_is_sent(tmp_path, monkeypatch):
    """Сюда уходит файл с ЭТОГО компьютера — скриншот с перепиской, документ.
    Молча отправлять его третьей стороне нельзя."""
    seen = _calls(monkeypatch)
    result = _connector(tmp_path, consent=False).call(source=str(_image(tmp_path)))
    assert not result.ok and result.reason == "no-consent"
    assert seen == []


def test_missing_key_refuses_by_key(tmp_path):
    ready, why = _connector(tmp_path, api_key="").available()
    assert not ready and "ключ" in why.lower()


def test_missing_endpoint_refuses_by_endpoint(tmp_path):
    """У человека с ключом, но без адреса, отказ обязан назвать адрес — иначе
    он пойдёт искать второй ключ, которого не существует."""
    ready, why = _connector(tmp_path, endpoint="").available()
    assert not ready and "endpoint" in why.lower()


def test_url_goes_as_json(tmp_path, monkeypatch):
    seen = _calls(monkeypatch)
    _connector(tmp_path).call(source="https://site/pic.jpg")
    assert seen[0]["json"] == {"url": "https://site/pic.jpg"}
    assert seen[0]["headers"]["Ocp-Apim-Subscription-Key"] == "k"


def test_local_file_goes_as_raw_bytes(tmp_path, monkeypatch):
    """multipart Azure отвергает: локальную картинку он принимает только телом
    с application/octet-stream."""
    seen = _calls(monkeypatch)
    path = _image(tmp_path)
    _connector(tmp_path).call(source=str(path))
    assert seen[0]["bytes"] == path.read_bytes()
    assert seen[0]["content_type"] == "application/octet-stream"


def test_features_and_version_are_in_the_url(tmp_path, monkeypatch):
    seen = _calls(monkeypatch)
    _connector(tmp_path).call(source="https://site/pic.jpg", features=av.READ_FEATURES)
    assert f"api-version={av.API_VERSION}" in seen[0]["url"]
    assert f"features={av.READ_FEATURES}" in seen[0]["url"]


def test_scene_answer_is_compacted(tmp_path, monkeypatch):
    _calls(monkeypatch)
    data = _connector(tmp_path).call(source="https://site/pic.jpg").data
    assert data["caption"] == "a cat sitting on a laptop"
    # Тег ниже порога отброшен: он бы прозвучал как факт о картинке.
    assert data["tags"] == ["cat", "laptop"]


def test_scene_answer_warns_about_english(tmp_path, monkeypatch):
    """Русского описания у Azure нет вовсе. Английская фраза посреди русского
    ответа без предупреждения выглядит как сбой."""
    _calls(monkeypatch)
    message = _connector(tmp_path).call(source="https://site/pic.jpg").message
    assert "по-английски" in message and "a cat" in message


def test_ocr_keeps_lines_and_drops_polygons(tmp_path, monkeypatch):
    _calls(monkeypatch, payload=TEXT)
    result = _connector(tmp_path).call(source="https://site/чек.png", features=av.READ_FEATURES)
    assert result.data["lines"] == ["Итого к оплате", "1 240 рублей"]
    assert "words" not in result.data and "1 240 рублей" in result.message


def test_truncated_text_says_so(tmp_path, monkeypatch):
    """Обрезанный текст иначе звучит как весь текст, и вывод делается по
    половине документа."""
    many = {"readResult": {"blocks": [{"lines": [{"text": f"строка {i}"} for i in range(60)]}]}}
    _calls(monkeypatch, payload=many)
    result = _connector(tmp_path).call(source="https://site/док.png", features=av.READ_FEATURES)
    assert result.data["truncated"] is True and "только начало" in result.message


def test_broken_schema_does_not_crash(tmp_path, monkeypatch):
    """У Azure одновременно живут несколько версий API; падать посреди
    разговора из-за несовпадения схемы — худший исход."""
    _calls(monkeypatch, payload={"tagsResult": "вовсе не словарь"})
    result = _connector(tmp_path).call(source="https://site/pic.jpg")
    assert result.ok and result.data["tags"] == []


def test_http_error_explains_itself(tmp_path, monkeypatch):
    """Самая частая ошибка — «feature Caption is not supported in this region»,
    и по одному коду её не опознать. Вслух это не произносится (base отвечает
    «не ответил»), но в data и в логе причина обязана быть целиком."""
    payload = {"error": {"code": "InvalidRequest", "message": "Caption is not supported"}}
    _calls(monkeypatch, payload=payload, status=400)
    result = _connector(tmp_path).call(source="https://site/pic.jpg")
    assert not result.ok
    assert "Caption is not supported" in result.data["error"]
    assert "400" in result.data["error"]


def test_key_never_appears_in_the_error(tmp_path, monkeypatch):
    """Ключ уходит заголовком и в ответе не повторяется — но если сервис вдруг
    вернёт его эхом, наружу он попасть не должен."""
    _calls(monkeypatch, payload={"error": {"code": "x", "message": "ключ k плохой"}}, status=401)
    c = _connector(tmp_path, api_key="СЕКРЕТ123")
    result = c.call(source="https://site/pic.jpg")
    assert "СЕКРЕТ123" not in result.message
    assert "СЕКРЕТ123" not in result.data["error"]


def test_cache_key_is_the_file_content_not_the_path(tmp_path, monkeypatch):
    """screenshot.png перезаписывается каждым новым снимком: ключ по пути отдал
    бы вчерашний ответ на сегодняшнюю картинку."""
    seen = _calls(monkeypatch)
    path = _image(tmp_path, "screenshot.png", b"first shot")
    c = _connector(tmp_path)
    c.call(source=str(path))
    path.write_bytes(b"another shot entirely")
    c.call(source=str(path))
    assert len(seen) == 2


def test_same_file_is_answered_from_cache(tmp_path, monkeypatch):
    seen = _calls(monkeypatch)
    path = _image(tmp_path)
    c = _connector(tmp_path)
    c.call(source=str(path))
    second = c.call(source=str(path))
    assert second.cached and len(seen) == 1


def test_ocr_and_description_are_different_cache_entries(tmp_path, monkeypatch):
    """«Прочитай текст» и «что на картинке» — два разных ответа на одну и ту же
    картинку; общий ключ отдавал бы один вместо другого."""
    seen = _calls(monkeypatch)
    c = _connector(tmp_path)
    c.call(source="https://site/pic.jpg", features=av.DESCRIBE_FEATURES)
    c.call(source="https://site/pic.jpg", features=av.READ_FEATURES)
    assert len(seen) == 2


def test_missing_file_is_a_refusal_not_a_crash(tmp_path, monkeypatch):
    _calls(monkeypatch)
    result = _connector(tmp_path).call(source=str(tmp_path / "нет-такого.png"))
    assert not result.ok and result.reason == "error"


def test_empty_file_is_refused_before_the_network(tmp_path, monkeypatch):
    seen = _calls(monkeypatch)
    result = _connector(tmp_path).call(source=str(_image(tmp_path, "пусто.png", b"")))
    assert not result.ok and seen == []


def test_oversized_image_is_refused_before_the_network(tmp_path, monkeypatch):
    """Свой отказ понятнее чужого 400 и не тратит квоту."""
    seen = _calls(monkeypatch)
    big = _image(tmp_path, "big.png", b"x" * (av.MAX_IMAGE_BYTES + 1))
    result = _connector(tmp_path).call(source=str(big))
    assert not result.ok and seen == []
    assert "МБ" in result.data["error"]


def test_quota_survives_and_then_refuses(tmp_path, monkeypatch):
    _calls(monkeypatch)
    c = _connector(tmp_path, daily_quota=1)
    c.call(source="https://site/1.jpg")
    second = c.call(source="https://site/2.jpg")
    assert not second.ok and second.reason == "quota"


def test_region_without_caption_is_asked_again_without_it(tmp_path, monkeypatch):
    """Живой случай, а не выдумка: в swedencentral caption отвечает 400 при
    исправном ключе. caption стоит первым в DESCRIBE_FEATURES, поэтому без
    обхода «что на картинке» не работало бы вообще — и ответ был бы «не ответил»,
    то есть причина осталась бы невидимой."""
    seen = _sequence(monkeypatch, [(REGION_ERROR, 400), (SCENE, 200)])
    result = _connector(tmp_path).call(source="https://site/pic.jpg")
    assert result.ok
    assert len(seen) == 2
    assert "caption" in seen[0]["url"]
    # Снято ровно то, чего нет. Остальное обязано уцелеть: иначе ответ пустеет
    # без причины, и виноватым выглядит Azure.
    assert "caption" not in seen[1]["url"]
    assert "tags" in seen[1]["url"] and "objects" in seen[1]["url"]


def test_region_gap_is_remembered_between_calls(tmp_path, monkeypatch):
    """Коннектор пересобирается фабрикой на каждый вызов, поэтому память живёт в
    модуле. Иначе лишний 400 платился бы на каждой картинке."""
    seen = _sequence(monkeypatch, [(REGION_ERROR, 400), (SCENE, 200), (SCENE, 200)])
    _connector(tmp_path).call(source="https://site/1.jpg")
    _connector(tmp_path).call(source="https://site/2.jpg")
    assert len(seen) == 3
    assert "caption" not in seen[2]["url"]


def test_region_answer_lists_what_is_seen(tmp_path, monkeypatch):
    """Без caption ответ обязан звучать перечислением. «Описания нет» человек
    слышит как отказ, хотя ответ есть — просто списком."""
    no_caption = {k: v for k, v in SCENE.items() if k != "captionResult"}
    _sequence(monkeypatch, [(REGION_ERROR, 400), (no_caption, 200)])
    message = _connector(tmp_path).call(source="https://site/pic.jpg").message
    assert "cat" in message and "по-английски" in message
    assert "Описания нет" not in message


def test_retry_does_not_read_the_file_again(tmp_path, monkeypatch):
    """Повтор после регионального отказа отправляет ТЕ ЖЕ байты, а не читает
    диск заново: screenshot.png перезаписывается каждым новым снимком, и второе
    чтение отправило бы уже другую картинку — с чужой перепиской, например.

    Сравниваем с обычным вызовом, а не с числом: файл и так читается дважды
    (один раз хешируется в ключ кеша, один раз уходит в запрос), и вписать «2»
    значило бы зафиксировать в тесте деталь, к повтору не относящуюся.
    """
    reads = []
    original = av.Path.read_bytes
    monkeypatch.setattr(
        av.Path, "read_bytes", lambda self: (reads.append(str(self)), original(self))[1]
    )

    # Литерал ASCII: в bytes кириллица не компилируется вовсе.
    plain = _image(tmp_path, "plain.png", b"PNG-plain")
    _sequence(monkeypatch, [(SCENE, 200)])
    _connector(tmp_path).call(source=str(plain))
    without_retry = reads.count(str(plain))

    shot = _image(tmp_path, "screenshot.png", b"PNG-first-shot")
    seen = _sequence(monkeypatch, [(REGION_ERROR, 400), (SCENE, 200)])
    result = _connector(tmp_path).call(source=str(shot))

    assert result.ok
    assert len(seen) == 2 and seen[0]["bytes"] == seen[1]["bytes"] == b"PNG-first-shot"
    assert reads.count(str(shot)) == without_retry


def test_other_errors_are_not_retried(tmp_path, monkeypatch):
    """Повтор — только из-за региона. 401 и 429 от повтора не чинятся, а квоту
    тратят вдвое."""
    seen = _sequence(monkeypatch, [({"error": {"code": "401", "message": "нет"}}, 401)])
    result = _connector(tmp_path).call(source="https://site/pic.jpg")
    assert not result.ok and len(seen) == 1


def test_region_without_anything_left_refuses_with_the_reason(tmp_path, monkeypatch):
    """Если в регионе нет единственной запрошенной фичи, повторять нечем —
    отказ обязан назвать причину Azure, а не промолчать."""
    seen = _sequence(monkeypatch, [(REGION_ERROR, 400)])
    result = _connector(tmp_path).call(source="https://site/pic.jpg", features="caption")
    assert not result.ok and len(seen) == 1
    assert "not supported in this region" in result.data["error"]
