"""Решение «какую модель Whisper держать» — без видеокарты и без Steam."""
import subprocess
import winreg

from johnny.game_watch import decide, free_vram_mb, steam_game_running

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

from johnny.game_watch import Guard


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


def _сторож(recognizer, *, игра, память):
    return Guard(
        recognizer,
        ФальшивыеНастройки(),
        game_running=lambda: игра,
        free_mb=lambda: память,
    )


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
