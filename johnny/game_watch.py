"""Признак «идёт игра» и решение, какую модель Whisper держать.

Отдельный модуль именно потому, что решение проверяется без видеокарты и без
Steam: decide() — чистая функция, а два признака читаются двумя мелкими
функциями, которые в тестах подменяются.
"""
import logging
import subprocess
import threading

logger = logging.getLogger(__name__)

_STEAM_KEY = r"Software\Valve\Steam"
_NVIDIA_SMI_TIMEOUT = 5.0


def steam_game_running() -> bool:
    """True, если Steam запустил игру: RunningAppID — её номер, 0 — игры нет.

    Ключ означает «игра ЗАПУЩЕНА», а не «игра в фокусе», и это именно то, что
    нужно: свёрнутая игра видеопамять не отдаёт. Следи он за фокусом, Джони
    менял бы модель на каждый alt-tab по 2.4 секунды за раз.
    """
    try:
        import winreg
    except ImportError:  # не Windows — признака просто нет
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _STEAM_KEY) as key:
            value, _ = winreg.QueryValueEx(key, "RunningAppID")
    except OSError:
        # Steam не установлен, не запускался или ключа нет. Это не ошибка:
        # признак недоступен, и точка — жаловаться в лог не о чем.
        return False
    try:
        return int(value) != 0
    except (TypeError, ValueError):
        return False


def free_vram_mb() -> int | None:
    """Свободная видеопамять в мегабайтах. None — спросить не у кого.

    None и ноль — разные вещи, поэтому не int: на машине без nvidia-smi ноль
    означал бы «памяти нет совсем» и гнал бы Джони на маленькую модель вечно.
    """
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=_NVIDIA_SMI_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    lines = out.stdout.strip().splitlines()
    if not lines:
        return None
    try:
        return int(lines[0].strip())
    except ValueError:
        return None


def decide(
    current: str,
    *,
    normal_model: str,
    gaming_model: str,
    game_running: bool,
    free_mb: int | None,
    low_mb: int,
    high_mb: int,
) -> str:
    """Какую модель держать сейчас. Может вернуть ту же, что и была.

    Два порога вместо одного — это гистерезис, и он здесь не перестраховка.
    Сама подмена меняет свободную память на 1490 МБ (2138 у medium против 648
    у small). На одном пороге уход на small немедленно перевёл бы условие
    возврата, и Джони закольцевался бы, платя 2.4 секунды за круг. Поэтому
    зазор между low_mb и high_mb обязан быть шире 1490 МБ.
    """
    if not gaming_model:
        return normal_model  # выключено настройкой
    if game_running:
        return gaming_model
    if free_mb is None:
        # Спросить не у кого, и Steam молчит. Поводов уходить нет.
        return normal_model
    if free_mb < low_mb:
        return gaming_model
    if free_mb > high_mb:
        return normal_model
    return current  # мёртвая зона между порогами: не трогаем


_POLL_SECONDS = 5.0


class Guard:
    """Фоновый сторож: следит за признаками и просит сменить модель.

    Про Whisper знает ровно одно — что у распознавателя есть use_model и
    model_name. Признаки берёт функциями-аргументами, поэтому проверяется без
    видеокарты и без Steam.
    """

    def __init__(self, recognizer, settings, *, game_running=steam_game_running,
                 free_mb=free_vram_mb, poll_seconds: float = _POLL_SECONDS):
        self._recognizer = recognizer
        self._settings = settings
        self._game_running = game_running
        self._free_mb = free_mb
        self._poll = poll_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def enabled(self) -> bool:
        return bool(getattr(self._settings, "whisper_model_gaming", ""))

    def tick(self) -> str:
        """Один опрос. Возвращает имя модели, которая должна стоять сейчас.

        Сбой признака не должен ронять поток: сторож фоновый, и его смерть
        осталась бы незамеченной до перезапуска Джони — а Whisper при этом
        навсегда застрял бы на той модели, что была в тот момент.
        """
        try:
            game = bool(self._game_running())
        except Exception:
            logger.warning("Не удалось прочитать признак игры", exc_info=True)
            game = False
        try:
            free = self._free_mb()
        except Exception:
            logger.warning("Не удалось прочитать свободную видеопамять", exc_info=True)
            free = None

        current = self._recognizer.model_name
        wanted = decide(
            current,
            normal_model=self._settings.whisper_model,
            gaming_model=self._settings.whisper_model_gaming,
            game_running=game,
            free_mb=free,
            low_mb=self._settings.gpu_guard_low_mb,
            high_mb=self._settings.gpu_guard_high_mb,
        )
        # available, а не только сравнение имён: use_model (задача 1) может
        # провалиться дважды подряд (не поднялась ни новая модель, ни старая)
        # и оставить распознаватель пустым, а model_name — по-прежнему
        # называющим старую модель. Если бы тут сравнивались только имена,
        # такой двойной отказ прошёл бы незамеченным до следующей смены
        # признака, а до тех пор Джони был бы глухим без единого лога об
        # этом. getattr со значением по умолчанию True — чтобы фальшивки в
        # тестах без атрибута available вели себя как всегда готовые.
        recognizer_ready = getattr(self._recognizer, "available", True)
        if wanted != current or not recognizer_ready:
            self._recognizer.use_model(wanted)
        return wanted

    def start(self) -> None:
        """Запустить опрос в фоне. Выключенный настройкой сторож не стартует."""
        if not self.enabled or self._thread is not None:
            return

        def loop() -> None:
            while not self._stop.wait(self._poll):
                try:
                    self.tick()
                except Exception:
                    logger.exception("Сторож видеопамяти упал на опросе")

        self._thread = threading.Thread(target=loop, daemon=True)
        self._thread.start()
        logger.info(
            "Сторож видеопамяти запущен: во время игры модель %r",
            self._settings.whisper_model_gaming,
        )

    def stop(self) -> None:
        self._stop.set()
