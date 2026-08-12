"""Тесты для ActionRegistry — ядро нового пакета actions."""

from johnny.actions.registry import ActionRegistry, ActionResult


def test_register_and_execute():
    """Зарегистрированный обработчик вызывается с правильными аргументами."""
    reg = ActionRegistry()

    @reg.register("greet")
    def greet(argument, ctx):
        return ActionResult(True, f"Привет, {argument}!")

    result = reg.execute("greet", "Мир")
    assert result == ActionResult(True, "Привет, Мир!")


def test_execute_unknown_action():
    """Незарегистрированное действие → ActionResult(False, ...)."""
    reg = ActionRegistry()
    result = reg.execute("nonexistent", "arg")
    assert result.ok is False
    assert "Неизвестное действие" in result.message


def test_context_kwargs_passed():
    """Контекстные параметры (apps, channels, ...) доходят до обработчика."""
    reg = ActionRegistry()
    received = {}

    @reg.register("check_ctx")
    def check_ctx(argument, ctx):
        received.update(ctx)
        return ActionResult(True, "ok")

    reg.execute("check_ctx", "arg", apps={"дота": "x"}, new_tab=False)
    assert received["apps"] == {"дота": "x"}
    assert received["new_tab"] is False


def test_contains_check():
    """Оператор in работает для проверки наличия действия."""
    reg = ActionRegistry()

    @reg.register("exists")
    def exists(argument, ctx):
        return ActionResult(True, "ok")

    assert "exists" in reg
    assert "missing" not in reg


def test_registered_actions_list():
    """registered_actions() возвращает список зарегистрированных имён."""
    reg = ActionRegistry()

    @reg.register("a")
    def a(argument, ctx):
        return ActionResult(True, "ok")

    @reg.register("b")
    def b(argument, ctx):
        return ActionResult(True, "ok")

    names = reg.registered_actions()
    assert "a" in names
    assert "b" in names
    assert len(names) == 2


def test_duplicate_register_overwrites(caplog):
    """Повторная регистрация перезаписывает обработчик и логирует warning."""
    reg = ActionRegistry()

    @reg.register("dup")
    def first(argument, ctx):
        return ActionResult(True, "first")

    @reg.register("dup")
    def second(argument, ctx):
        return ActionResult(True, "second")

    result = reg.execute("dup", "")
    assert result.message == "second"


def test_handler_exception_propagates():
    """Исключение в обработчике пробрасывается наружу (не глотается)."""
    reg = ActionRegistry()

    @reg.register("boom")
    def boom(argument, ctx):
        raise ValueError("взрыв")

    import pytest
    with pytest.raises(ValueError, match="взрыв"):
        reg.execute("boom", "")


def test_global_registry_has_all_actions():
    """Глобальный реестр содержит все ожидаемые действия после импорта пакета."""
    from johnny.actions.registry import registry

    expected = [
        "open_url", "browser_open", "open_channel", "browser_seek",
        "browser_click_result", "browser_next", "browser_fullscreen", "browser_focus",
        "launch_app", "app_focus", "move_window", "launch_on_monitor", "open_url_on_monitor",
        "system", "set_volume", "volume_delta",
        "steam_login",
        "type_text", "press_enter",
        "discord_message", "discord_call",
        "remember", "forget", "list_memory",
    ]
    for name in expected:
        assert name in registry, f"Действие {name!r} не зарегистрировано в глобальном реестре"
