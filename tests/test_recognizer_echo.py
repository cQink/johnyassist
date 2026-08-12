from johnny.recognizer import is_prompt_echo, _BASE_PROMPT

# Строим подсказку из РЕАЛЬНОГО _BASE_PROMPT + фиктивного словаря — раньше
# здесь была захардкожена старая шапка («Голосовые команды на русском...»),
# которая уже не встречается в коде, из-за чего тесты оставались зелёными,
# пока фильтр молча переставал ловить эхо.
_FAKE_VOCAB = "открой ютуб запусти доту зайди на смурф дота апекс спотифай импульс ёж"
PROMPT = f"{_BASE_PROMPT} {_FAKE_VOCAB}"


def test_bare_header_is_echo():
    # Ровно то, что Whisper вернул вместо «пауза»: голая шапка подсказки,
    # без единого слова словаря. Короче _ECHO_MIN_WORDS, но всё равно эхо.
    assert is_prompt_echo(_BASE_PROMPT, PROMPT) is True


def test_vocabulary_slice_is_echo():
    assert is_prompt_echo("дота апекс спотифай импульс", PROMPT) is True


def test_short_command_is_not_echo():
    # 2 слова: короче порога, хоть и встречается в подсказке.
    assert is_prompt_echo("открой ютуб", PROMPT) is False


def test_three_words_from_prompt_are_not_echo():
    # Порог 4 слова: «зайди на смурф» — настоящая команда, не трогаем.
    assert is_prompt_echo("зайди на смурф", PROMPT) is False


def test_real_command_is_not_echo():
    assert is_prompt_echo("поставь на паузу пожалуйста", PROMPT) is False


def test_case_and_yo_are_ignored():
    assert is_prompt_echo(_BASE_PROMPT.upper(), PROMPT) is True


def test_words_out_of_order_are_not_echo():
    # Не непрерывный кусок подсказки (слова словаря переставлены) — значит
    # настоящая речь, а не продолжение подсказки.
    assert is_prompt_echo("апекс дота импульс спотифай", PROMPT) is False


def test_empty_text_is_not_echo():
    assert is_prompt_echo("", PROMPT) is False


def test_header_is_derived_from_own_prompt_argument():
    # Сигнатура функции обещает работать с ЛЮБЫМ prompt, а не только с тем,
    # что начинается с глобального _BASE_PROMPT. Берём подсказку с другой
    # шапкой (тоже 4 слова, как и у _BASE_PROMPT) и проверяем, что функция
    # ловит ИМЕННО эту шапку как эхо, а не молча сверяется с _BASE_PROMPT.
    other_prompt = "Совсем другая шапка тут. алиас1 алиас2 алиас3 алиас4"
    assert is_prompt_echo("Совсем другая шапка тут", other_prompt) is True
    # И наоборот: голая _BASE_PROMPT-шапка внутри ЭТОЙ подсказки эхом не
    # является — она не встречается в other_prompt вовсе.
    assert is_prompt_echo(_BASE_PROMPT, other_prompt) is False


def test_merged_command_after_name_is_not_echo():
    # Слитный режим: расшифровка штатно начинается с имени. Когда имя стояло
    # в КОНЦЕ шапки, за ним в подсказке сразу начинался словарь, и такая
    # настоящая команда ложно опознавалась как эхо и молча выбрасывалась.
    assert is_prompt_echo("джони открой ютуб запусти", PROMPT) is False


def test_yo_in_vocabulary_is_normalized_to_ye():
    # В словаре — «ёж» через ё; Whisper обычно транскрибирует такие слова без
    # точек, через «е» («еж»). Без .replace("ё", "е") в _words() это сравнение
    # не совпало бы — что и было бы незамеченной регрессией, так как раньше
    # ни подсказка, ни фиктивный словарь этого теста не содержали «ё» вовсе.
    # 4 слова — попадает в срез по словарю, а не в раннее совпадение с шапкой.
    assert is_prompt_echo("апекс спотифай импульс еж", PROMPT) is True
