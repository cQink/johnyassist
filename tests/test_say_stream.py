"""Нарезка потока модели на фразы и решение «это разговор или команда».

Ошибка здесь слышна сразу: либо Джони зачитывает вслух JSON, либо ждёт конца
ответа и никакого стриминга не получается.
"""

import johnny.say_stream as say_stream


def test_cut_takes_the_first_finished_sentence():
    phrase, rest = say_stream.cut("Привет. Как дела", 120)
    assert phrase == "Привет."
    assert rest == "Как дела"


def test_cut_waits_while_the_sentence_is_unfinished():
    """Отдать полфразы в синтез — значит услышать обрыв на полуслове."""
    phrase, rest = say_stream.cut("Привет, как", 120)
    assert phrase == ""
    assert rest == "Привет, как"


def test_cut_gives_up_waiting_at_the_limit():
    """Модель иногда сыплет текст вообще без точек. Без потолка первая фраза
    дождалась бы конца ответа — то есть стриминга бы не было."""
    text = "слово " * 40
    phrase, rest = say_stream.cut(text, 60)
    assert phrase
    assert len(phrase) <= 60
    assert not phrase.endswith("сл")   # режем по границе слова, не посреди


def test_cut_can_take_two_sentences_at_once():
    """Дальше первой фразы спешить некуда: Джони уже говорит, а каждый лишний
    вызов Fish — это и деньги, и ещё одна слышимая пауза."""
    phrase, rest = say_stream.cut("Раз. Два. Три", 120, sentences=2)
    assert phrase == "Раз. Два."
    assert rest == "Три"


def test_cut_takes_what_there_is_if_asked_for_more():
    phrase, rest = say_stream.cut("Раз. Два", 120, sentences=2)
    assert phrase == "Раз."
    assert rest == "Два"


def test_cut_keeps_the_question_mark_with_the_sentence():
    phrase, _ = say_stream.cut("Правда? Да", 120)
    assert phrase == "Правда?"


def test_command_branch_is_recognised_by_a_brace():
    """Модель отвечает JSON на исправленную команду. Озвучить его — значит
    прочитать вслух {"command": "громкость 5"}."""
    assert say_stream.looks_like_command('{"command": "громкость 5"}') is True


def test_command_branch_is_recognised_by_a_fence():
    assert say_stream.looks_like_command("```json") is True


def test_ordinary_answer_is_not_a_command():
    assert say_stream.looks_like_command("Дела отлично, спасибо") is False


def test_cut_bounds_sentences_from_below():
    """При sentences ≤ 0 функция должна вернуть ровно одну фразу, а не все.

    Иначе озвучится весь ответ вместо одной фразы (Джони зачитает не то),
    а при отрицательном значении ещё и упадёт на IndexError.
    """
    # sentences=0 должен вернуть первую фразу, не все
    phrase, rest = say_stream.cut("Раз. Два. Три.", 120, sentences=0)
    assert phrase == "Раз."
    assert rest == "Два. Три."

    # sentences=-3 должен вернуть первую фразу, не упасть
    phrase, rest = say_stream.cut("Раз. Два.", 120, sentences=-3)
    assert phrase == "Раз."
    assert rest == "Два."
