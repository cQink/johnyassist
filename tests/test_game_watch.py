"""Решение «какую модель Whisper держать» — без видеокарты и без Steam."""
import subprocess
import threading
import time
import winreg

from johnny.game_watch import Guard, decide, free_vram_mb, steam_game_running

БАЗА = dict(normal_model="medium", gaming_model="small", low_mb=2500, high_mb=4500)


def test_игра_запущена_уходим_на_маленькую():
    assert decide("medium", game_running=True, free_mb=6000, **БАЗА) == "small"


def test_игры_нет_и_памяти_вдоволь_возвращаемся():
    assert decide("small", game_running=False, free_mb=6000, **БАЗА) == "medium"


def test_памяти_мало_уходим_даже_без_steam():
    """Игра мимо Steam: Epic, свой лаунчер, просто exe."""
    assert decide("medium", game_running=False, free_mb=1000, **БАЗА) == "small"


def test_между_порогами_ничего_не_трогаем():
    """Гистерезис. Без мёртвой зоны Джони метался бы туда-сюда, и каждое
    метание стоит 2.4 секунды."""
    assert decide("small", game_running=False, free_mb=3000, **БАЗА) == "small"
    assert decide("medium", game_running=False, free_mb=3000, **БАЗА) == "medium"


def test_free_mb_равен_low_mb_остаётся_в_мёртвой_зоне():
    """Сравнение в decide() строгое (<), поэтому равенство нижнему порогу не
    должно само по себе уводить на маленькую модель — иначе граница съехала
    бы на один мегабайт левее задуманной."""
    assert decide("medium", game_running=False, free_mb=БАЗА["low_mb"], **БАЗА) == "medium"


def test_free_mb_равен_high_mb_остаётся_в_мёртвой_зоне():
    """Симметрично: равенство верхнему порогу не должно возвращать обычную
    модель, иначе гистерезис сузился бы на единицу с другой стороны."""
    assert decide("small", game_running=False, free_mb=БАЗА["high_mb"], **БАЗА) == "small"


def test_мёртвая_зона_шире_того_что_освобождает_подмена():
    """Подмена сама меняет свободную память на 1490 МБ (2138 − 648). Если бы
    зазор между порогами был уже, уход на small тут же перевёл бы порог
    возврата, и Джони закольцевался бы."""
    assert БАЗА["high_mb"] - БАЗА["low_mb"] > 1490


def test_пустая_игровая_модель_выключает_переключение():
    """Значение по умолчанию: ничего не делаем никогда."""
    assert decide(
        "medium",
        normal_model="medium",
        gaming_model="",
        game_running=True,
        free_mb=100,
        low_mb=2500,
        high_mb=4500,
    ) == "medium"


def test_без_ответа_от_видеокарты_остаёмся_на_обычной():
    """nvidia-smi не отвечает — про игру ничего не известно. Молча уходить на
    маленькую модель было бы решением на пустом месте."""
    assert decide("medium", game_running=False, free_mb=None, **БАЗА) == "medium"


def test_none_и_ноль_свободной_памяти_это_разные_решения():
    """None ("спросить не у кого") и 0 ("памяти нет совсем") должны вести к
    разным моделям. Слей их в одно — и машина без nvidia-smi навсегда
    застряла бы на маленькой модели вместо обычной."""
    assert decide("medium", game_running=False, free_mb=None, **БАЗА) == "medium"
    assert decide("medium", game_running=False, free_mb=0, **БАЗА) == "small"


# --- free_vram_mb: subprocess.run подменяем, настоящий nvidia-smi не трогаем ---


def _результат(*, returncode=0, stdout=""):
    """Короткий помощник, чтобы не тащить в каждый тест конструктор
    CompletedProcess целиком."""
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


def test_free_vram_mb_разбирает_нормальный_вывод(monkeypatch):
    monkeypatch.setattr(
        "johnny.game_watch.subprocess.run",
        lambda *a, **kw: _результат(stdout="2048\n"),
    )
    assert free_vram_mb() == 2048


def test_free_vram_mb_nvidia_smi_не_найден(monkeypatch):
    """FileNotFoundError — подкласс OSError: на машине без видеокарты NVIDIA
    исполняемого файла попросту нет в PATH, и это не повод падать."""
    def бросить_отсутствие_файла(*a, **kw):
        raise FileNotFoundError("nvidia-smi не найден")

    monkeypatch.setattr("johnny.game_watch.subprocess.run", бросить_отсутствие_файла)
    assert free_vram_mb() is None


def test_free_vram_mb_ненулевой_returncode(monkeypatch):
    """Процесс запустился, но ответить не смог — доверять stdout нельзя."""
    monkeypatch.setattr(
        "johnny.game_watch.subprocess.run",
        lambda *a, **kw: _результат(returncode=1, stdout=""),
    )
    assert free_vram_mb() is None


def test_free_vram_mb_нечисловой_вывод(monkeypatch):
    """Формат вывода nvidia-smi вдруг сменился — парсить нечего."""
    monkeypatch.setattr(
        "johnny.game_watch.subprocess.run",
        lambda *a, **kw: _результат(stdout="N/A\n"),
    )
    assert free_vram_mb() is None


def test_free_vram_mb_пустой_вывод(monkeypatch):
    """Код возврата нулевой, но строк в выводе нет — тоже не число."""
    monkeypatch.setattr(
        "johnny.game_watch.subprocess.run",
        lambda *a, **kw: _результат(stdout=""),
    )
    assert free_vram_mb() is None


# --- steam_game_running: подменяем winreg, реестр не трогаем ---
#
# winreg импортируется внутри функции инструкцией `import winreg`, а не на
# уровне модуля. Импорт достаёт тот же объект модуля из sys.modules, что и
# `import winreg` здесь в тесте, поэтому monkeypatch.setattr на атрибуты
# этого модуля виден и внутри функции.


class _ФальшивыйКлюч:
    """Заглушка ключа реестра: OpenKey используется как контекстный менеджер."""

    def __enter__(self):
        return self

    def __exit__(self, *исключение):
        return False


def test_steam_game_running_running_app_id_не_ноль(monkeypatch):
    monkeypatch.setattr(winreg, "OpenKey", lambda *a, **kw: _ФальшивыйКлюч())
    monkeypatch.setattr(winreg, "QueryValueEx", lambda key, name: (570, winreg.REG_DWORD))
    assert steam_game_running() is True


def test_steam_game_running_running_app_id_ноль(monkeypatch):
    monkeypatch.setattr(winreg, "OpenKey", lambda *a, **kw: _ФальшивыйКлюч())
    monkeypatch.setattr(winreg, "QueryValueEx", lambda key, name: (0, winreg.REG_DWORD))
    assert steam_game_running() is False


def test_steam_game_running_ключа_нет(monkeypatch):
    """Steam не установлен или ни разу не запускался — ключа реестра просто
    нет. Это не ошибка, а отсутствие признака, поэтому OSError гасится."""
    def бросить_отсутствие_ключа(*a, **kw):
        raise FileNotFoundError("ключ не найден")

    monkeypatch.setattr(winreg, "OpenKey", бросить_отсутствие_ключа)
    assert steam_game_running() is False


# --- Guard: подмена модели в фоне ---


class ФальшивыйRecognizer:
    def __init__(self, model="medium"):
        self.model_name = model
        self.смены = []

    def use_model(self, model):
        self.смены.append(model)
        self.model_name = model
        return True


class ФальшивыеНастройки:
    whisper_model = "medium"
    whisper_model_gaming = "small"
    gpu_guard_low_mb = 2500
    gpu_guard_high_mb = 4500
    whisper_device = "cuda"


def _сторож(recognizer, *, игра, память):
    return Guard(
        recognizer,
        ФальшивыеНастройки(),
        game_running=lambda: игра,
        free_mb=lambda: память,
    )


def _дождаться(условие, *, тайм_аут=2.0):
    """Ждём условие опросом, а не фиксированным sleep — иначе тест либо
    слишком долгий, либо изредка падает на медленной машине."""
    предел = time.monotonic() + тайм_аут
    while time.monotonic() < предел:
        if условие():
            return
        time.sleep(0.005)
    assert условие(), "условие не наступило за отведённое время"


def test_запуск_игры_уводит_на_маленькую():
    recognizer = ФальшивыйRecognizer()

    _сторож(recognizer, игра=True, память=6000).tick()

    assert recognizer.смены == ["small"]


def test_повторный_опрос_не_дёргает_модель():
    """Сторож опрашивает раз в несколько секунд, а признак почти всегда тот же.
    Каждая лишняя подмена — 2.4 секунды и выброшенная рабочая модель."""
    recognizer = ФальшивыйRecognizer()
    guard = _сторож(recognizer, игра=True, память=6000)

    guard.tick()
    guard.tick()
    guard.tick()

    assert recognizer.смены == ["small"]


def test_выход_из_игры_возвращает_обычную():
    recognizer = ФальшивыйRecognizer("small")

    _сторож(recognizer, игра=False, память=6000).tick()

    assert recognizer.смены == ["medium"]


def test_сбой_признака_не_роняет_сторожа():
    """Сторож живёт в фоновом потоке. Упадёт — Джони останется на той модели,
    что была, и никто об этом не узнает до перезапуска."""
    recognizer = ФальшивыйRecognizer()

    def взрыв():
        raise OSError("реестр недоступен")

    guard = Guard(
        recognizer,
        ФальшивыеНастройки(),
        game_running=взрыв,
        free_mb=lambda: 6000,
    )

    assert guard.tick() == "medium"
    assert recognizer.смены == []


class ФальшивыйНедоступныйRecognizer:
    """Модель по имени та же, что нужна, но распознавать нечем: имитирует
    двойной отказ use_model из ревью задачи 1 — self._model остался None,
    хотя model_name всё ещё называет старую модель."""

    def __init__(self, model="medium", available=False):
        self.model_name = model
        self.available = available
        self.смены = []

    def use_model(self, model):
        self.смены.append(model)
        self.model_name = model
        self.available = True
        return True


def test_недоступный_распознаватель_подменяется_даже_при_совпадении_имени():
    """Дыра из ревью задачи 1: если сторож сравнивает только имена моделей,
    он никогда не заметит, что распознаватель на самом деле пуст (available
    == False), и Джони останется глухим до перезапуска даже после того, как
    игра закончилась и wanted снова совпал с current."""
    recognizer = ФальшивыйНедоступныйRecognizer("medium", available=False)

    wanted = _сторож(recognizer, игра=False, память=6000).tick()

    assert wanted == "medium"
    assert recognizer.смены == ["medium"]
    assert recognizer.available is True


def test_при_игре_видеопамять_не_опрашивается():
    """Находка 1 ревью: decide() при game_running=True вообще не смотрит на
    free_mb — первой же строкой возвращает gaming_model. Значит и звать
    nvidia-smi (self._free_mb) в этом случае незачем: это подпроцесс с
    таймаутом 5 секунд, отбирающий у игры и создание процесса, и опрос
    драйвера — ровно в тот момент, ради которого сторож вообще существует."""
    recognizer = ФальшивыйRecognizer()
    вызовы_памяти = []

    def память():
        вызовы_памяти.append(1)
        return 6000

    guard = Guard(
        recognizer,
        ФальшивыеНастройки(),
        game_running=lambda: True,
        free_mb=память,
    )

    guard.tick()

    assert вызовы_памяти == []


def test_без_игры_видеопамять_по_прежнему_опрашивается():
    """Симметричный к предыдущему: пропуск free_mb — это следствие того, что
    decide() не смотрит на память именно при game_running=True, а не общая
    экономия. Без игры признак по-прежнему нужен."""
    recognizer = ФальшивыйRecognizer("small")
    вызовы_памяти = []

    def память():
        вызовы_памяти.append(1)
        return 6000

    guard = Guard(
        recognizer,
        ФальшивыеНастройки(),
        game_running=lambda: False,
        free_mb=память,
    )

    guard.tick()

    assert вызовы_памяти == [1]


# --- Guard: enabled и жизненный цикл потока (start/stop) ---


class ФальшивыеНастройкиВыключено:
    whisper_model = "medium"
    whisper_model_gaming = ""
    gpu_guard_low_mb = 2500
    gpu_guard_high_mb = 4500
    whisper_device = "cuda"


class ФальшивыеНастройкиCPU:
    """whisper_model_gaming задан, но whisper_device — cpu. Панель
    (johnny/panel.py) даёт переключить device в один клик отдельно от
    whisper_model_gaming, и это ровно та комбинация, которую находка 5 ловит."""

    whisper_model = "medium"
    whisper_model_gaming = "small"
    gpu_guard_low_mb = 2500
    gpu_guard_high_mb = 4500
    whisper_device = "cpu"


def test_выключенный_сторож_не_дёргает_tick():
    """Находка 7 ревью: tick() публичный, и при пустой whisper_model_gaming
    decide() и так вернёт normal_model — но по дороге tick() успевает
    опросить оба признака и, если current не совпадает с normal_model,
    позвать use_model. Выключенный сторож не должен трогать вообще ничего."""
    recognizer = ФальшивыйRecognizer("small")  # current уже не normal_model
    опрошено = []

    guard = Guard(
        recognizer,
        ФальшивыеНастройкиВыключено(),
        game_running=lambda: (опрошено.append("game"), False)[1],
        free_mb=lambda: (опрошено.append("free"), 6000)[1],
    )

    wanted = guard.tick()

    assert wanted == "small"  # вернул текущую модель, не тронул
    assert recognizer.смены == []
    assert опрошено == []


def test_выключенный_сторож_не_запускает_поток():
    """Явное требование задачи: пустая whisper_model_gaming вообще не должна
    заводить фоновый поток опроса. Проверяем отсутствие потока, а не только
    то, что признаки не читались, — иначе поток мог бы тихо крутиться и
    ничего не делать, и это осталось бы незамеченным."""
    recognizer = ФальшивыйRecognizer()
    guard = Guard(
        recognizer,
        ФальшивыеНастройкиВыключено(),
        game_running=lambda: False,
        free_mb=lambda: 6000,
        poll_seconds=0.01,
    )

    поток_до = threading.active_count()
    guard.start()

    assert guard._thread is None
    # Число живых потоков в процессе не выросло — поток не просто не
    # сохранён в _thread, а действительно не создавался. Сравнение <=, а не
    # ==: во всём наборе тестов возможен чужой фоновый поток, который ещё не
    # успел завершиться, и это не имеет отношения к проверяемому поведению.
    assert threading.active_count() <= поток_до


def test_cpu_устройство_выключает_сторож_даже_с_игровой_моделью():
    """Находка 5 ревью: whisper_device выбирается отдельно от
    whisper_model_gaming. На процессоре подменять нечего — видеопамять там не
    расходуется, а перезагрузка модели на cpu — чистые секунды простоя и
    потеря точности без всякой экономии. И наоборот: свободной видеопамяти на
    cpu-машине честно мало, и запасной признак увёл бы на small вовсе без
    причины, если бы enabled её не отсекал."""
    recognizer = ФальшивыйRecognizer()
    опрошено = []

    guard = Guard(
        recognizer,
        ФальшивыеНастройкиCPU(),
        game_running=lambda: (опрошено.append("game"), True)[1],
        free_mb=lambda: (опрошено.append("free"), 100)[1],
    )

    assert guard.enabled is False

    wanted = guard.tick()

    assert wanted == recognizer.model_name  # ничего не тронул
    assert recognizer.смены == []
    assert опрошено == []


def test_cpu_устройство_не_запускает_поток():
    recognizer = ФальшивыйRecognizer()
    guard = Guard(
        recognizer,
        ФальшивыеНастройкиCPU(),
        game_running=lambda: True,
        free_mb=lambda: 100,
        poll_seconds=0.01,
    )

    guard.start()

    assert guard._thread is None


def test_повторный_start_не_заводит_второй_поток():
    recognizer = ФальшивыйRecognizer()
    guard = Guard(
        recognizer,
        ФальшивыеНастройки(),
        game_running=lambda: False,
        free_mb=lambda: 6000,
        poll_seconds=0.01,
    )

    guard.start()
    первый_поток = guard._thread
    guard.start()

    assert guard._thread is первый_поток
    guard.stop()
    _дождаться(lambda: not первый_поток.is_alive())


def test_stop_останавливает_цикл_опроса():
    recognizer = ФальшивыйRecognizer()
    счётчик = []

    def игра():
        счётчик.append(1)
        return False

    guard = Guard(
        recognizer,
        ФальшивыеНастройки(),
        game_running=игра,
        free_mb=lambda: 6000,
        poll_seconds=0.01,
    )

    guard.start()
    _дождаться(lambda: len(счётчик) >= 2)
    guard.stop()
    _дождаться(lambda: not guard._thread.is_alive())

    число_на_момент_остановки = len(счётчик)

    assert len(счётчик) == число_на_момент_остановки


def test_первый_опрос_происходит_сразу_а_не_через_poll_seconds():
    """Находка 9 ревью: игра, уже идущая в момент запуска Джони (автозапуск
    при входе в систему плюс ~3.5 минуты на загрузку моделей — человек
    успевает войти в игру раньше), должна быть замечена сразу, а не только
    через первый poll_seconds — иначе модель уже займёт видеопамять, которую
    в этот момент делит с игрой. poll_seconds здесь заведомо больше времени
    теста, чтобы отличить «опросили сразу» от «просто быстро опрашиваем»."""
    recognizer = ФальшивыйRecognizer()
    счётчик = []

    def игра():
        счётчик.append(1)
        return False

    guard = Guard(
        recognizer,
        ФальшивыеНастройки(),
        game_running=игра,
        free_mb=lambda: 6000,
        poll_seconds=100.0,
    )

    guard.start()
    _дождаться(lambda: len(счётчик) >= 1, тайм_аут=1.0)
    guard.stop()

    assert len(счётчик) >= 1


def test_сбой_первого_опроса_не_мешает_потоку_завестись():
    """Продолжение находки 9: опрос до цикла обёрнут в try/except именно
    затем, чтобы сбой признака на самом первом тике не помешал потоку начать
    штатный цикл — иначе один плохой опрос на старте выключал бы сторожа
    навсегда, никак об этом не сообщив."""
    recognizer = ФальшивыйRecognizer()
    счётчик = []

    def взрывной_первый_раз():
        счётчик.append(1)
        if len(счётчик) == 1:
            raise OSError("реестр недоступен")
        return False

    guard = Guard(
        recognizer,
        ФальшивыеНастройки(),
        game_running=взрывной_первый_раз,
        free_mb=lambda: 6000,
        poll_seconds=0.01,
    )

    guard.start()
    _дождаться(lambda: len(счётчик) >= 2)
    guard.stop()

    assert len(счётчик) >= 2


def test_stop_дожидается_текущего_тика_прежде_чем_вернуться():
    """Находка 6 ревью: join в stop() обязан закрывать окно, а не только
    сужать его. Поток, уже вошедший в tick() (например, в разгаре подмены
    модели), не должен пережить сам вызов stop() — иначе restart_app в трее
    мог бы запустить новый процесс Джони, пока старый сторож ещё доигрывает
    use_model на той же карте."""
    recognizer = ФальшивыйRecognizer()
    можно_продолжить = threading.Event()
    вошли_в_тик = threading.Event()

    def медленный_признак():
        вошли_в_тик.set()
        можно_продолжить.wait(2.0)
        return False

    guard = Guard(
        recognizer,
        ФальшивыеНастройки(),
        game_running=медленный_признак,
        free_mb=lambda: 6000,
        poll_seconds=0.01,
    )

    guard.start()
    assert вошли_в_тик.wait(2.0) is True

    stop_вернулся = threading.Event()

    def остановить():
        guard.stop()
        stop_вернулся.set()

    threading.Thread(target=остановить, daemon=True).start()

    # Тик всё ещё держит признак заблокированным — stop() обязан ждать его
    # завершения, а не вернуться немедленно.
    assert stop_вернулся.wait(0.3) is False, "stop() вернулась раньше конца тика — окно не закрыто"

    можно_продолжить.set()

    assert stop_вернулся.wait(2.0) is True
    assert guard._thread.is_alive() is False


def test_nvidia_smi_запускается_без_окна_консоли(monkeypatch):
    """Иначе раз в пять секунд поверх всего мигает чёрный прямоугольник.

    Проверяем именно флаг, а не «видно ли окно»: увидеть его в тесте нельзя,
    а забыть при следующей правке — легко. Опрос идёт в фоне постоянно, и
    мигает он в том числе поверх полноэкранной игры.
    """
    вызовы = []

    def запомнить(*args, **kwargs):
        вызовы.append(kwargs)
        return _результат(stdout="2048\n")

    monkeypatch.setattr("johnny.game_watch.subprocess.run", запомнить)
    free_vram_mb()
    assert вызовы[0].get("creationflags") == subprocess.CREATE_NO_WINDOW
