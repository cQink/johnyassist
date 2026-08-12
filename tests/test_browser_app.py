import johnny.browser as browser
from johnny import browser_app


def test_launch_command_carries_profile_and_port():
    line = browser_app.launch_command(r"C:\chrome.exe", r"D:\p", 9222)
    assert '--user-data-dir="D:\\p"' in line
    assert "--remote-debugging-port=9222" in line
    assert line.endswith('"%1"'), "Windows подставляет ссылку вместо %1"


def test_launch_command_uses_the_same_profile_as_browser():
    """Профиль и порт — из browser.py, а не переписаны строкой.

    Иначе при смене порта регистрация тихо разъедется с кодом, и ссылки
    начнут открывать браузер, к которому Джони не может подключиться.
    """
    line = browser_app.launch_command(
        browser._chrome_exe(), str(browser._PROFILE), browser._PORT
    )
    assert str(browser._PROFILE) in line
    assert str(browser._PORT) in line
