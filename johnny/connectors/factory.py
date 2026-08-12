"""Сборка коннекторов из конфига — единственное место, где живут имена ключей.

Зачем отдельный модуль: коннектор нужен и действию (голосовая команда), и
панели, и, со временем, плагину. Если каждый собирает его сам, имя ключа в
secrets.yaml оказывается прописано в трёх местах, и переименование ломает два
из них молча.

Согласие берётся из settings.yaml (connectors.consent), а не из ключа: наличие
токена значит «могу», а не «разрешено». Разводить эти два понятия обязательно —
иначе оплата ключа сама по себе включала бы поиск по лицам.
"""

from __future__ import annotations

import logging
from pathlib import Path

from .azure_vision import AzureVisionConnector
from .face_index import FaceIndexConnector
from .faceplusplus import FacePlusPlusConnector
from .social_analyzer import SocialAnalyzerConnector
from .translator import TranslatorConnector

logger = logging.getLogger(__name__)

# Имя коннектора → класс и карта «аргумент конструктора → имя в secrets.yaml».
# Карта, а не одна строка: у Face++ доступ — это ПАРА, ключ и секрет отдельными
# строками в консоли, и передать её одним значением нельзя.
#
# Ключа может не быть вовсе: коннектор тогда честно откажет через available(), а
# не исчезнет из списка — «команда не существует» и «нет ключа» человеку звучат
# по-разному.
BUILDERS = {
    "azure-vision": (AzureVisionConnector, {"api_key": "azure_vision_key"}),
    # Локальный, ключа нет и наружу не ходит — но это единственный коннектор,
    # который хранит биометрию на диске (models/face-index). Согласие ему не
    # нужно, а вот про сам факт хранения человек знать обязан.
    "face-index": (FaceIndexConnector, {}),
    "faceplusplus": (
        FacePlusPlusConnector,
        {"api_key": "faceplusplus_api_key", "api_secret": "faceplusplus_api_secret"},
    ),
    # Локальный пакет, ключ не нужен.
    "social-analyzer": (SocialAnalyzerConnector, {}),
    # Локальный движок; ключ нужен только чужому LibreTranslate, и то не всякому.
    "translator": (TranslatorConnector, {"api_key": "libretranslate_api_key"}),
}


def build(name: str, config, *, cache_dir=None):
    """Готовый коннектор по имени или None, если такого нет.

    config — обычный Config (нужны secrets и settings). Передаём его целиком,
    а не разобранным на части: набор нужных полей у коннекторов разный и будет
    расти, а протаскивать каждый новый параметр через четыре сигнатуры — тот
    самый способ получить рассинхрон.
    """
    entry = BUILDERS.get(name)
    if entry is None:
        return None
    connector_class, secret_names = entry

    secrets = getattr(config, "secrets", None) or {}
    options = _options(config)
    per_connector = options.get(name) or {}

    kwargs = {"consent": _consent(options, name)}
    for argument, secret_key in secret_names.items():
        kwargs[argument] = str(secrets.get(secret_key) or "")
    if cache_dir:
        kwargs["cache_dir"] = Path(cache_dir)
    # Квота, глубина обхода, адрес ресурса Azure или своего переводчика — всё,
    # что зависит от тарифа и намерения, а не от кода.
    for option in ("daily_quota", "top", "endpoint"):
        if option in per_connector:
            kwargs[option] = per_connector[option]
    return connector_class(**kwargs)


def available_names() -> list[str]:
    return list(BUILDERS)


def _options(config) -> dict:
    """Блок connectors из settings.yaml. Его может не быть вовсе — тогда всё
    выключено по умолчанию, и это правильное значение по умолчанию: коннекторы
    тратят деньги и трогают персональные данные."""
    settings = getattr(config, "settings", None)
    options = getattr(settings, "connectors", None)
    return options if isinstance(options, dict) else {}


def _consent(options: dict, name: str) -> bool:
    """Согласие: общее для всех (consent: true) или точечное для коннектора.

    Точечное сильнее общего — включить всё, кроме поиска по лицам, должно быть
    можно одной строкой, иначе человек выключает целиком и не пользуется ничем.
    """
    per_connector = options.get(name) or {}
    if isinstance(per_connector, dict) and "consent" in per_connector:
        return bool(per_connector["consent"])
    return bool(options.get("consent", False))
