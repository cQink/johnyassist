"""Запасной провайдер: CLI `claude -p` подпроцессом.

Он стоит в цепочке ПОСЛЕДНИМ и играет роль страховки — «если молчат все, есть
ещё вот это». Поэтому здесь важнее обычного отличать два случая, которые
раньше выглядели одинаково (пустая строка):

  - программы на машине нет вовсе — страховки не существует, и человек должен
    узнать это из лога, а не по тому, что Джони раз за разом «не понял»;
  - программа есть, но сорвалась — обычный сбой, ответит следующий.

Замер 2026-08-08: на машине владельца claude не установлен, а цепочка была
['opus', 'claude'] — то есть за сильной моделью не стояло ничего.
"""

import subprocess

import johnny.brain_claude as brain_claude


def _quiet(monkeypatch):
    """Ловим предупреждения вместо записи в общий johnny.log.

    Настоящий warn_once помнит показанные ключи глобально, и первый же тест
    заглушил бы все следующие.
    """
    warnings = []
    monkeypatch.setattr(brain_claude, "warn_once", lambda key, message: warnings.append(message))
    return warnings


def test_missing_program_warns_and_does_not_spawn(monkeypatch):
    """Программы нет — говорим об этом и не тратим время на заведомый провал."""
    warnings = _quiet(monkeypatch)
    monkeypatch.setattr(brain_claude, "_CLAUDE", "")
    spawned = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: spawned.append(a))

    assert brain_claude.run("вопрос") == ""
    assert spawned == []
    assert len(warnings) == 1
    assert "claude" in warnings[0]


def test_answer_is_returned_and_prompt_goes_through_stdin(monkeypatch):
    """Промпт — в stdin, а не в argv: claude.cmd batch-обёртка, и многострочный
    текст с кавычками в аргументе калечится."""
    _quiet(monkeypatch)
    monkeypatch.setattr(brain_claude, "_CLAUDE", "C:/npm/claude.cmd")
    seen = {}

    class FakeResult:
        stdout = "Токио, сэр."

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        seen["input"] = kwargs.get("input")
        return FakeResult()

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert brain_claude.run("столица японии?") == "Токио, сэр."
    assert seen["argv"] == ["C:/npm/claude.cmd", "-p"]
    assert seen["input"] == "столица японии?"
    assert "столица японии?" not in seen["argv"]


def test_failure_returns_empty_string_and_warns(monkeypatch):
    """Сбой не роняет Джони, но и не проходит в полной тишине."""
    warnings = _quiet(monkeypatch)
    monkeypatch.setattr(brain_claude, "_CLAUDE", "C:/npm/claude.cmd")
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **kw: (_ for _ in ()).throw(OSError("нет такого файла"))
    )

    assert brain_claude.run("вопрос") == ""
    assert len(warnings) == 1
    assert "нет такого файла" in warnings[0]


def test_not_found_is_empty_not_the_bare_name(monkeypatch):
    """Пустая строка, а не «claude».

    Голое имя CreateProcess не резолвит (PATHEXT он не применяет), так что
    надежда «а вдруг запустится» была строго слабее which — зато превращала
    «программы нет» в неотличимый от сетевого сбоя пустой ответ.
    """
    monkeypatch.setattr(brain_claude.shutil, "which", lambda name: None)
    monkeypatch.setattr(brain_claude.os.path, "isfile", lambda path: False)
    assert brain_claude._find_claude() == ""


def test_which_result_wins(monkeypatch):
    """which учитывает PATHEXT — потому и он первым, а npm-папка догадкой."""
    monkeypatch.setattr(
        brain_claude.shutil, "which", lambda name: "C:/real/claude.cmd" if name == "claude" else None
    )
    assert brain_claude._find_claude() == "C:/real/claude.cmd"
