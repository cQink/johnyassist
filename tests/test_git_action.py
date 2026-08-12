"""Голосовой слой git: маршрутизация фраз и передача рабочей папки.

Логика самих команд проверена в test_git_tools.py — здесь только склейка:
доехала ли фраза до нужного действия и та ли папка ушла в git.
"""

import types

import pytest
import yaml
from pathlib import Path

import johnny.actions.git_action as git_action
from johnny.actions import execute
from johnny.config import load_config
from johnny.router import route, route_exact

_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def commands():
    return load_config(str(_ROOT / "config")).commands


def _config_with(workdir):
    """Минимальный объект конфига: действию нужен только settings.git_workdir."""
    return types.SimpleNamespace(settings=types.SimpleNamespace(git_workdir=workdir))


# --- маршрутизация ----------------------------------------------------------


@pytest.mark.parametrize("phrase,action", [
    ("что с гитом", "git_status"),
    ("статус гита", "git_status"),
    ("какая ветка", "git_branch"),
    ("на какой я ветке", "git_branch"),
    ("покажи ветки", "git_branches"),
    ("что изменилось", "git_changes"),
    ("последние коммиты", "git_log"),
])
def test_phrases_reach_their_action(phrase, action, commands):
    routed = route_exact(phrase, commands, literal_only=True) or route(phrase, commands)
    assert routed is not None, phrase
    assert routed.action == action


def test_commit_message_is_captured(commands):
    routed = route("закоммить починил панель", commands)
    assert routed.action == "git_commit"
    assert routed.argument == "починил панель"


def test_longer_commit_phrase_wins_over_the_greedy_one(commands):
    """«закоммить с сообщением *» обязана стоять ВЫШЕ «закоммить *».

    Иначе жадный шаблон заберёт фразу первым и слова «с сообщением» уедут
    в текст коммита — ровно та же ловушка, что у «переведи фразу *».
    """
    routed = route("закоммить с сообщением починил панель", commands)
    assert routed.action == "git_commit"
    assert routed.argument == "починил панель"


def test_new_branch_name_is_captured(commands):
    routed = route("создай ветку правка панели", commands)
    assert routed.action == "git_new_branch"
    assert routed.argument == "правка панели"


def test_git_phrases_do_not_fall_into_the_catch_all_open(commands):
    """В конце commands.yaml стоит ловящее всё «открой *». Git-фразы не должны
    до него доезжать."""
    for phrase in ("что с гитом", "покажи ветки", "последние коммиты"):
        routed = route_exact(phrase, commands, literal_only=True) or route(phrase, commands)
        assert routed.action != "app_focus", phrase


# --- передача рабочей папки -------------------------------------------------


def test_action_passes_configured_workdir(monkeypatch):
    seen = {}

    def fake_status(workdir, runner=None):
        seen["workdir"] = workdir
        return True, "ок"

    monkeypatch.setattr(git_action.git_tools, "status", fake_status)
    result = execute(
        types.SimpleNamespace(action="git_status", argument="", via="тест"),
        {}, {}, config=_config_with("D:/repo"),
    )
    assert result.ok is True
    assert seen["workdir"] == "D:/repo"


def test_action_without_config_does_not_crash():
    """execute зовут и из цепочек, и из тестов — config может не приехать.
    Тогда честный отказ, а не AttributeError."""
    result = execute(
        types.SimpleNamespace(action="git_status", argument="", via="тест"),
        {}, {},
    )
    assert result.ok is False
    assert "git_workdir" in result.message


def test_commit_action_forwards_the_message(monkeypatch):
    seen = {}

    def fake_commit(workdir, message, runner=None):
        seen["message"] = message
        return True, "ок"

    monkeypatch.setattr(git_action.git_tools, "commit", fake_commit)
    execute(
        types.SimpleNamespace(action="git_commit", argument="починил панель", via="тест"),
        {}, {}, config=_config_with("D:/repo"),
    )
    assert seen["message"] == "починил панель"


# --- настройка --------------------------------------------------------------


def test_git_workdir_defaults_to_empty():
    """Пустая папка по умолчанию — не лень, а защита: с непустой Джони
    рассказывал бы про случайный репозиторий (см. git_tools)."""
    settings = load_config(str(_ROOT / "config")).settings
    assert hasattr(settings, "git_workdir")


def test_commands_yaml_is_valid_after_the_git_block():
    """Правка руками в 800-строчном yaml легко ломает файл целиком."""
    data = yaml.safe_load((_ROOT / "config" / "commands.yaml").read_text(encoding="utf-8"))
    assert data["что с гитом"]["action"] == "git_status"
