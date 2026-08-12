import logging
import os
import re
import socket
import subprocess
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent
_PROFILE = _ROOT / "chrome-profile"
_PORT = 9222
_driver = None
_tab = None  # handle вкладки, в которой Джони работает сейчас


def _chrome_exe() -> str:
    for candidate in (
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ):
        if os.path.exists(candidate):
            return candidate
    return "chrome"


def _port_open() -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.3)
        return sock.connect_ex(("127.0.0.1", _PORT)) == 0


def _launch_chrome() -> None:
    subprocess.Popen(
        [
            _chrome_exe(),
            f"--user-data-dir={_PROFILE}",
            f"--remote-debugging-port={_PORT}",
            "--no-first-run",
            "--no-default-browser-check",
        ]
    )
    for _ in range(60):  # ждём, пока поднимется debug-порт
        if _port_open():
            time.sleep(0.5)
            return
        time.sleep(0.3)


def get_driver():
    """Selenium-драйвер, прицепленный к окну Chrome-Джони (лениво поднимает)."""
    global _driver
    if _driver is not None:
        try:
            _ = _driver.current_url  # жив ли
            return _driver
        except Exception:
            _driver = None
    if not _port_open():
        _launch_chrome()
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options

    opts = Options()
    opts.add_experimental_option("debuggerAddress", f"127.0.0.1:{_PORT}")
    _driver = webdriver.Chrome(options=opts)
    return _driver


def _pages(driver) -> list:
    """Список вкладок. Пустой — у браузера нет ни одной страницы."""
    try:
        return driver.window_handles
    except Exception:
        return []


def _active():
    """Драйвер, наведённый на вкладку Джони.

    ЕДИНСТВЕННАЯ точка переключения. Раньше её не было вовсе: драйвер
    цеплялся к произвольной вкладке один раз и работал с ней всегда — из-за
    этого цепочка «найди → включи первое видео → на полный экран» могла
    отработать в трёх разных местах, а фуллскрин честно не находил плеер.

    Браузер без единой страницы (процесс жив, окна закрыты — в памяти его
    держат расширения) получает ОКНО: вкладка там бесполезна, показать её
    негде. Пропавшая вкладка Джони — не ошибка, работаем с текущей.
    """
    driver = get_driver()
    pages = _pages(driver)  # один запрос: эту функцию зовёт каждое действие
    if not pages:
        driver.switch_to.new_window("window")
        return driver
    if _tab is not None and _tab in pages:
        driver.switch_to.window(_tab)
    return driver


def bring_to_front() -> None:
    """Поднять окно Chrome-Джони поверх остальных (иначе открывается в фоне)."""
    import win32con
    import win32gui

    driver = _active()
    try:
        title = driver.title or ""
    except Exception:
        title = ""

    found = []

    def _cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            text = win32gui.GetWindowText(hwnd)
            if text.endswith("Google Chrome") and (not title or title[:18] in text):
                found.append(hwnd)

    win32gui.EnumWindows(_cb, None)
    if not found:
        return
    hwnd = found[0]
    if win32gui.IsIconic(hwnd):  # разворачиваем ТОЛЬКО если свёрнуто,
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)  # не ломая максимизацию
    # topmost → notopmost поднимает окно наверх, не оставляя его «всегда сверху»
    for flag in (win32con.HWND_TOPMOST, win32con.HWND_NOTOPMOST):
        win32gui.SetWindowPos(hwnd, flag, 0, 0, 0, 0, win32con.SWP_NOMOVE | win32con.SWP_NOSIZE)
    _force_foreground(hwnd)


def _force_foreground(hwnd) -> None:
    """SetForegroundWindow сам по себе Windows часто МОЛЧА блокирует: с
    Windows 2000 действует foreground lock — процесс, который только что не
    получал ввод от пользователя (а голосовая команда Джони приходит именно
    так, из фонового потока), не может просто забрать передний план.

    ЖИВОЙ БАГ (2026-08-05): topmost/notopmost поднимает окно ВИЗУАЛЬНО (видно
    поверх остальных), но document.hasFocus() внутри Chrome оставался False
    ДАЖЕ ПОСЛЕ SetForegroundWindow — окно не становится настоящим foreground-
    приложением ОС, хотя видно его поверх всего. Судя по всему, именно
    статус foreground-приложения (а не просто Z-order) проверяет Fullscreen
    API перед тем как разрешить полноэкранный режим — этим объясняется,
    почему «открой видео на полный экран» не срабатывало, даже когда окно
    Chrome было визуально видно.

    ОБХОД, ПОПЫТКА №1: AttachThreadInput временно объединяет очередь ввода
    нашего потока с очередью потока, который ОС сейчас считает foreground.
    ЖИВОЙ БАГ (2026-08-05, попытка №1 провалилась): этого одного оказалось
    недостаточно — SetForegroundWindow всё равно падал с исключением
    (`pywintypes.error: (0, 'SetForegroundWindow', 'No error message is
    available')`), AttachThreadInput сам по себе foreground lock не снял.

    ОБХОД, ПОПЫТКА №2: синтетическое нажатие Alt через keybd_event прямо
    перед вызовом. Windows засчитывает это как «процесс только что получил
    ввод» и по этому основанию отдельно снимает foreground lock — тот же
    принцип, на котором уже держатся медиа-клавиши в actions.py. Держим оба
    приёма вместе (AttachThreadInput + синтетический ввод), а не заменяем
    один другим: они снимают разные условия одного и того же ограничения.
    """
    import win32api
    import win32con
    import win32gui
    import win32process

    before = win32gui.GetForegroundWindow()
    if before == hwnd:
        return
    current_thread = win32api.GetCurrentThreadId()
    target_thread = win32process.GetWindowThreadProcessId(before)[0] if before else 0
    attached = False
    try:
        if target_thread and target_thread != current_thread:
            win32process.AttachThreadInput(current_thread, target_thread, True)
            attached = True
        win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
        win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        logger.exception("bring_to_front: SetForegroundWindow сорвался")
    finally:
        if attached:
            win32process.AttachThreadInput(current_thread, target_thread, False)
    after = win32gui.GetForegroundWindow()
    logger.info("bring_to_front: foreground %s -> %s (хотели %s)", before, after, hwnd)


def open(url: str, new_tab: bool = True) -> None:
    """Открыть URL. По умолчанию — в новой вкладке, на которую переключаемся.

    new_tab=False (сказано «в этой вкладке») переиспользует вкладку Джони.
    """
    global _tab
    full = url if url.startswith(("http://", "https://")) else "https://" + url
    driver = get_driver()
    if not _pages(driver):
        driver.switch_to.new_window("window")  # зомби-браузер: нужно окно
    elif new_tab:
        driver.switch_to.new_window("tab")
    else:
        _active()
    _tab = driver.current_window_handle
    driver.get(full)
    bring_to_front()


def seek(seconds: int) -> None:
    """Абсолютная перемотка: на позицию seconds от начала."""
    _active().execute_script(
        "var v=document.querySelector('video'); if(v){v.currentTime=arguments[0];}", seconds
    )


def seek_relative(delta: int) -> None:
    """Относительная перемотка: +delta вперёд / -delta назад."""
    _active().execute_script(
        "var v=document.querySelector('video');"
        "if(v){v.currentTime=Math.max(0, v.currentTime+arguments[0]);}",
        delta,
    )


# Верхняя граница ожидания, а не задержка: обычно результаты поиска
# отрисовываются за 0.3–1с и шаг идёт дальше сразу. Восемь секунд человек
# ждёт только когда что-то действительно сломалось.
_WAIT_TIMEOUT = 8.0
_WAIT_POLL = 0.25

_RESULTS = "ytd-video-renderer #video-title, a#video-title-link"


def _wait_for(script: str, timeout: float = _WAIT_TIMEOUT) -> bool:
    """Опрашивать страницу, пока скрипт не вернёт истину. False — не дождались.

    Не WebDriverWait: вся работа со страницей в проекте уже написана на JS,
    заводить второй способ обращения к ней ради одной функции незачем.
    """
    driver = _active()
    deadline = time.monotonic() + timeout
    while True:
        if driver.execute_script(script):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(_WAIT_POLL)


def _click_when_ready(selector: str, timeout: float) -> bool:
    """Дождаться элемента и нажать. False — элемент так и не появился.

    Клик перепроверяет элемент сам: между ожиданием и нажатием страница может
    перерисоваться, и обращение к пропавшему элементу уронило бы скрипт.
    """
    if not _wait_for(f"return !!document.querySelector('{selector}');", timeout):
        return False
    return bool(
        _active().execute_script(
            f"var b=document.querySelector('{selector}');"
            "if(!b) return false; b.click(); return true;"
        )
    )


def click_result(n: int, timeout: float = _WAIT_TIMEOUT) -> bool:
    """Кликнуть N-й (1-based) видео-результат поиска YouTube.

    Ждёт, пока результатов станет хотя бы n: в цепочке этот шаг идёт сразу
    за поиском, и страница ещё грузится. Раньше пустой список означал
    «молча ничего не сделал», а вызывающий считал это успехом.
    """
    n = int(n)  # подставляется в JS, поэтому число обязано быть числом
    if not _wait_for(f"return document.querySelectorAll('{_RESULTS}').length >= {n};", timeout):
        return False
    return bool(
        _active().execute_script(
            f"var l=document.querySelectorAll('{_RESULTS}'); var el=l[{n} - 1];"
            "if(!el) return false; el.click(); return true;"
        )
    )


def next_video(timeout: float = _WAIT_TIMEOUT) -> bool:
    return _click_when_ready(".ytp-next-button", timeout)


# Сколько ждём, пока экран реально развернётся после нажатия. Переход не
# мгновенный, но и не долгий: если за это время document.fullscreenElement
# пуст — значит запрос отклонили, и врать «Готово» нельзя.
_FULLSCREEN_CONFIRM = 2.0


def _send_key(driver, key: str) -> None:
    """Настоящее нажатие клавиши через драйвер.

    Ввод, пришедший от драйвера, браузер считает жестом пользователя —
    в отличие от событий, созданных из JavaScript.
    """
    from selenium.webdriver.common.action_chains import ActionChains

    ActionChains(driver).send_keys(key).perform()


def fullscreen(timeout: float = _WAIT_TIMEOUT) -> bool:
    """Развернуть плеер на весь экран. False — не получилось.

    Кнопку нажать НЕЛЬЗЯ: Fullscreen API требует жеста пользователя, а клик
    из JavaScript таким жестом не считается — Chrome молча отклоняет запрос,
    и это выглядело как «сработало», хотя видео оставалось в окне. Поэтому
    шлём настоящее нажатие «f» (горячая клавиша и на YouTube, и на Twitch) и
    проверяем итог по document.fullscreenElement, а не по факту нажатия.

    Ищем универсальный `<video>` (а не кнопку конкретного сайта — раньше был
    `.ytp-fullscreen-button`, только YouTube, поэтому на Twitch плеер «не
    находился» никогда), но ФОКУС для «f» ставим на `#movie_player`, если он
    есть: живой тест 2026-08-03 показал, что фокус на голом `<video>` ломает
    именно YouTube — там хоткей «f» слушает контейнер плеера, а не сам тег
    `<video>`. `#movie_player` — универсальный контейнер YouTube (и на видео
    из поиска, и на отдельной странице), поэтому его достаточно проверить
    без завязки на URL. Сайты без такого контейнера (Twitch и т.п.) получают
    фокус на сам `<video>`, как раньше.

    ВИДЕО МОЖЕТ БЫТЬ НА ДРУГОЙ ВКЛАДКЕ: `_tab` — вкладка, которую Джони сам
    открыл ПОСЛЕДНИЙ РАЗ, а не обязательно та, куда сейчас смотрит человек
    (за сессию могло открыться несколько видео, или вкладку переключили
    вручную — Selenium не умеет спросить браузер, какая вкладка визуально
    активна, см. известное ограничение). Если на `_tab` видео не нашлось —
    ищем среди ВСЕХ открытых вкладок ту, где оно реально есть, и запоминаем
    её как новую `_tab`.
    """
    global _tab
    video_wait_start = time.monotonic()
    video_found = _wait_for("return !!document.querySelector('video');", timeout)
    # ДИАГНОСТИКА (2026-08-06): прошлый раунд логировал video-ожидание только
    # при провале — если оно проходит успешно, но МЕДЛЕННО (страница ещё не
    # долистала SPA-переход после click_result), это никак не было видно.
    logger.info(
        "Фуллскрин: video %s за %.2fс на текущей вкладке",
        "найдено" if video_found else "НЕ найдено",
        time.monotonic() - video_wait_start,
    )
    if not video_found:
        # ДИАГНОСТИКА (2026-08-04): живая жалоба «не нашёл плеер» после
        # успешного click_result в цепочке — непонятно, реально ли клик
        # перевёл вкладку на страницу видео, или страница осталась на
        # результатах поиска. current_url/title показывают это без доступа
        # к самому браузеру.
        driver = _active()
        try:
            logger.info(
                "Фуллскрин: video не нашёлся на текущей вкладке (url=%r, title=%r)",
                driver.current_url,
                driver.title,
            )
        except Exception:
            logger.exception("Фуллскрин: не смог снять диагностику с текущей вкладки")
        driver = get_driver()
        for handle in _pages(driver):
            driver.switch_to.window(handle)
            found = driver.execute_script("return !!document.querySelector('video');")
            try:
                logger.info(
                    "Фуллскрин: проверяю вкладку %r (url=%r) -> video=%s",
                    handle,
                    driver.current_url,
                    found,
                )
            except Exception:
                logger.exception("Фуллскрин: не смог снять диагностику со вкладки %r", handle)
            if found:
                _tab = handle
                break
        else:
            return False
    driver = _active()
    if driver.execute_script("return !!document.fullscreenElement;"):
        return True  # уже развёрнуто; повторное «f» свернуло бы обратно
    # ГИПОТЕЗА (2026-08-05, по живым логам): оба прогона нашли video/movie_player
    # МГНОВЕННО (0.02с — SPA-переход ни при чём), но fullscreenElement не
    # появлялся ни разу, хотя document.hasFocus() был True в одном из двух
    # прогонов — то есть дело не во внутреннем фокусе документа. bring_to_front
    # (единственное место в коде, где окно реально поднимается поверх через
    # win32 SetForegroundWindow) вызывается только из open() — сюда, в
    # click_result→fullscreen, человек обычно приходит НЕ глядя в Chrome
    # (голосовая команда произносится из другого окна/приложения). Chrome
    # молча отклоняет requestFullscreen(), если окно-источник не на переднем
    # плане у ОС — а это Z-order всей Windows, а не document.hasFocus().
    bring_to_front()
    # ЖИВОЙ БАГ (2026-08-05): bring_to_front реально переключает foreground у
    # ОС (подтверждено логом bring_to_front: до/после), но document.hasFocus()
    # сразу после этого всё ещё False — переключение окна на уровне ОС и
    # уведомление КОНКРЕТНОЙ вкладки внутри Chrome идут через асинхронный IPC
    # между процессами браузера, мгновенной проверке взяться неоткуда.
    # Активно ждём hasFocus вместо одной мгновенной пробы.
    focus_wait_start = time.monotonic()
    got_focus = _wait_for("return document.hasFocus();", 1.5)
    logger.info(
        "Фуллскрин: document.hasFocus() %s за %.2fс после bring_to_front",
        "да" if got_focus else "нет (таймаут)",
        time.monotonic() - focus_wait_start,
    )
    # Без снятия фокуса «f» уйдёт в строку поиска и просто напечатается.
    driver.execute_script(
        "if(document.activeElement && document.activeElement.blur){document.activeElement.blur();}"
        "var t=document.querySelector('#movie_player') || document.querySelector('video');"
        "if(t && t.focus){t.focus();}"
    )
    # ДИАГНОСТИКА (2026-08-06): проверяем гипотезу «страница/вкладка не в
    # фокусе браузера» — hasFocus/hidden покажут, реально ли вкладка активна
    # в момент нажатия «f» (хоткей YouTube может игнорировать событие, если
    # document не в фокусе, даже когда DOM-элемент формально сфокусирован).
    try:
        state = driver.execute_script(
            "return {hasFocus: document.hasFocus(), hidden: document.hidden, "
            "moviePlayer: !!document.querySelector('#movie_player'), "
            "active: document.activeElement && document.activeElement.tagName};"
        )
        logger.info("Фуллскрин: перед 'f' — %r", state)
    except Exception:
        logger.exception("Фуллскрин: не смог снять диагностику фокуса перед 'f'")
    key_at = time.monotonic()
    _send_key(driver, "f")
    ok = _wait_for("return !!document.fullscreenElement;", _FULLSCREEN_CONFIRM)
    logger.info("Фуллскрин: fullscreenElement=%s через %.2fс после 'f'", ok, time.monotonic() - key_at)
    return ok


def parse_time_ru(text: str):
    """«13 42»->822, «5 минут»->300, «1 час 5 минут»->3900. None, если не понял."""
    text = text.lower().strip()
    m_hour = re.search(r"(\d+)\s*час", text)
    m_min = re.search(r"(\d+)\s*мин", text)
    m_sec = re.search(r"(\d+)\s*сек", text)
    if m_hour or m_min or m_sec:
        hours = int(m_hour.group(1)) if m_hour else 0
        minutes = int(m_min.group(1)) if m_min else 0
        seconds = int(m_sec.group(1)) if m_sec else 0
        return hours * 3600 + minutes * 60 + seconds
    nums = re.findall(r"\d+", text)
    if len(nums) == 3:  # часы минуты секунды
        return int(nums[0]) * 3600 + int(nums[1]) * 60 + int(nums[2])
    if len(nums) == 2:  # минуты секунды
        return int(nums[0]) * 60 + int(nums[1])
    if len(nums) == 1:
        return int(nums[0])
    return None
