from johnny.recognizer import build_vocabulary


def test_vocabulary_includes_app_names_and_channel_aliases():
    apps = {"дота": "steam://x", "апекс": "steam://y"}
    channels = {"9impulse": {"twitch": "9impulse", "aliases": ["импульс", "кирчик"]}}
    vocab = build_vocabulary(apps, channels)
    for word in ("дота", "апекс", "импульс", "кирчик"):
        assert word in vocab


def test_vocabulary_is_unique():
    apps = {"дота": "steam://x"}
    channels = {"c": {"twitch": "c", "aliases": ["дота"]}}  # дубль «дота»
    assert build_vocabulary(apps, channels).split().count("дота") == 1


def test_vocabulary_empty_config():
    assert build_vocabulary({}, {}) == ""


from johnny.config import CommandRule
from johnny.recognizer import _MAX_VOCAB_WORDS


def test_vocabulary_includes_command_keywords():
    commands = [
        CommandRule("громкость *", "set_volume", "{0}"),
        CommandRule("сделай скриншот", "system", "screenshot"),
    ]
    vocab = build_vocabulary({}, {}, commands)
    assert "громкость" in vocab
    assert "скриншот" in vocab


def test_vocabulary_drops_stopwords_and_star():
    commands = [CommandRule("открой канал * на твиче", "open_channel", "twitch|{0}")]
    words = build_vocabulary({}, {}, commands).split()
    assert "*" not in words
    assert "на" not in words
    assert "твиче" in words


def test_proper_names_come_before_command_words():
    commands = [CommandRule("громкость *", "set_volume", "{0}")]
    words = build_vocabulary({"дота": "x"}, {}, commands).split()
    assert words.index("дота") < words.index("громкость")


def test_vocabulary_respects_word_budget():
    commands = [
        CommandRule(f"команда{i} слово{i}", "system", "x") for i in range(200)
    ]
    words = build_vocabulary({}, {}, commands).split()
    assert len(words) <= _MAX_VOCAB_WORDS


def test_multiword_alias_entries_are_split_into_words():
    # «серёга пират» — один алиас, но два СЛОВА. Раньше это была одна запись
    # в списке ДО обрезки по бюджету, из-за чего budget считал entries, а не
    # слова, и итоговая строка после .split() вылезала за лимит.
    channels = {"c": {"twitch": "c", "aliases": ["серёга", "серёга пират", "пират"]}}
    words = build_vocabulary({}, channels).split()
    assert "серёга пират" not in words
    assert words.count("серёга") == 1
    assert words.count("пират") == 1


def test_truncation_drops_keywords_not_proper_nouns():
    # Имена собственные заведомо не помещаются в бюджет сами по себе —
    # обрезка обязана срезать хвост словаря КОМАНД, а не имена.
    apps = {f"прога{i}": "x" for i in range(_MAX_VOCAB_WORDS + 5)}
    commands = [CommandRule("громкость *", "set_volume", "{0}")]
    words = build_vocabulary(apps, {}, commands).split()
    assert len(words) == _MAX_VOCAB_WORDS
    assert "громкость" not in words
    assert all(w.startswith("прога") for w in words)


def test_late_keyword_still_fits_after_many_earlier_commands():
    # Живой баг: «громкость» и другие слова громкости/медиа лежат в конце
    # commands.yaml. При бюджете в 60 ЗАПИСЕЙ (не слов) они срезались.
    commands = [CommandRule(f"неважно{i}", "system", "x") for i in range(70)] + [
        CommandRule("громкость *", "set_volume", "{0}")
    ]
    words = build_vocabulary({}, {}, commands).split()
    assert "громкость" in words


def test_имена_людей_попадают_в_словарь():
    people = {
        "гоша": {"discord": "Гречка", "aliases": ["гоша", "георгий"]},
        "твикс": {"discord": "Твикс 1", "aliases": ["твикс"]},
    }
    words = build_vocabulary({}, {}, None, people).split()
    for word in ("гоша", "георгий", "гречка", "твикс"):
        assert word in words, word


def test_имена_людей_идут_раньше_слов_команд():
    # Имена собственные переживают обрезку бюджета, слова команд — нет.
    # Кому уйдёт сообщение, решает именно услышанное имя.
    commands = [CommandRule("громкость *", "set_volume", "{0}")]
    people = {"гоша": {"discord": "Гречка", "aliases": ["гоша"]}}
    words = build_vocabulary({}, {}, commands, people).split()
    assert words.index("гоша") < words.index("громкость")


def test_имена_людей_переживают_обрезку_бюджета():
    people = {"гоша": {"discord": "Гречка", "aliases": ["гоша"]}}
    commands = [CommandRule(f"команда{i} слово{i}", "system", "x") for i in range(200)]
    words = build_vocabulary({}, {}, commands, people).split()
    assert len(words) == _MAX_VOCAB_WORDS
    assert "гоша" in words and "гречка" in words


def test_словарь_без_людей_работает_как_раньше():
    assert build_vocabulary({"дота": "x"}, {}) == "дота"


def test_живой_конфиг_вмещает_имена_людей_в_бюджет():
    """Живой config/: имена людей обязаны быть в подсказке, бюджет — целым.

    Бюджет измерен настоящим токенизатором и стоит вплотную к лимиту Whisper:
    его превышение молча срежет НАЧАЛО подсказки, а там имя «Джони», на
    котором держится защита от ложных срабатываний.
    """
    from johnny.config import load_config

    cfg = load_config("config")
    words = build_vocabulary(cfg.apps, cfg.channels, cfg.commands, cfg.people).split()
    assert len(words) <= _MAX_VOCAB_WORDS
    for person in cfg.people.values():
        for alias in person["aliases"]:
            assert alias.split()[0] in words, alias
