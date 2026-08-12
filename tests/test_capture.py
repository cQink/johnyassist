"""Снимок экрана, окна и кадр с камеры.

Пакеты (PIL, cv2, win32gui) подменяются целиком: настоящий снимок в тесте
зависел бы от того, что открыто на машине, а кадр с камеры — от наличия самой
камеры. Проверяется здесь другое: что отказ приходит словами, а не исключением,
и что файл после себя не остаётся.
"""

import sys
import types

import pytest

import johnny.capture as capture


class FakeImage:
    def __init__(self, sink):
        self._sink = sink

    def save(self, path):
        self._sink.append(str(path))
        # Настоящий Pillow создаёт файл, и discard() должен его найти.
        with open(path, "wb") as handle:
            handle.write(b"png")


def _fake_pil(monkeypatch, saved, grab=None, boom=None):
    """Подменяет PIL.ImageGrab. grab получает kwargs вызова."""
    calls = []

    def fake_grab(**kwargs):
        calls.append(kwargs)
        if boom is not None:
            raise boom
        return FakeImage(saved)

    module = types.ModuleType("PIL")
    module.ImageGrab = types.SimpleNamespace(grab=fake_grab)
    monkeypatch.setitem(sys.modules, "PIL", module)
    return calls


@pytest.fixture(autouse=True)
def _temp_capture_dir(monkeypatch, tmp_path):
    """Снимки в tmp_path: тест не должен писать в рабочий models/."""
    monkeypatch.setattr(capture, "CAPTURE_DIR", tmp_path / "captures")


# ── экран ────────────────────────────────────────────────────────────────────

def test_screen_saves_a_file_and_returns_its_path(monkeypatch):
    saved = []
    _fake_pil(monkeypatch, saved)
    path, refusal = capture.screen()

    assert refusal == ""
    assert path in saved
    assert path.endswith(".png")


def test_screen_grabs_all_monitors(monkeypatch):
    """У владельца два монитора: снимок только главного молча терял бы половину
    рабочего стола, а человек сказал «что на экране» про тот, куда смотрит."""
    calls = _fake_pil(monkeypatch, [])
    capture.screen()
    assert calls[0]["all_screens"] is True


def test_screen_without_pillow_refuses_in_words(monkeypatch):
    monkeypatch.setitem(sys.modules, "PIL", None)
    path, refusal = capture.screen()
    assert path == ""
    assert "Pillow" in refusal


def test_screen_failure_is_a_refusal_not_an_exception(monkeypatch):
    """Снимок падает на заблокированном экране — это отказ, а не крах команды."""
    _fake_pil(monkeypatch, [], boom=OSError("screen locked"))
    path, refusal = capture.screen()
    assert path == ""
    assert "screen locked" in refusal


def test_two_screens_in_a_row_do_not_overwrite_each_other(monkeypatch):
    saved = []
    _fake_pil(monkeypatch, saved)
    first, _ = capture.screen()
    second, _ = capture.screen()
    assert first != second


# ── окно ─────────────────────────────────────────────────────────────────────

def _fake_win32(monkeypatch, handle=42, rect=(0, 0, 800, 600), iconic=False):
    module = types.ModuleType("win32gui")
    module.GetForegroundWindow = lambda: handle
    module.GetWindowRect = lambda h: rect
    module.IsIconic = lambda h: iconic
    monkeypatch.setitem(sys.modules, "win32gui", module)


def test_window_grabs_only_its_own_rectangle(monkeypatch):
    _fake_win32(monkeypatch, rect=(10, 20, 810, 620))
    calls = _fake_pil(monkeypatch, [])
    path, refusal = capture.window()

    assert refusal == ""
    assert path
    assert calls[0]["bbox"] == (10, 20, 810, 620)


def test_minimised_window_is_refused_explicitly(monkeypatch):
    """Свёрнутое окно спрашиваем у Windows, а не считаем по размеру.

    Живой прямоугольник свёрнутого окна — (-32000,-32000,-31840,-31972), то
    есть 160×28: размер правдоподобный, проверка «слишком маленькое» его
    пропускает, ImageGrab снимает пустоту, и OCR отвечает «текста нет». Это
    враньё: текст есть, просто окно свёрнуто.
    """
    _fake_win32(monkeypatch, rect=(-32000, -32000, -31840, -31972), iconic=True)
    _fake_pil(monkeypatch, [])
    path, refusal = capture.window()

    assert path == ""
    assert "свёрнуто" in refusal


def test_degenerate_rect_is_refused_too(monkeypatch):
    """Не свёрнуто, но снимать нечего — тоже отказ, а не пустая картинка."""
    _fake_win32(monkeypatch, rect=(100, 100, 100, 100))
    _fake_pil(monkeypatch, [])
    path, refusal = capture.window()

    assert path == ""
    assert refusal


def test_window_without_foreground_window_is_refused(monkeypatch):
    _fake_win32(monkeypatch, handle=0)
    _fake_pil(monkeypatch, [])
    path, refusal = capture.window()

    assert path == ""
    assert "активного окна" in refusal


def test_window_without_pywin32_refuses_in_words(monkeypatch):
    monkeypatch.setitem(sys.modules, "win32gui", None)
    path, refusal = capture.window()
    assert path == ""
    assert "pywin32" in refusal


# ── камера ───────────────────────────────────────────────────────────────────

class FakeDevice:
    def __init__(self, opened=True, frames=None):
        self._opened = opened
        self._frames = frames
        self.released = False
        self.reads = 0

    def isOpened(self):
        return self._opened

    def read(self):
        self.reads += 1
        if self._frames is None:
            return True, b"frame"
        return self._frames.pop(0) if self._frames else (False, None)

    def release(self):
        self.released = True


def _fake_cv2(monkeypatch, device, encode_ok=True):
    module = types.ModuleType("cv2")
    module.VideoCapture = lambda index: device
    module.imencode = lambda ext, frame: (encode_ok, types.SimpleNamespace(tobytes=lambda: b"png"))
    monkeypatch.setitem(sys.modules, "cv2", module)
    return module


def test_camera_saves_a_frame(monkeypatch):
    device = FakeDevice()
    _fake_cv2(monkeypatch, device)
    path, refusal = capture.camera()

    assert refusal == ""
    assert path.endswith(".png")


def test_camera_warms_up_before_keeping_a_frame(monkeypatch):
    """Первый кадр почти всегда чёрный: матрице нужно время на экспозицию.
    Отдать его в Azure значило бы платить за распознавание темноты."""
    device = FakeDevice()
    _fake_cv2(monkeypatch, device)
    capture.camera()
    assert device.reads == capture.CAMERA_WARMUP_FRAMES


def test_busy_camera_is_refused_in_words(monkeypatch):
    device = FakeDevice(opened=False)
    _fake_cv2(monkeypatch, device)
    path, refusal = capture.camera()

    assert path == ""
    assert "занята" in refusal


def test_camera_is_always_released(monkeypatch):
    """Незакрытая камера — горящий индикатор и «устройство используется» в Zoom.
    Отпускать её надо и после отказа, иначе одна неудача занимает камеру
    до перезапуска Джони."""
    device = FakeDevice(frames=[(False, None)])
    _fake_cv2(monkeypatch, device)
    capture.camera()
    assert device.released is True


def test_camera_writes_bytes_itself_not_via_imwrite(monkeypatch):
    """cv2.imwrite не понимает не-ASCII в пути, а проект лежит в «johny assist»
    и у людей бывает кириллица в имени пользователя. Путь обрабатывает Python."""
    device = FakeDevice()
    module = _fake_cv2(monkeypatch, device)
    assert not hasattr(module, "imwrite")     # его и не зовём
    path, _ = capture.camera()
    from pathlib import Path

    assert Path(path).read_bytes() == b"png"


def test_camera_without_opencv_refuses_in_words(monkeypatch):
    monkeypatch.setitem(sys.modules, "cv2", None)
    path, refusal = capture.camera()
    assert path == ""
    assert "opencv" in refusal


# ── выделенная область ───────────────────────────────────────────────────────

class FakeCrop:
    """Обрезанная картинка. Помнит, какие координаты ей дали."""

    def __init__(self, box, sink):
        self.box = box
        self._sink = sink

    def save(self, path):
        self._sink.append(str(path))
        with open(path, "wb") as handle:
            handle.write(b"png")


class FakeFull:
    def __init__(self, crops, sink):
        self._crops = crops
        self._sink = sink

    def crop(self, box):
        crop = FakeCrop(box, self._sink)
        self._crops.append(crop)
        return crop


def _fake_region(monkeypatch, saved, box=(10, 20, 110, 220), why="", origin=(0, 0)):
    """Подменяет PIL целиком (ImageGrab + Image) и ответ выделения.

    Возвращает (crops, order): список обрезок и порядок событий — по нему
    видно, что экран снят ДО показа оверлея, а не после.
    """
    crops = []
    order = []
    grabs = _fake_pil(monkeypatch, saved)
    module = sys.modules["PIL"]

    # Снимок экрана тоже попадает в порядок событий: иначе «сняли до оверлея»
    # проверялось бы косвенно, а именно этот порядок здесь и важен.
    plain_grab = module.ImageGrab.grab

    def watched_grab(**kwargs):
        order.append(("grab", kwargs.get("all_screens")))
        return plain_grab(**kwargs)

    module.ImageGrab = types.SimpleNamespace(grab=watched_grab)

    def fake_open(path):
        order.append(("open", str(path)))
        return FakeFull(crops, saved)

    module.Image = types.SimpleNamespace(open=fake_open)

    def fake_ask(timeout_seconds):
        order.append(("ask", timeout_seconds))
        return (box, why) if box is not None else (None, why)

    monkeypatch.setattr(capture, "_ask_region", fake_ask)
    monkeypatch.setattr(capture, "_virtual_origin", lambda: origin)
    return crops, order, grabs


def test_region_saves_the_crop_not_the_whole_screen(monkeypatch):
    saved = []
    crops, _, _ = _fake_region(monkeypatch, saved)
    path, refusal = capture.region()

    assert refusal == ""
    assert "region" in path
    assert [crop.box for crop in crops] == [(10, 20, 110, 220)]


def test_screen_is_grabbed_before_the_overlay_appears(monkeypatch):
    """Порядок здесь единственно возможный: полупрозрачная плёнка оверлея,
    показанная раньше снимка, попала бы в кадр, и OCR читал бы текст сквозь неё."""
    saved = []
    _, order, grabs = _fake_region(monkeypatch, saved)
    capture.region()

    assert grabs, "экран не сняли вовсе"
    assert [event for event, _ in order] == ["grab", "ask", "open"]
    # Файл полного экрана существовал до того, как спросили координаты.
    assert saved[0].endswith(".png") and "screen" in saved[0]


def test_region_shifts_the_crop_by_the_virtual_origin(monkeypatch):
    """Второй монитор слева от главного: экранные координаты уходят в минус, а
    картинка начинается от угла общей области. Без сдвига обрезка уедет."""
    saved = []
    crops, _, _ = _fake_region(
        monkeypatch, saved, box=(-1900, 20, -1800, 220), origin=(-1920, 0)
    )
    capture.region()

    assert [crop.box for crop in crops] == [(20, 20, 120, 220)]


def test_region_deletes_the_intermediate_full_screenshot(monkeypatch):
    """Человек выделил кусок именно потому, что остальное отправлять не хотел."""
    from pathlib import Path

    saved = []
    _fake_region(monkeypatch, saved)
    path, _ = capture.region()

    full = [name for name in saved if "screen" in name]
    assert full and not Path(full[0]).exists()
    assert Path(path).exists()


def test_cancelled_selection_is_a_refusal_in_words(monkeypatch):
    saved = []
    crops, _, _ = _fake_region(monkeypatch, saved, box=None, why="выделение отменили")
    path, refusal = capture.region()

    assert path == ""
    assert refusal == "выделение отменили"
    assert crops == []


def test_cancelled_selection_leaves_no_screenshot_behind(monkeypatch):
    """Отказ не повод оставить на диске снимок всего рабочего стола."""
    from pathlib import Path

    saved = []
    _fake_region(monkeypatch, saved, box=None, why="выделение отменили")
    capture.region()

    assert saved and not any(Path(name).exists() for name in saved)


def test_region_gives_up_when_the_screenshot_failed(monkeypatch):
    """Отказ экрана передаём как есть: своей формулировки у области тут нет."""
    saved = []
    _, order, _ = _fake_region(monkeypatch, saved)
    monkeypatch.setattr(capture, "screen", lambda: ("", "не удалось снять экран (боль)"))
    path, refusal = capture.region()

    assert path == ""
    assert refusal == "не удалось снять экран (боль)"
    assert order == [], "оверлей показали, хотя снимать нечего"


def test_region_without_pillow_refuses_in_words(monkeypatch):
    monkeypatch.setitem(sys.modules, "PIL", None)
    path, refusal = capture.region()

    assert path == ""
    assert refusal == "нет пакета Pillow"


def test_region_failure_is_a_refusal_not_an_exception(monkeypatch):
    saved = []
    _fake_region(monkeypatch, saved)
    sys.modules["PIL"].Image = types.SimpleNamespace(
        open=lambda _path: (_ for _ in ()).throw(OSError("файл битый"))
    )
    path, refusal = capture.region()

    assert path == ""
    assert "не удалось снять область" in refusal


def test_region_passes_its_timeout_to_the_selection(monkeypatch):
    """Оверлей висит поверх всех окон: брошенный, он выглядит как зависший
    экран, поэтому предел по времени обязан доезжать до процесса."""
    saved = []
    _, order, _ = _fake_region(monkeypatch, saved)
    capture.region(timeout_seconds=3.0)

    assert ("ask", 3.0) in order


# ── выделение как отдельный процесс ──────────────────────────────────────────

class FakeDone:
    def __init__(self, returncode=0, stdout=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = ""


def _fake_run(monkeypatch, result=None, boom=None):
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        if boom is not None:
            raise boom
        return result

    monkeypatch.setattr(capture.subprocess, "run", fake_run)
    return calls


def test_selection_runs_in_a_separate_process(monkeypatch):
    """Не Toplevel внутри Джони, а свой процесс: панель держит свой Tk-цикл в
    своём потоке, а тулкиты выполняются в фоновом — Tk из чужого потока вешает
    панель молча, без исключения."""
    calls = _fake_run(monkeypatch, FakeDone(stdout="1 2 3 4\n"))
    box, why = capture._ask_region(5.0)

    assert box == (1, 2, 3, 4)
    assert why == ""
    command, kwargs = calls[0]
    assert command[0] == sys.executable
    assert command[1:] == ["-m", "johnny.region_select"]
    assert kwargs["timeout"] == 5.0


def test_abandoned_overlay_times_out(monkeypatch):
    import subprocess as real_subprocess

    _fake_run(monkeypatch, boom=real_subprocess.TimeoutExpired("cmd", 60))
    box, why = capture._ask_region(60.0)

    assert box is None
    assert why == "область так и не выбрали"


def test_escape_comes_back_as_a_cancel(monkeypatch):
    _fake_run(monkeypatch, FakeDone(returncode=1))
    box, why = capture._ask_region(5.0)

    assert box is None
    assert why == "выделение отменили"


@pytest.mark.parametrize("stdout", ["", "1 2 3", "1 2 3 4 5", "a b c d"])
def test_unexpected_output_is_a_refusal_not_a_crash(monkeypatch, stdout):
    """Процесс мог упасть с чем угодно в stdout: разбор обязан отказать словами."""
    _fake_run(monkeypatch, FakeDone(stdout=stdout))
    box, why = capture._ask_region(5.0)

    assert box is None
    assert why == "выделение не получилось"


def test_unstartable_process_is_a_refusal(monkeypatch):
    _fake_run(monkeypatch, boom=OSError("нет python"))
    box, why = capture._ask_region(5.0)

    assert box is None
    assert "не удалось показать выделение" in why


def test_region_select_module_is_valid_python():
    """Синтаксическая ошибка здесь притворилась бы отменой: процесс упал бы с
    кодом 1, а Джони сказал бы «выделение отменили» — то есть соврал."""
    import ast
    from pathlib import Path

    source = Path(capture.__file__).with_name("region_select.py")
    tree = ast.parse(source.read_text(encoding="utf-8"))
    names = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
    assert "select" in names


def test_virtual_origin_falls_back_to_zero(monkeypatch):
    """Без pywin32 сдвига нет, но и отказа быть не должно: на одном мониторе
    угол виртуального стола и есть (0, 0)."""
    monkeypatch.setitem(sys.modules, "win32api", None)
    monkeypatch.setitem(sys.modules, "win32con", None)

    assert capture._virtual_origin() == (0, 0)


# ── удаление ─────────────────────────────────────────────────────────────────

def test_discard_removes_the_file(monkeypatch):
    saved = []
    _fake_pil(monkeypatch, saved)
    path, _ = capture.screen()
    from pathlib import Path

    assert Path(path).exists()
    capture.discard(path)
    assert not Path(path).exists()


def test_discard_of_a_missing_file_is_silent():
    """Зовётся в finally: исключение отсюда затёрло бы настоящий ответ."""
    capture.discard("нет-такого-файла.png")


def test_discard_of_empty_path_is_silent():
    capture.discard("")
