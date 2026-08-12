"""Git голосом: что происходит в репозитории и как зафиксировать сделанное.

Отвечает на вопросы, которые в работе задаёшь чаще всего и на которые дольше
всего лезть руками: «на какой я ветке», «что я наменял», «какие есть ветки»,
«последние коммиты». Плюс сам коммит.

ГРАНИЦЫ, И ОНИ ЗДЕСЬ ГЛАВНОЕ:

1. **Джони ничего не индексирует сам.** `git add -A` голосом — это способ
   отправить в коммит то, чего человек не видел: свежий secrets.yaml, дамп
   базы, случайно сохранённый лог с чужой перепиской. Что войдёт в коммит,
   решает человек заранее руками; Джони фиксирует уже выбранное. Если индекс
   пуст — он так и говорит, а не «помогает».

2. **Push не реализован вовсе.** Он необратим и выносит код наружу; голосовая
   команда, сработавшая от ослышки, тут стоит слишком дорого. Сторож —
   `tests/test_git_tools.py::test_there_is_no_push`.

3. **Никакого shell.** Сообщение коммита приходит из распознавания речи, то
   есть снаружи; аргументы уходят списком, и кавычки в надиктованной фразе
   остаются кавычками, а не превращаются в команду.

4. **Рабочая папка задаётся явно** (`git_workdir` в settings.yaml). Без неё
   Джони не запускает git вообще — не в той папке он рассказал бы про чужой
   репозиторий, и человек бы этого не заметил.

Разбор вывода идёт по `--porcelain`: без него git отвечает на языке системы,
и на русской локали разбор развалился бы.
"""

import subprocess

# Сколько ждём git. Локальные операции укладываются в доли секунды; всё, что
# висит дольше, — это либо огромный репозиторий, либо git ждёт ввода (пароль,
# редактор сообщения). Ждать голосом такое нельзя.
_TIMEOUT = 10.0

# Сколько коммитов зачитываем по умолчанию. Больше пяти на слух не
# запоминается, а читать их Джони будет вслух.
_LOG_COUNT = 5


class GitError(Exception):
    """git отработал, но вернул ошибку (не репозиторий, конфликт и т.п.)."""


def plural(count: int, one: str, few: str, many: str) -> str:
    """Русское числительное: 1 файл, 2 файла, 5 файлов.

    Нужно именно здесь, а не «потом причешем»: ответы Джони звучат ГОЛОСОМ,
    и «3 файл» на слух спотыкает сильнее, чем в тексте на экране.
    morph.py не подходит — он про сопоставление имён, а не про склонения.
    """
    tail_100 = abs(count) % 100
    tail_10 = abs(count) % 10
    if 11 <= tail_100 <= 14:
        return many
    if tail_10 == 1:
        return one
    if 2 <= tail_10 <= 4:
        return few
    return many


def _run(args: list[str], workdir: str, timeout: float = _TIMEOUT) -> str:
    """Запустить git и вернуть stdout. Бросает GitError на ненулевой код.

    Без shell и без склейки строк — см. границу 3 в докстринге модуля.
    """
    result = subprocess.run(
        ["git", *args],
        cwd=workdir,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    if result.returncode != 0:
        raise GitError((result.stderr or result.stdout).strip())
    return result.stdout


def _guarded(workdir: str, runner, args: list[str], soft: tuple = ()):
    """Общая обвязка: проверить папку, позвать git, перевести сбои в речь.

    Возвращает (ok, текст_или_вывод). Все публичные функции ходят через неё,
    чтобы «не задан workdir», «нет git» и «не репозиторий» звучали одинаково
    в каждой команде, а не по-разному в семи местах.

    soft — куски сообщений git, которые означают не сбой, а пустой результат
    («коммитов ещё нет»). Для них возвращается (True, ""), и вызывающий сам
    решает, как назвать пустоту по-русски. Без этого git отвечает ненулевым
    кодом, и человек слышал бы английское `fatal: ...` вместо ответа.
    """
    if not (workdir or "").strip():
        return False, ("Не задана рабочая папка: укажи git_workdir "
                       "в config/settings.yaml")
    try:
        return True, runner(args, workdir)
    except FileNotFoundError:
        # git не установлен либо не в PATH.
        return False, "Не нашёл git на этом компьютере"
    except subprocess.TimeoutExpired:
        return False, "git не ответил вовремя"
    except GitError as exc:
        text = str(exc).lower()
        if any(marker in text for marker in soft):
            return True, ""
        if "not a git repository" in text or "не является репозиторием" in text:
            return False, "В этой папке нет git-репозитория"
        return False, f"git отказался: {exc}"
    except OSError as exc:
        # Папки нет, нет прав и т.п. — cwd проверяет уже сама ОС.
        return False, f"Не смог зайти в рабочую папку: {exc}"


def _branch_name(head_line: str) -> str:
    """Имя ветки из первой строки `status --porcelain -b`.

    Форм несколько, и все встречаются вживую:
        ## main...origin/main [ahead 1]
        ## main
        ## No commits yet on main      (свежий репозиторий)
        ## HEAD (no branch)            (detached HEAD)
    """
    line = head_line[2:].strip() if head_line.startswith("##") else head_line.strip()
    if line.startswith("No commits yet on "):
        return line[len("No commits yet on "):].strip()
    if line.startswith("HEAD "):
        return ""
    return line.split("...")[0].split(" ")[0].strip()


def status(workdir: str, runner=_run):
    """«Что с гитом» — ветка и сводка изменений одной фразой."""
    ok, out = _guarded(workdir, runner, ["status", "--porcelain=v1", "-b"])
    if not ok:
        return False, out

    lines = [line for line in out.splitlines() if line.strip()]
    head = lines[0] if lines else ""
    name = _branch_name(head)
    where = f"На ветке {name}" if name else "Голова откреплена от ветки"

    staged = unstaged = untracked = 0
    for line in lines[1:]:
        code = line[:2]
        if code == "??":
            untracked += 1
            continue
        if code[0] not in " ?":
            staged += 1
        if len(code) > 1 and code[1] not in " ?":
            unstaged += 1

    if not (staged or unstaged or untracked):
        return True, f"{where}. Изменений нет"

    parts = []
    if staged:
        parts.append(f"{staged} в индексе")
    if unstaged:
        parts.append(f"{unstaged} {plural(unstaged, 'изменённый', 'изменённых', 'изменённых')}")
    if untracked:
        parts.append(f"{untracked} {plural(untracked, 'новый', 'новых', 'новых')}")
    total = staged + unstaged + untracked
    noun = plural(total, "файл", "файла", "файлов")
    return True, f"{where}. {', '.join(parts)} — всего {total} {noun}"


def branch(workdir: str, runner=_run):
    """«На какой я ветке»."""
    ok, out = _guarded(workdir, runner, ["branch", "--show-current"])
    if not ok:
        return False, out
    name = out.strip()
    if not name:
        return True, "Сейчас не на ветке: голова откреплена"
    return True, f"Ветка {name}"


def branches(workdir: str, runner=_run):
    """«Какие есть ветки» — только локальные: удалённые в голосовой ответ не
    влезают, а спрашивают обычно про свои."""
    ok, out = _guarded(
        workdir, runner, ["branch", "--format=%(refname:short)"]
    )
    if not ok:
        return False, out
    names = [line.strip() for line in out.splitlines() if line.strip()]
    if not names:
        return True, "Веток нет"
    count = len(names)
    noun = plural(count, "ветка", "ветки", "веток")
    return True, f"{count} {noun}: {', '.join(names)}"


def changes(workdir: str, runner=_run):
    """«Что изменилось» — по файлам, без содержимого.

    Именно имена файлов, а не diff: читать вслух построчный diff бессмысленно,
    а «что я вообще трогал» — самый частый вопрос.
    """
    ok, out = _guarded(workdir, runner, ["status", "--porcelain=v1"])
    if not ok:
        return False, out
    names = [line[3:].strip() for line in out.splitlines() if line.strip()]
    if not names:
        return True, "Ничего не изменено"
    shown = names[:8]
    tail = "" if len(names) <= 8 else f" и ещё {len(names) - 8}"
    return True, f"Изменено: {', '.join(shown)}{tail}"


def log(workdir: str, count: int = _LOG_COUNT, runner=_run):
    """«Последние коммиты» — заголовки, без хешей: хеш на слух бесполезен."""
    # Свежий репозиторий без коммитов — не ошибка, но git отвечает ненулевым
    # кодом («does not have any commits yet»). Найдено живым прогоном: без
    # этого Джони зачитывал английское `fatal: ...` вместо «коммитов пока нет».
    ok, out = _guarded(
        workdir, runner, ["log", f"-{count}", "--format=%s"],
        soft=("does not have any commits", "не имеет коммитов"),
    )
    if not ok:
        return False, out
    titles = [line.strip() for line in out.splitlines() if line.strip()]
    if not titles:
        return True, "Коммитов пока нет"
    return True, "Последние коммиты: " + "; ".join(titles)


def commit(workdir: str, message: str, runner=_run):
    """Зафиксировать УЖЕ ПРОИНДЕКСИРОВАННОЕ. Ничего не добавляет в индекс сам.

    Порядок проверок важен: сначала пустое сообщение (это опечатка человека),
    потом пустой индекс (это состояние репозитория). Иначе на пустом индексе
    и пустом сообщении Джони жаловался бы не на то, что ближе к делу.
    """
    text = (message or "").strip()
    if not text:
        return False, "Не расслышал сообщение коммита"

    ok, staged = _guarded(workdir, runner, ["diff", "--cached", "--name-only"])
    if not ok:
        return False, staged
    if not staged.strip():
        return False, ("В индексе пусто — сначала выбери, что войдёт в коммит. "
                       "Сам я ничего не добавляю")

    ok, out = _guarded(workdir, runner, ["commit", "-m", text])
    if not ok:
        return False, out
    count = len([line for line in staged.splitlines() if line.strip()])
    noun = plural(count, "файл", "файла", "файлов")
    return True, f"Закоммитил {count} {noun}"


def new_branch(workdir: str, name: str, runner=_run):
    """Создать ветку и перейти на неё.

    Имя чистим от пробелов: надиктованное «новая ветка правка панели» иначе
    ушло бы в git как три аргумента.
    """
    clean = "-".join((name or "").split())
    if not clean:
        return False, "Не расслышал имя ветки"
    ok, out = _guarded(workdir, runner, ["checkout", "-b", clean])
    if not ok:
        return False, out
    return True, f"Создал ветку {clean} и перешёл на неё"
