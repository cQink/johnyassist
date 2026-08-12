"""Голосовые git-команды. Вся логика — в johnny/git_tools.py.

Здесь только склейка: достать рабочую папку из конфига и перевести
(ok, сообщение) в ActionResult. Границы («сам ничего не индексирую», «push
нет») живут в git_tools вместе с их обоснованием — здесь их дублировать
нельзя, разъедутся.
"""

from .. import git_tools
from .registry import ActionResult, registry


def _workdir(ctx: dict) -> str:
    """Папка репозитория из settings.yaml.

    config в ctx может не быть вовсе: execute зовут и из тестов, и из цепочек.
    Пустая строка тогда честнее исключения — git_tools скажет, чего не хватает.
    """
    config = ctx.get("config")
    return getattr(getattr(config, "settings", None), "git_workdir", "") or ""


def _result(pair) -> ActionResult:
    ok, message = pair
    return ActionResult(ok, message)


@registry.register("git_status")
def action_git_status(argument: str, ctx: dict) -> ActionResult:
    """«Джони, что с гитом» — ветка и сводка изменений."""
    return _result(git_tools.status(_workdir(ctx)))


@registry.register("git_branch")
def action_git_branch(argument: str, ctx: dict) -> ActionResult:
    """«Джони, на какой я ветке»."""
    return _result(git_tools.branch(_workdir(ctx)))


@registry.register("git_branches")
def action_git_branches(argument: str, ctx: dict) -> ActionResult:
    """«Джони, какие есть ветки»."""
    return _result(git_tools.branches(_workdir(ctx)))


@registry.register("git_changes")
def action_git_changes(argument: str, ctx: dict) -> ActionResult:
    """«Джони, что изменилось» — имена файлов, без содержимого."""
    return _result(git_tools.changes(_workdir(ctx)))


@registry.register("git_log")
def action_git_log(argument: str, ctx: dict) -> ActionResult:
    """«Джони, последние коммиты» — заголовки."""
    return _result(git_tools.log(_workdir(ctx)))


@registry.register("git_commit")
def action_git_commit(argument: str, ctx: dict) -> ActionResult:
    """«Джони, закоммить <сообщение>» — фиксирует УЖЕ проиндексированное.

    Ничего не добавляет в индекс: что войдёт в коммит, человек выбирает
    заранее руками (см. git_tools про `git add -A` голосом).
    """
    return _result(git_tools.commit(_workdir(ctx), argument))


@registry.register("git_new_branch")
def action_git_new_branch(argument: str, ctx: dict) -> ActionResult:
    """«Джони, создай ветку <имя>»."""
    return _result(git_tools.new_branch(_workdir(ctx), argument))
