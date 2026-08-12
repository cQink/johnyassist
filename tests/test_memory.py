import itertools

import johnny.memory as memory
from johnny.memory import Turn


def _reset():
    memory._turns.clear()


def test_record_turn_appears_in_recent_context():
    _reset()
    memory.record_turn("привет", "привет, сэр")
    context = memory.recent_context()
    assert len(context) == 1
    assert context[0].user_text == "привет"
    assert context[0].reply == "привет, сэр"


def test_recent_context_keeps_only_last_five(monkeypatch):
    _reset()
    counter = itertools.count(0.0)
    monkeypatch.setattr(memory.time, "monotonic", lambda: next(counter))
    for i in range(6):
        memory.record_turn(f"вопрос{i}", f"ответ{i}")
    context = memory.recent_context(max_age=100.0)
    assert len(context) == 5
    assert context[0].user_text == "вопрос1"  # вопрос0 вытеснен (maxlen=5)
    assert context[-1].user_text == "вопрос5"


def test_recent_context_drops_turns_older_than_max_age(monkeypatch):
    _reset()
    times = iter([0.0, 700.0])  # обмен в момент 0, проверка в момент 700
    monkeypatch.setattr(memory.time, "monotonic", lambda: next(times))
    memory.record_turn("вопрос", "ответ")
    assert memory.recent_context(max_age=600.0) == []


def test_recent_context_keeps_turns_within_max_age(monkeypatch):
    _reset()
    times = iter([0.0, 300.0])
    monkeypatch.setattr(memory.time, "monotonic", lambda: next(times))
    memory.record_turn("вопрос", "ответ")
    assert len(memory.recent_context(max_age=600.0)) == 1


def test_empty_memory_has_no_context():
    _reset()
    assert memory.recent_context() == []


def test_remember_appends_fact(tmp_path, monkeypatch):
    f = tmp_path / "memory.yaml"
    monkeypatch.setattr(memory, "_MEMORY_FILE", f)
    memory.remember("любит кофе без сахара")
    assert memory.list_facts() == ["любит кофе без сахара"]


def test_remember_appends_to_existing_facts(tmp_path, monkeypatch):
    f = tmp_path / "memory.yaml"
    monkeypatch.setattr(memory, "_MEMORY_FILE", f)
    memory.remember("факт раз")
    memory.remember("факт два")
    assert memory.list_facts() == ["факт раз", "факт два"]


def test_list_facts_on_missing_file_is_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "_MEMORY_FILE", tmp_path / "missing.yaml")
    assert memory.list_facts() == []


def test_forget_removes_closest_matching_fact(tmp_path, monkeypatch):
    """Живой пример из спеки: короткий запрос против длинного факта.
    SequenceMatcher по целым строкам тут дал бы 0.45 — ниже разумного
    порога; доля слов запроса, найденных в факте, — 1.0."""
    f = tmp_path / "memory.yaml"
    monkeypatch.setattr(memory, "_MEMORY_FILE", f)
    memory.remember("у пользователя стим-аккаунт art_vol_teror")
    memory.remember("любит кофе без сахара")
    removed = memory.forget("стим-аккаунт")
    assert removed == "у пользователя стим-аккаунт art_vol_teror"
    assert memory.list_facts() == ["любит кофе без сахара"]


def test_forget_matches_by_partial_word_overlap(tmp_path, monkeypatch):
    f = tmp_path / "memory.yaml"
    monkeypatch.setattr(memory, "_MEMORY_FILE", f)
    memory.remember("у пользователя стим-аккаунт art_vol_teror")
    # 2 из 3 слов запроса совпадают (0.667 ≥ порога 0.5) — «про» в факте нет.
    removed = memory.forget("про стим аккаунт")
    assert removed == "у пользователя стим-аккаунт art_vol_teror"


def test_forget_returns_none_when_nothing_close_enough(tmp_path, monkeypatch):
    f = tmp_path / "memory.yaml"
    monkeypatch.setattr(memory, "_MEMORY_FILE", f)
    memory.remember("любит кофе без сахара")
    assert memory.forget("совершенно другая тема про космос") is None
    assert memory.list_facts() == ["любит кофе без сахара"]


def test_forget_on_empty_memory_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "_MEMORY_FILE", tmp_path / "missing.yaml")
    assert memory.forget("что угодно") is None


def test_build_prompt_block_empty_when_nothing_to_show():
    assert memory.build_prompt_block([], []) == ""


def test_build_prompt_block_includes_context_and_facts():
    context = [Turn("хотите анекдот", "да, вот анекдот про...", at=0.0)]
    block = memory.build_prompt_block(context, ["любит кофе без сахара"])
    assert "хотите анекдот" in block
    assert "любит кофе без сахара" in block


def test_build_prompt_block_context_only():
    context = [Turn("привет", "привет, сэр", at=0.0)]
    block = memory.build_prompt_block(context, [])
    assert "привет" in block
    assert "Известно о пользователе" not in block


def test_build_prompt_block_facts_only():
    block = memory.build_prompt_block([], ["любит кофе без сахара"])
    assert "любит кофе без сахара" in block
    assert "Недавний разговор" not in block


def test_load_facts_with_malformed_yaml_returns_empty_list(tmp_path, monkeypatch):
    """Битый memory.yaml не должен ронять весь LLM-путь (list_facts()
    вызывается на каждом ходу через build_prompt_block) — деградируем до
    пустого списка фактов вместо необработанного YAMLError."""
    f = tmp_path / "memory.yaml"
    f.write_text("not: valid: yaml: [[[", encoding="utf-8")
    monkeypatch.setattr(memory, "_MEMORY_FILE", f)
    assert memory.list_facts() == []


def test_load_facts_skips_entries_without_text_key(tmp_path, monkeypatch):
    """Синтаксически валидный, но неправильной формы YAML (например,
    руками поправленный не туда) не должен ронять остальные факты — только
    сам плохой элемент пропускается."""
    f = tmp_path / "memory.yaml"
    f.write_text(
        "- text: любит кофе без сахара\n"
        "  added: '2026-08-06 12:00:00'\n"
        "- added: '2026-08-06 12:01:00'\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(memory, "_MEMORY_FILE", f)
    assert memory.list_facts() == ["любит кофе без сахара"]


def test_load_facts_with_non_list_top_level_returns_empty(tmp_path, monkeypatch):
    f = tmp_path / "memory.yaml"
    f.write_text("just a string", encoding="utf-8")
    monkeypatch.setattr(memory, "_MEMORY_FILE", f)
    assert memory.list_facts() == []


def test_remember_does_not_overwrite_malformed_file(tmp_path, monkeypatch):
    """Регресс на находку ре-ревью: если _load_facts() деградировал до []
    из-за порчи файла (а не потому что он реально пуст), remember() не
    должен молча затирать файл содержимым [новый_факт] — это стирает
    всё, что было в битом файле безвозвратно."""
    f = tmp_path / "memory.yaml"
    broken_content = "not: valid: yaml: [[[\n"
    f.write_text(broken_content, encoding="utf-8")
    monkeypatch.setattr(memory, "_MEMORY_FILE", f)

    memory.remember("новый факт")

    broken_files = list(tmp_path.glob("memory.yaml.broken-*"))
    assert len(broken_files) == 1
    assert broken_files[0].read_text(encoding="utf-8") == broken_content
    assert memory.list_facts() == ["новый факт"]


def test_forget_on_malformed_file_returns_none_without_writing(tmp_path, monkeypatch):
    """forget() ничего не пишет, поэтому битый файл ему затирать нечем —
    ведёт себя как «ничего похожего не найдено» и оставляет файл как есть."""
    f = tmp_path / "memory.yaml"
    broken_content = "not: valid: yaml: [[[\n"
    f.write_text(broken_content, encoding="utf-8")
    monkeypatch.setattr(memory, "_MEMORY_FILE", f)

    result = memory.forget("что угодно")

    assert result is None
    assert list(tmp_path.glob("memory.yaml.broken-*")) == []
    assert f.read_text(encoding="utf-8") == broken_content


def test_load_facts_handles_os_error_gracefully(tmp_path, monkeypatch):
    """Временно заблокированный/недоступный файл не должен ронять весь
    LLM-путь (list_facts() дёргается на каждом ходу) — та же деградация,
    что и для битого YAML."""
    f = tmp_path / "memory.yaml"
    f.write_text("- text: что-то\n", encoding="utf-8")
    monkeypatch.setattr(memory, "_MEMORY_FILE", f)

    def raise_os_error(*args, **kwargs):
        raise OSError("simulated lock")

    monkeypatch.setattr(type(f), "read_text", raise_os_error)
    assert memory.list_facts() == []


def test_load_facts_handles_unicode_decode_error_gracefully(tmp_path, monkeypatch):
    """Файл в неправильной кодировке не должен ронять весь LLM-путь —
    та же деградация, что и для битого YAML."""
    f = tmp_path / "memory.yaml"
    f.write_text("- text: что-то\n", encoding="utf-8")
    monkeypatch.setattr(memory, "_MEMORY_FILE", f)

    def raise_unicode_error(*args, **kwargs):
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")

    monkeypatch.setattr(type(f), "read_text", raise_unicode_error)
    assert memory.list_facts() == []


# --- Персистентность разговора между запусками ---


def test_dialog_survives_restart(tmp_path, monkeypatch):
    """Главное, ради чего затевалась персистентность: «а он что ответил?»
    после перезапуска должно работать так же, как до него."""
    monkeypatch.setattr(memory, "_DIALOG_FILE", tmp_path / "dialog.yaml")
    memory._turns.clear()
    memory.record_turn("кто выиграл", "Гоша")

    memory._turns.clear()  # изображаем новый процесс: буфер в памяти пуст
    memory.load_dialog()

    context = memory.recent_context()
    assert len(context) == 1
    assert context[0].user_text == "кто выиграл"
    assert context[0].reply == "Гоша"


def test_load_dialog_drops_stale_turns(tmp_path, monkeypatch):
    """Вчерашний разговор — не контекст. Возраст считается по НАСТЕННОМУ
    времени: monotonic из прошлого процесса в новом не значит ничего."""
    monkeypatch.setattr(memory, "_DIALOG_FILE", tmp_path / "dialog.yaml")
    memory._turns.clear()
    memory.record_turn("давнее", "давний ответ")

    memory._turns.clear()
    # Часы вперёд на сутки — файл тот же, но обмен уже просрочен.
    real_time = memory.time.time()
    monkeypatch.setattr(memory.time, "time", lambda: real_time + 86400)
    memory.load_dialog()

    assert memory.recent_context() == []


def test_load_dialog_survives_broken_file(tmp_path, monkeypatch):
    """Битый dialog.yaml не должен ронять запуск: разговор — вещь
    необязательная, ассистент обязан подняться и без него."""
    broken = tmp_path / "dialog.yaml"
    broken.write_text("не: [ямл", encoding="utf-8")
    monkeypatch.setattr(memory, "_DIALOG_FILE", broken)
    memory._turns.clear()

    memory.load_dialog()  # не бросает

    assert memory.recent_context() == []


def test_missing_dialog_file_is_not_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "_DIALOG_FILE", tmp_path / "нет-такого.yaml")
    memory._turns.clear()
    memory.load_dialog()
    assert memory.recent_context() == []


# --- CRUD поверх фактов ---


def test_update_fact_replaces_text_and_returns_previous(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "_MEMORY_FILE", tmp_path / "memory.yaml")
    memory.remember("стим-аккаунт old_nick")

    previous = memory.update_fact("стим-аккаунт", "стим-аккаунт art_vol_teror")

    assert previous == "стим-аккаунт old_nick"
    assert memory.list_facts() == ["стим-аккаунт art_vol_teror"]


def test_update_fact_returns_none_when_nothing_matches(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "_MEMORY_FILE", tmp_path / "memory.yaml")
    memory.remember("любит кофе")

    assert memory.update_fact("совершенно другое", "новое") is None
    assert memory.list_facts() == ["любит кофе"]  # ничего не затёрли


def test_clear_facts_returns_count_and_empties(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "_MEMORY_FILE", tmp_path / "memory.yaml")
    memory.remember("первое")
    memory.remember("второе")

    assert memory.clear_facts() == 2
    assert memory.list_facts() == []


def test_clear_facts_on_empty_memory_is_zero(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "_MEMORY_FILE", tmp_path / "memory.yaml")
    assert memory.clear_facts() == 0


def test_clear_facts_does_not_wipe_unreadable_file(tmp_path, monkeypatch):
    """Тот же принцип, что и у forget(): непрочитанное не стираем — иначе
    один сбой чтения уносит всю память безвозвратно."""
    f = tmp_path / "memory.yaml"
    f.write_text("не: [ямл", encoding="utf-8")
    monkeypatch.setattr(memory, "_MEMORY_FILE", f)

    assert memory.clear_facts() == 0
    assert f.read_text(encoding="utf-8") == "не: [ямл"


def test_facts_detailed_carries_dates(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "_MEMORY_FILE", tmp_path / "memory.yaml")
    memory.remember("любит кофе")

    detailed = memory.facts_detailed()

    assert detailed[0]["text"] == "любит кофе"
    assert "added" in detailed[0]
    # list_facts остаётся строками: его результат уходит в промпт и в озвучку.
    assert memory.list_facts() == ["любит кофе"]


# --- Ограничение объёма блока для промпта ---


def test_prompt_block_drops_facts_beyond_char_budget():
    facts = [f"факт номер {i} " + "х" * 100 for i in range(20)]
    block = memory.build_prompt_block([], facts, max_facts_chars=300)

    assert block  # что-то влезло
    assert len(block) < 600  # но далеко не все двадцать
    # Отбираем с конца: свежие факты важнее давних.
    assert "факт номер 19" in block
    assert "факт номер 0 " not in block


def test_prompt_block_keeps_original_order():
    facts = ["первый", "второй", "третий"]
    block = memory.build_prompt_block([], facts, max_facts_chars=1000)
    assert block.index("первый") < block.index("второй") < block.index("третий")


def test_long_fact_does_not_cut_off_older_short_ones():
    """Перебираем весь список, а не обрываемся на первом непоместившемся:
    один длинный факт посреди коротких не должен отрезать всё старше него."""
    facts = ["короткий A", "х" * 500, "короткий B"]
    block = memory.build_prompt_block([], facts, max_facts_chars=100)
    assert "короткий A" in block
    assert "короткий B" in block


def test_prompt_block_empty_without_context_or_facts():
    assert memory.build_prompt_block([], []) == ""
