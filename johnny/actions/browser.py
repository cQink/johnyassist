"""Браузерные действия: открытие URL/каналов, перемотка, полноэкранный режим."""

import webbrowser

from .registry import ActionResult, registry

_SCHEMES = ("http://", "https://", "steam://")
_CHANNEL_HOSTS = {"twitch": "twitch.tv", "youtube": "youtube.com"}


# ── Утилиты (используются другими модулями — НЕ часть реестра) ────────────

def open_url(argument: str) -> None:
    """Открыть URL в браузере по умолчанию."""
    url = argument if argument.startswith(_SCHEMES) else "https://" + argument
    webbrowser.open(url)


def _resolve_channel(alias: str, platform: str, channels: dict):
    """Найти слаг канала по произнесённому имени (в любом падеже). None — не нашли."""
    from ..morph import stem_phrase

    key = stem_phrase(alias)
    for info in channels.values():
        slug = info.get(platform)
        if not slug:
            continue
        if any(stem_phrase(str(a)) == key for a in info.get("aliases", [])):
            return slug
    return None


# ── Зарегистрированные действия ──────────────────────────────────────────────

@registry.register("open_url")
def action_open_url(argument: str, ctx: dict) -> ActionResult:
    open_url(argument)
    return ActionResult(True, "Открываю")


@registry.register("open_channel")
def action_open_channel(argument: str, ctx: dict) -> ActionResult:
    """argument = «platform|имя»: открыть канал по словарю, иначе — буквально."""
    channels = ctx.get("channels") or {}
    new_tab = ctx.get("new_tab", True)
    platform, _, alias = argument.partition("|")
    host = _CHANNEL_HOSTS.get(platform, "twitch.tv")
    slug = _resolve_channel(alias, platform, channels) or alias
    action_browser_open(f"{host}/{slug}", {"new_tab": new_tab})
    return ActionResult(True, "Открываю")


@registry.register("browser_open")
def action_browser_open(argument: str, ctx: dict) -> ActionResult:
    """Открыть URL в окне Chrome-Джони; при сбое — в браузере по умолчанию."""
    from .. import browser

    new_tab = ctx.get("new_tab", True)
    try:
        browser.open(argument, new_tab=new_tab)
    except Exception:
        open_url(argument)
    return ActionResult(True, "Открываю")


@registry.register("browser_seek")
def action_browser_seek(argument: str, ctx: dict) -> ActionResult:
    """«13 42» — абсолютно; «5 минут вперёд/назад» — относительно."""
    from .. import browser

    text = argument.lower()
    seconds = browser.parse_time_ru(text)
    if seconds is None:
        return ActionResult(False, "Не понял время")
    if any(word in text for word in ("вперёд", "вперед", "дальше")):
        browser.seek_relative(seconds)
    elif "назад" in text:
        browser.seek_relative(-seconds)
    else:
        browser.seek(seconds)
    return ActionResult(True, "Готово")


@registry.register("browser_click_result")
def action_browser_click_result(argument: str, ctx: dict) -> ActionResult:
    from .. import browser

    if browser.click_result(int(argument)):
        return ActionResult(True, "Включаю")
    return ActionResult(False, "Не нашёл видео на странице")


@registry.register("browser_next")
def action_browser_next(argument: str, ctx: dict) -> ActionResult:
    from .. import browser

    if browser.next_video():
        return ActionResult(True, "Дальше")
    return ActionResult(False, "Не нашёл кнопку следующего видео")


@registry.register("browser_fullscreen")
def action_browser_fullscreen(argument: str, ctx: dict) -> ActionResult:
    from .. import browser

    if browser.fullscreen():
        return ActionResult(True, "Готово")
    return ActionResult(False, "Не нашёл плеер на странице")


@registry.register("browser_focus")
def action_browser_focus(argument: str, ctx: dict) -> ActionResult:
    from .. import browser

    browser.bring_to_front()
    return ActionResult(True, "Готово")
