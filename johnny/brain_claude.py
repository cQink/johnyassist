import os
import shutil
import subprocess

from .http_client import warn_once

NAME = "claude"


def _find_claude() -> str:
    """Полный путь к claude или пустая строка, если его на машине нет.

    Под pythonw (трей) голое имя не резолвится в claude.cmd (CreateProcess не
    знает PATHEXT) и вызов молча падал — поэтому ищем реальный файл: which
    (учитывает PATHEXT), затем npm-папку.

    Раньше при неудаче возвращалось само имя «claude» — в надежде, что запуск
    как-нибудь разрешится. Не разрешался: CreateProcess PATHEXT не применяет,
    то есть эта надежда была строго слабее which, зато превращала «программы
    нет» в неотличимый от сетевого сбоя пустой ответ.
    """
    found = shutil.which("claude") or shutil.which("claude.cmd")
    if found:
        return found
    guess = os.path.join(os.environ.get("APPDATA", ""), "npm", "claude.cmd")
    return guess if os.path.isfile(guess) else ""


_CLAUDE = _find_claude()


def run(prompt: str) -> str:
    """Ответ claude -p. Пустая строка = недоступен, зовущий пробует следующего."""
    if not _CLAUDE:
        # Этот провайдер стоит в цепочке ПОСЛЕДНИМ и считается страховкой на
        # случай, когда всё остальное молчит. Если программы на машине нет,
        # страховки нет тоже — и узнать об этом надо из лога, а не по тому, что
        # Джони раз за разом отвечает «не понял». Замер 2026-08-08: на машине
        # владельца её действительно нет, цепочка была ['opus', 'claude'] —
        # то есть за Opus 5 не стояло ничего.
        warn_once(
            NAME,
            "Программа claude на этой машине не найдена — запасного мозга за "
            "сильной моделью нет (npm i -g @anthropic-ai/claude-code)",
        )
        return ""
    try:
        # Промпт передаём через stdin, а не argv: claude.cmd — batch-обёртка,
        # и многострочный текст с кавычками/скобками в аргументе калечится.
        result = subprocess.run(
            [_CLAUDE, "-p"],
            input=prompt,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=120,
        )
        return result.stdout
    except Exception as error:
        # claude недоступен/таймаут — без трейсбека в логе на каждую фразу, но
        # и не в полной тишине: пустая строка отсюда неотличима от «модель не
        # знает ответа».
        warn_once(NAME, f"claude -p не ответил ({error}) — запасного мозга нет")
        return ""
