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

# Windows поднимает окно консоли на КАЖДЫЙ запуск консольной программы. При
# опросе раз в пять секунд это чёрный прямоугольник, мигающий поверх всего —
# в том числе поверх полноэкранной игры, ради которой сторож и заведён.
# Живая жалоба владельца 2026-08-23: «убери консоль, которая появляется раз в
# 5 сек». Флаг есть только на Windows; на остальных системах его нет, но там и
# окно не всплывает, поэтому ноль — правильное значение по умолчанию.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


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
            creationflags=_NO_WINDOW,      # без этого раз в 5 секунд мигает консоль
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
        # Проверка device обязательна отдельно от whisper_model_gaming:
        # панель (johnny/panel.py) даёт переключить whisper_device в один
        # клик, и на процессоре подменять нечего — видеопамять там не
        # расходуется. Продолжи сторож работать на cpu, он бы перезагружал
        # модель на процессоре (секунды простоя, потеря точности) без всякой
        # экономии, ради которой всё затевалось, а запасной признак свободной
        # видеопамяти (её на cpu-машине честно мало) уводил бы на small вовсе
        # без причины.
        return (
            bool(getattr(self._settings, "whisper_model_gaming", ""))
            and getattr(self._settings, "whisper_device", "") == "cuda"
        )

    def tick(self) -> str:
        """Один опрос. Возвращает имя модели, которая должна стоять сейчас.

        Сбой признака не должен ронять поток: сторож фоновый, и его смерть
        осталась бы незамеченной до перезапуска Джони — а Whisper при этом
        навсегда застрял бы на той модели, что была в тот момент.
        """
        if not self.enabled:
            # Настройкой выключено — decide() и так вернёт нормальную модель,
            # но не читаем признаки и не зовём use_model вовсе: незачем
            # опрашивать реестр и nvidia-smi ради решения, которое заведомо
            # ничего не поменяет.
            return self._recognizer.model_name

        try:
            game = bool(self._game_running())
        except Exception:
            logger.warning("Не удалось прочитать признак игры", exc_info=True)
            game = False

        free = None
        if not game:
            # При идущей игре decide() смотрит только на game_running и до
            # free_mb не доходит (первая же строка после проверки enabled).
            # Не будим nvidia-smi ради значения, которое всё равно
            # проигнорируют, — это подпроцесс с таймаутом 5 секунд, и звать
            # его на каждом опросе ровно во время игры значило бы отбирать у
            # неё ресурсы, которые сторож призван возвращать.
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
            # Опрос до цикла, а не только внутри while: без него игра, уже
            # идущая в момент запуска Джони (автозапуск при входе в систему
            # плюс ~3.5 минуты на загрузку моделей — человек успевает войти в
            # игру раньше), была бы замечена только через poll_seconds, а к
            # этому моменту modeль уже заняла бы 2138 МБ карты, которую делит
            # с игрой. try/except — тот же, что и в цикле ниже: сбой первого
            # опроса не должен помешать потоку завестись.
            try:
                self.tick()
            except Exception:
                logger.exception("Сторож видеопамяти упал на первом опросе")
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

    def stop(self, *, join_timeout: float = 3.0) -> None:
        """Остановить фоновый опрос и дождаться, что поток и правда встал.

        join обязателен, а не только взвести событие: без него тик, уже
        вошедший в self.tick() (например, в разгаре подмены модели — это
        ~2.4 секунды), доиграл бы её уже ПОСЛЕ того, как вызывающий код решил,
        что путь свободен. Для restart_app в трее это не абстракция: именно в
        это окно новый процесс Джони начинает грузить модели на ту же карту.
        join с запасом по времени закрывает окно, а не только сужает его.

        Событие взводится один раз и не сбрасывается, _thread не обнуляется.
        Повторный start() после stop() тихо ничего не сделает (см. проверку
        `self._thread is not None` в начале start()) — как и повторный
        stop(), он просто сразу же join'ится на уже мёртвом потоке. Сейчас
        никто сторож не перезапускает, поэтому это не чинится — но если
        понадобится, перед новым start() нужно будет создать новый Guard или
        явно сбросить _thread и _stop.
        """
        self._stop.set()
        if self._thread is not None:
            self._thread.join(join_timeout)
            if self._thread.is_alive():
                logger.warning(
                    "Сторож видеопамяти не остановился за %.1fс", join_timeout
                )
        logger.info("Сторож видеопамяти остановлен")
