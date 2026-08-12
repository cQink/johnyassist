"""Сборка коннекторов из конфига: ключи, согласие, настройки тарифа.

Главное, что здесь проверяется, — согласие НЕ включается само. Наличие
оплаченного ключа не значит «разрешено искать людей», и цена ошибки в этом
месте не деньги.
"""

from dataclasses import dataclass, field

import johnny.connectors.factory as factory


@dataclass
class FakeSettings:
    connectors: dict = field(default_factory=dict)


@dataclass
class FakeConfig:
    settings: FakeSettings = field(default_factory=FakeSettings)
    secrets: dict = field(default_factory=dict)


def _config(connectors=None, secrets=None):
    return FakeConfig(FakeSettings(connectors or {}), secrets or {})


def test_unknown_name_returns_none():
    assert factory.build("нет-такого", _config()) is None


def test_key_from_secrets_makes_connector_available():
    c = factory.build(
        "azure-vision",
        _config({"azure-vision": {"endpoint": "https://r.cognitiveservices.azure.com"}},
                {"azure_vision_key": "k"}),
    )
    assert c.available() == (True, "")


def test_missing_key_is_honest_refusal_not_crash():
    """Коннектор без ключа обязан собраться: команда должна ответить «нужен
    ключ», а не «не понял» — это разные вещи для человека."""
    c = factory.build("azure-vision", _config())
    ready, why = c.available()
    assert not ready and "ключ" in why.lower()


def test_azure_key_without_endpoint_names_the_endpoint():
    """Ключ есть, адреса нет — отказ обязан назвать именно адрес. Иначе человек
    пойдёт искать второй ключ, которого не существует."""
    c = factory.build("azure-vision", _config(secrets={"azure_vision_key": "k"}))
    ready, why = c.available()
    assert not ready and "endpoint" in why.lower()


def test_azure_endpoint_comes_from_settings():
    """Адрес ресурса у каждого свой, общего нет — только из настроек."""
    config = _config({"azure-vision": {"endpoint": "https://мой.cognitiveservices.azure.com/"}})
    # Хвостовой слэш срезается: путь к API склеивается с ним напрямую, и двойной
    # слэш Azure отвергает как неизвестный маршрут.
    assert factory.build("azure-vision", config)._endpoint.endswith("azure.com")


def test_faceplusplus_needs_both_key_and_secret():
    """Доступ — ПАРА. Половина пары не должна выглядеть как готовность."""
    only_key = factory.build("faceplusplus", _config(secrets={"faceplusplus_api_key": "k"}))
    assert only_key.available()[0] is False
    both = factory.build(
        "faceplusplus",
        _config(secrets={"faceplusplus_api_key": "k", "faceplusplus_api_secret": "s"}),
    )
    assert both.available() == (True, "")


def test_key_never_leaks_into_refusal_text():
    """Причина отказа уходит человеку в ответ и в лог: ключа в ней быть не
    должно ни при каком раскладе."""
    c = factory.build(
        "faceplusplus",
        _config(secrets={"faceplusplus_api_key": "СЕКРЕТ123", "faceplusplus_api_secret": "S2"}),
    )
    c._api_secret = ""
    reason = c.available()[1]
    assert "СЕКРЕТ123" not in reason and "S2" not in reason


def test_consent_is_off_by_default():
    """Ключ есть, согласия нет — запроса не будет."""
    c = factory.build("faceplusplus", _config(secrets={"faceplusplus_api_key": "k"}))
    assert c._consent is False


def test_general_consent_enables_connector():
    c = factory.build("azure-vision", _config({"consent": True}, {"azure_vision_key": "k"}))
    assert c._consent is True


def test_per_connector_consent_overrides_general():
    """«Всё, кроме лиц» — одной строкой. Иначе человек выключает целиком и не
    пользуется ничем."""
    config = _config(
        {"consent": True, "faceplusplus": {"consent": False}},
        {"faceplusplus_api_key": "k", "azure_vision_key": "k"},
    )
    assert factory.build("azure-vision", config)._consent is True
    assert factory.build("faceplusplus", config)._consent is False


def test_per_connector_consent_can_enable_alone():
    config = _config({"azure-vision": {"consent": True}}, {"azure_vision_key": "k"})
    assert factory.build("azure-vision", config)._consent is True


def test_daily_quota_comes_from_settings():
    """Лимит зависит от тарифа: константа в коде либо режет оплаченное, либо
    не спасает."""
    c = factory.build(
        "azure-vision", _config({"azure-vision": {"daily_quota": 7}}, {"azure_vision_key": "k"})
    )
    assert c.daily_quota == 7


def test_social_analyzer_needs_no_key():
    c = factory.build("social-analyzer", _config())
    assert not hasattr(c, "_api_key")


def test_missing_connectors_block_is_not_an_error():
    """Блока connectors в settings.yaml может не быть вовсе — тогда всё
    выключено, и это правильное значение по умолчанию."""
    c = factory.build("azure-vision", FakeConfig())
    assert c._consent is False


def test_cache_dir_is_passed_through(tmp_path):
    c = factory.build("azure-vision", _config(), cache_dir=tmp_path)
    assert c.cache_dir == tmp_path


def test_available_names_lists_every_builder():
    assert set(factory.available_names()) == {
        "azure-vision",
        "face-index",
        "faceplusplus",
        "social-analyzer",
        "translator",
    }


def test_face_index_needs_no_key_and_no_consent():
    """Единственный коннектор, который наружу не ходит вовсе: вектор считается
    на этой машине. Согласие значит «разрешаю отправить третьей стороне», и
    спрашивать его там, где отправки нет, — приучать жать «да» не глядя.

    Ключа у него тоже нет, поэтому пустой secrets.yaml его не выключает.
    """
    connector = factory.build("face-index", FakeConfig())
    assert connector is not None
    assert connector.requires_consent is False


def test_translator_endpoint_comes_from_settings():
    """Адрес LibreTranslate — настройка, а не константа: у своего инстанса в
    локальной сети он свой."""
    config = _config({"translator": {"endpoint": "http://nas:5000/translate"}})
    assert factory.build("translator", config)._endpoint == "http://nas:5000/translate"


def test_translator_has_no_endpoint_by_default():
    """Пустой адрес значит «только офлайн». Публичный инстанс по умолчанию
    означал бы, что продиктованный текст молча уезжает на чужой сервер."""
    assert factory.build("translator", _config())._endpoint == ""


def test_translator_needs_no_consent():
    c = factory.build("translator", _config())
    assert c.requires_consent is False
