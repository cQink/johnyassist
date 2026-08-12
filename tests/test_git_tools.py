"""Git-хелперы: что Джони говорит про репозиторий и чего он НЕ делает.

Проверяем поведение и границы, а не формулировки слово в слово: тексты —
дело вкуса и будут меняться, а «коммитим только проиндексированное» и «push
не существует» меняться не должны.

Настоящий git здесь не запускается: раннер подменяется. Иначе тесты зависели
бы от того, какой репозиторий лежит на машине, и падали бы у каждого второго.
"""

import pytest

import johnny.git_tools as git_tools


class FakeRunner:
    """Подставной git: отдаёт заготовленный ответ и помнит, что у него просили."""

    def __init__(self, replies=None, error=None):
        self.replies = replies or {}
        self.error = error
        self.calls = []

    def __call__(self, args, workdir, timeout=10.0):
        self.calls.append(list(args))
        if self.error is not None:
            raise self.error
        for key, reply in self.replies.items():
            if key in args:
                return reply
        return ""


CLEAN = "## main...origin/main"
DIRTY = "\n".join([
    "## main...origin/main [ahead 1]",
    " M johnny/panel.py",
    "M  johnny/config.py",
    "?? notes.txt",
])


# --- числительные -----------------------------------------------------------


@pytest.mark.parametrize("count,expected", [
    (1, "файл"), (2, "файла"), (3, "файла"), (4, "файла"),
    (5, "файлов"), (11, "файлов"), (12, "файлов"), (14, "файлов"),
    (21, "файл"), (22, "файла"), (25, "файлов"), (101, "файл"),
])
def test_plural_matches_russian_rules(count, expected):
    """«3 файл» и «5 файла» на слух режут сильнее, чем кажется на бумаге."""
    assert git_tools.plural(count, "файл", "файла", "файлов") == expected


# --- рабочая папка ----------------------------------------------------------


def test_without_workdir_says_so_and_does_not_run_git():
    """Пустой git_workdir — это «не настроено», а не «сломалось».

    Главное здесь второе утверждение: git не должен запускаться в СЛУЧАЙНОЙ
    папке (той, из которой стартовал Джони) — там может оказаться чужой
    репозиторий, и человек услышит статус не своего проекта.
    """
    runner = FakeRunner()
    ok, message = git_tools.status("", runner=runner)
    assert ok is False
    assert runner.calls == []
    assert "git_workdir" in message


def test_missing_git_is_reported_as_missing_not_as_crash():
    runner = FakeRunner(error=FileNotFoundError())
    ok, message = git_tools.status("/repo", runner=runner)
    assert ok is False
    assert "git" in message.lower()


def test_not_a_repository_is_explained():
    runner = FakeRunner(error=git_tools.GitError("not a git repository"))
    ok, message = git_tools.status("/repo", runner=runner)
    assert ok is False
    assert "репозитор" in message.lower()


# --- статус -----------------------------------------------------------------


def test_clean_repo_reports_branch_and_cleanliness():
    ok, message = git_tools.status("/repo", runner=FakeRunner({"status": CLEAN}))
    assert ok is True
    assert "main" in message


def test_dirty_repo_counts_staged_unstaged_and_untracked():
    ok, message = git_tools.status("/repo", runner=FakeRunner({"status": DIRTY}))
    assert ok is True
    # 1 в индексе (M ), 1 изменён без индекса ( M), 1 новый (??)
    assert "main" in message
    assert "1" in message


def test_status_asks_porcelain_so_parsing_does_not_depend_on_language():
    """Без --porcelain git отвечает на языке пользователя, и разбор развалится
    на первой же машине с русской локалью."""
    runner = FakeRunner({"status": CLEAN})
    git_tools.status("/repo", runner=runner)
    assert any("--porcelain" in arg for call in runner.calls for arg in call)


def test_fresh_repo_without_commits_does_not_crash():
    """`## No commits yet on main` — отдельная форма первой строки."""
    ok, message = git_tools.status(
        "/repo", runner=FakeRunner({"status": "## No commits yet on main"})
    )
    assert ok is True
    assert "main" in message


def test_detached_head_is_not_called_a_branch():
    ok, message = git_tools.status(
        "/repo", runner=FakeRunner({"status": "## HEAD (no branch)"})
    )
    assert ok is True


# --- ветки ------------------------------------------------------------------


def test_branch_returns_current_branch():
    runner = FakeRunner({"--show-current": "feature/voice\n"})
    ok, message = git_tools.branch("/repo", runner=runner)
    assert ok is True
    assert "feature/voice" in message


def test_branches_lists_all_of_them():
    runner = FakeRunner({"--format=%(refname:short)": "main\ndev\nfeature/voice\n"})
    ok, message = git_tools.branches("/repo", runner=runner)
    assert ok is True
    assert "main" in message and "dev" in message


# --- коммиты (чтение) -------------------------------------------------------


def test_log_on_a_repo_without_commits_says_so_in_russian():
    """Найдено живым прогоном на свежем `git init`: git отвечает НЕНУЛЕВЫМ
    кодом («does not have any commits yet»), и человек слышал бы английское
    `fatal: ...` вместо ответа. Пустой репозиторий — не сбой."""
    runner = FakeRunner(error=git_tools.GitError(
        "fatal: your current branch 'main' does not have any commits yet"
    ))
    ok, message = git_tools.log("/repo", runner=runner)
    assert ok is True
    assert "fatal" not in message.lower()


def test_log_reads_titles_without_hashes():
    """Хеш на слух бесполезен — вслух читаются заголовки."""
    runner = FakeRunner({"log": "почини панель\nдобавь тесты\n"})
    ok, message = git_tools.log("/repo", runner=runner)
    assert ok is True
    assert "почини панель" in message


# --- коммит -----------------------------------------------------------------


def test_commit_never_stages_anything_itself():
    """САМОЕ ВАЖНОЕ ПРАВИЛО МОДУЛЯ.

    `git add -A` голосом — это способ отправить в коммит то, чего человек не
    видел: свежий secrets.yaml, дамп, чужую переписку в логе. Что попадёт в
    коммит, решает человек заранее; Джони фиксирует уже выбранное.
    """
    runner = FakeRunner({"commit": "[main abc1234] сообщение\n"})
    git_tools.commit("/repo", "сообщение", runner=runner)
    for call in runner.calls:
        assert "add" not in call
        assert "-A" not in call
        assert "--all" not in call
        assert "-a" not in call


def test_commit_without_staged_changes_refuses_and_says_why():
    runner = FakeRunner({"--cached": ""})   # diff --cached пуст = нечего коммитить
    ok, message = git_tools.commit("/repo", "сообщение", runner=runner)
    assert ok is False
    assert "индекс" in message.lower()
    assert not any("commit" in call for call in runner.calls)


def test_commit_with_empty_message_refuses():
    runner = FakeRunner({"--cached": "johnny/panel.py"})
    ok, message = git_tools.commit("/repo", "   ", runner=runner)
    assert ok is False
    assert not any("commit" in call for call in runner.calls)


def test_commit_passes_message_as_argument_not_as_shell_string():
    """Кавычки и точки с запятой в надиктованном сообщении не должны
    превращаться в команды. Список аргументов — единственная защита."""
    runner = FakeRunner({"--cached": "file.py", "commit": "[main abc1234] ok\n"})
    git_tools.commit("/repo", 'правка"; rm -rf /', runner=runner)
    commit_call = [call for call in runner.calls if "commit" in call][0]
    assert 'правка"; rm -rf /' in commit_call


# --- чего в модуле нет ------------------------------------------------------


def test_there_is_no_push():
    """Push необратим и выносит код наружу — голосом такого не делаем.

    Сторож, а не забывчивость: если push однажды понадобится, он появится
    вместе с подтверждением, и этот тест снимут осознанно.
    """
    assert not hasattr(git_tools, "push")


def test_no_public_helper_runs_a_shell():
    """shell=True где угодно в модуле = дыра: сообщение коммита приходит
    из распознавания речи, то есть снаружи."""
    import inspect

    source = inspect.getsource(git_tools)
    assert "shell=True" not in source
