import ctypes
import random
import threading
import time
from pathlib import Path

_SIGNALS_DIR = Path(__file__).resolve().parent.parent / "models" / "Signals"

# Громкость ВСЕГО, что Джони проигрывает через MCI (голос + звуки).
# Не системная громкость (pycaw) — та управляет всем компьютером, а тише
# нужен только сам Джони. 1.0 = как раньше, без изменений.
_VOLUME = 1.0


def set_volume(level: float) -> None:
    global _VOLUME
    _VOLUME = max(0.0, min(1.0, level))


# Алиас, который сейчас играет (или только что играл) — нужен команде «стоп»,
# чтобы пометить его на остановку (см. stop_all). Один Джони одновременно
# говорит/играет только один звук, поэтому одного слота достаточно.
_current_alias: str | None = None
# Алиасы, остановленные нарочно (через stop_all) — _wait_until_stopped
# сверяется с этим множеством, чтобы отличить намеренную остановку от
# естественного конца проигрывания.
_stopped_aliases: set[str] = set()
_lock = threading.Lock()

# Как часто опрашиваем "status ... mode", ожидая конца проигрывания (или
# пометку stop_all). Компромисс: короче — отзывчивее «стоп», длиннее — меньше
# нагрузка на CPU. 50мс неощутимо на слух ни для отклика, ни для лишней
# задержки в конце обычной, не прерванной фразы.
_STATUS_POLL_SECONDS = 0.05


def stop_all() -> None:
    """Пометить то, что сейчас играет, на остановку (команда «стоп»).

    Тишина, если нечего останавливать. НЕ шлёт "stop" сюда сама: живой тест
    (2026-08-03) показал, что MCI на машине пользователя ИГНОРИРУЕТ "stop
    {alias}", посланный ИЗ ДРУГОГО потока, пока в том же алиасе крутится
    "play" — команда просто теряется, три подтверждённых по логу stop_all()
    подряд не остановили ни одного звука. Судя по всему, MCI на этой системе
    надёжен, только когда команды алиасу подаёт ОДИН И ТОТ ЖЕ поток, что его
    открыл. Поэтому здесь только пометка — реальный "stop" шлёт поток,
    который играет (см. _wait_until_stopped в _play_mci), из СВОЕГО потока."""
    with _lock:
        alias = _current_alias
        if alias:
            _stopped_aliases.add(alias)


def _pick(folder: str):
    """Случайный .mp3 из models/Signals/<folder>. None, если нет.

    Берём только mp3: системный проигрыватель (MCI) не открывает m4a.
    """
    directory = _SIGNALS_DIR / folder
    if not directory.is_dir():
        return None
    files = sorted(directory.glob("*.mp3"))
    return random.choice(files) if files else None


def _wait_until_stopped(winmm, alias: str) -> None:
    """Ждать конца проигрывания alias, опрашивая статус вместо блокирующего
    "play ... wait" (см. _play_mci, почему не wait).

    Если alias попал в _stopped_aliases (stop_all с другого потока) — шлём
    "stop" САМИ, из этого же потока (того, что открыл alias) — единственный
    способ, эмпирически подтверждённый как надёжный на машине пользователя.
    """
    buffer = ctypes.create_unicode_buffer(32)
    while True:
        with _lock:
            marked_for_stop = alias in _stopped_aliases
        if marked_for_stop:
            winmm.mciSendStringW(f"stop {alias}", None, 0, None)
            return
        # Явно ждём "stopped" (а не "не playing"): MCI знает промежуточные
        # состояния вроде "seeking", и трактовать их как конец проигрывания
        # обрезало бы звук раньше времени — регресс хуже исходного бага.
        # Сбой самого запроса статуса (device закрыли извне и т.п.) тоже
        # считаем концом — иначе цикл крутился бы вечно.
        status_error = winmm.mciSendStringW(f"status {alias} mode", buffer, len(buffer), None)
        if status_error != 0 or buffer.value.strip().lower() == "stopped":
            return
        time.sleep(_STATUS_POLL_SECONDS)


def _play_mci(path: str) -> None:
    """Проиграть файл через системный MCI (winmm).

    mciSendStringW возвращает MCIERROR (0 = успех). Раньше код его игнорировал
    целиком, поэтому play_file() никогда не бросал исключение, даже когда
    winmm не смог ни открыть, ни проиграть файл (не хватает кодека, файл
    занят, битый mp3) — из-за этого except вокруг play(...) в tts_fish.say()
    был мёртвым кодом, а play_random() врал True при полном отказе звука.

    ПОЧЕМУ БЕЗ ФЛАГА "wait": раньше play был блокирующим ("play {alias}
    wait"), а «стоп» слался через MCI из ДРУГОГО потока (см. stop_all). Живой
    тест (2026-08-03) показал, что на машине пользователя это НЕ работает —
    "stop" из чужого потока просто теряется, звук играет до конца несмотря на
    подтверждённый по логу stop_all(). Теперь play не блокирует, а конец
    проигрывания (или пометка stop_all) отслеживается опросом статуса в
    ТОМ ЖЕ потоке, что открыл alias, — см. _wait_until_stopped.
    """
    global _current_alias
    winmm = ctypes.windll.winmm
    alias = f"jsnd{random.randint(0, 1_000_000)}"
    open_error = winmm.mciSendStringW(f'open "{path}" alias {alias}', None, 0, None)
    if open_error != 0:
        raise OSError(f"MCI не смог открыть файл {path!r} (код {open_error})")
    with _lock:
        _current_alias = alias
    try:
        if _VOLUME < 1.0:
            # "setaudio" не гарантирован для всех драйверов MCI — если команда
            # откажет, лучше проиграть громко, чем провалить всю озвучку.
            level = round(_VOLUME * 1000)
            winmm.mciSendStringW(f"setaudio {alias} volume to {level}", None, 0, None)
        play_error = winmm.mciSendStringW(f"play {alias}", None, 0, None)
        if play_error != 0:
            raise OSError(f"MCI не смог проиграть файл {path!r} (код {play_error})")
        _wait_until_stopped(winmm, alias)
    finally:
        # Закрываем в любом случае, иначе при сбое проигрывания алиас MCI
        # остаётся висеть навсегда (утечка на каждую неудачную попытку).
        with _lock:
            if _current_alias == alias:
                _current_alias = None
            _stopped_aliases.discard(alias)
        winmm.mciSendStringW(f"close {alias}", None, 0, None)


def play_random(folder: str) -> bool:
    """Проиграть случайный звук из папки.

    False — если файлов нет ИЛИ если проигрывание не удалось: вызывающему
    нужно только «получилось / нет», чтобы решить, подавать ли слышимый
    резерв (сигнал + голос) вместо тишины — не пробрасываем ему OSError.
    """
    path = _pick(folder)
    if path is None:
        return False
    try:
        _play_mci(str(path))
    except OSError:
        return False
    return True


def play_file(path: str) -> None:
    """Проиграть конкретный аудиофайл (mp3) через системный MCI.

    В отличие от play_random, ошибку не глотает: тут её ждут вызывающие
    (tts_fish.say(), speaker._edge_synth_and_play()) — им нужно узнать о
    сбое, чтобы уйти на запасной голос.
    """
    _play_mci(path)
