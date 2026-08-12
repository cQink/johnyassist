from johnny.config import CommandRule
from johnny.listener import recover_misheard_name, strip_wake_word

VARIANTS = ["джони", "джонни", "джани"]


def test_strips_name_from_start():
    assert strip_wake_word("джони громкость пять", VARIANTS) == ("громкость пять", True)


def test_strips_misheard_variants():
    for said in ("джонни громкость пять", "джани громкость пять"):
        assert strip_wake_word(said, VARIANTS) == ("громкость пять", True)


def test_strips_punctuation_and_case():
    assert strip_wake_word("Джони, громкость пять", VARIANTS) == ("громкость пять", True)


def test_strips_near_miss_of_whisper():
    # Whisper пишет имя как попало; нечёткое сравнение (0.75) обязано ловить
    # то, чего нет в списке вариантов дословно.
    assert strip_wake_word("джоник громкость пять", VARIANTS) == ("громкость пять", True)


def test_cuts_up_to_last_occurrence_in_head():
    # Пре-ролл 1.5с иногда захватывает хвост предыдущей речи, и в голову
    # фразы может попасть лишнее «джони». Два имени подряд: режем до
    # ПОСЛЕДНЕГО — при срезе до первого в команду попал бы мусор.
    assert strip_wake_word("джани джони громкость", VARIANTS) == ("громкость", True)


def test_ignores_match_beyond_head():
    # Дальше третьего слова не смотрим: случайное созвучие в конце длинной
    # фразы срезало бы всю команду целиком.
    said = "включи первое видео джони"
    assert strip_wake_word(said, VARIANTS) == (said, False)


def test_strips_name_at_third_word():
    # Ради этого случая голова и длиной в три слова: пре-ролл 1.5с может
    # захватить пару слов чужой речи перед именем.
    assert strip_wake_word("эй ага джони громкость", VARIANTS) == ("громкость", True)


def test_reports_not_found_when_name_absent():
    # Vosk услышал имя, Whisper — нет. Флаг False понижает доверие: модель
    # к такой фразе уже не подключаем.
    assert strip_wake_word("да я говорю ему", VARIANTS) == ("да я говорю ему", False)


def test_yo_is_normalized():
    assert strip_wake_word("джёни громкость", VARIANTS) == ("громкость", True)


def test_name_alone_gives_empty_command():
    # Позвал слитно, а команды не сказал — текст пустой, но имя найдено:
    # контроллер по этому сочетанию уйдёт в восстановление (звук + ожидание).
    assert strip_wake_word("джони", VARIANTS) == ("", True)


def test_empty_text():
    assert strip_wake_word("", VARIANTS) == ("", False)


COMMANDS_FOR_RECOVERY = [
    CommandRule("сделай громче", "system", "volume_up"),
    CommandRule("громкость *", "set_volume", "{0}"),
    CommandRule("найди на ютубе *", "browser_open", "yt?q={0}"),
    CommandRule("включи первое видео", "browser_click_result", "1"),
]


def test_recovers_name_heard_as_another_word():
    """Живой случай из логов: Whisper услышал «Джони» как «не».

    Имя не теряется — оно ЗАМЕНЯЕТСЯ чужим словом и остаётся первым,
    портя разбор. Похожесть «не» на «джони» всего 0.29, никакой порог
    нечёткого сравнения тут не поможет.
    """
    assert (
        recover_misheard_name("не сделай громче", COMMANDS_FOR_RECOVERY) == "сделай громче"
    )


def test_recovers_name_heard_as_long_word():
    """Второй живой случай: имя услышано как «желание».

    Эта фраза в логах ушла в тишину: с лишним словом она не разбиралась
    ни точно, ни цепочкой, а модель без якоря имени не спрашивают.
    """
    text = "желание найди на ютубе послезавтра и включи первое видео"
    assert (
        recover_misheard_name(text, COMMANDS_FOR_RECOVERY)
        == "найди на ютубе послезавтра и включи первое видео"
    )


def test_does_not_touch_a_phrase_that_is_already_a_command():
    """«сделай громче» без остатка тоже команда («громче»), но фраза цела —
    выбрасывать первое слово нельзя, иначе съедим нужное."""
    assert recover_misheard_name("сделай громче", COMMANDS_FOR_RECOVERY) is None


def test_does_not_recover_when_remainder_is_not_a_command():
    assert recover_misheard_name("абракадабра полная", COMMANDS_FOR_RECOVERY) is None


def test_single_word_cannot_be_recovered():
    assert recover_misheard_name("абракадабра", COMMANDS_FOR_RECOVERY) is None


from johnny.listener import recover_latin_prefix


def test_recovers_latin_wake_word_garbage():
    """Живой случай из history.log (2026-08-04): активатор услышан как «vd»,
    дальше — обычный связный русский текст."""
    assert (
        recover_latin_prefix("vd перезапустил и команда ввод нормально работает")
        == "перезапустил и команда ввод нормально работает"
    )


def test_recovers_latin_wake_word_garbage_before_long_conversation():
    text = "vd а еще он сам становится тише из за того что делает громкость тише"
    assert (
        recover_latin_prefix(text)
        == "а еще он сам становится тише из за того что делает громкость тише"
    )


def test_does_not_touch_all_latin_phrase():
    # Вся фраза на латинице — сравнивать не с чем (нет кириллицы в остатке),
    # резать её как «мусор перед активатором» нельзя.
    assert recover_latin_prefix("hello world") is None


def test_does_not_touch_single_word():
    assert recover_latin_prefix("vd") is None


def test_does_not_touch_long_latin_first_word():
    # Длиннее 4 букв — не похоже на короткий обрывок распознавания, скорее
    # настоящее слово (например, имя программы).
    assert recover_latin_prefix("steam завис у меня") is None


def test_does_not_touch_when_no_cyrillic_follows():
    # Мусорное слово есть, но дальше тоже не кириллица — нечего восстанавливать.
    assert recover_latin_prefix("vd 123 456") is None
