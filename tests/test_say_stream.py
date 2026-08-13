"""Нарезка потока модели на фразы и решение «это разговор или команда».

Ошибка здесь слышна сразу: либо Джони зачитывает вслух JSON, либо ждёт конца
ответа и никакого стриминга не получается.
"""

import threading
import types

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


def test_filler_returns_one_of_the_phrases():
    fillers = say_stream.Fillers(["Секунду", "Момент"])
    assert fillers.pick() in ("Секунду", "Момент")


def test_filler_never_repeats_itself_twice_in_a_row():
    """Повтор одной и той же фразы подряд слышен сразу и звучит поломкой.
    random.choice (как в _ACK_PHRASES) этого не гарантирует."""
    fillers = say_stream.Fillers(["Секунду", "Момент", "Сейчас"])
    said = [fillers.pick() for _ in range(30)]
    assert all(first != second for first, second in zip(said, said[1:]))


def test_single_filler_is_allowed_to_repeat():
    """Иначе список из одной фразы не смог бы вернуть ничего вовсе."""
    fillers = say_stream.Fillers(["Секунду"])
    assert fillers.pick() == "Секунду"
    assert fillers.pick() == "Секунду"


def test_empty_list_means_no_filler():
    """Пустой список — это способ выключить филлеры, не выключая стриминг."""
    assert say_stream.Fillers([]).pick() == ""


def _voice(fail_after=None):
    """Подставной голос: помнит, что синтезировали и что играли."""
    state = types.SimpleNamespace(prepared=[], played=[], fallback=[])

    def prepare(text):
        state.prepared.append(text)
        if fail_after is not None and len(state.prepared) > fail_after:
            return types.SimpleNamespace(path=None, temporary=True)
        return types.SimpleNamespace(path=f"/tmp/{len(state.prepared)}.mp3", temporary=True)

    def play(path):
        state.played.append(path)

    def fallback(text):
        state.fallback.append(text)

    return say_stream.Voice(prepare=prepare, play=play, fallback=fallback), state


def test_conversation_is_spoken_phrase_by_phrase():
    voice, state = _voice()
    result = say_stream.consume(
        iter(["Привет. ", "Как дела? ", "Всё хорошо."]),
        voice, fillers=say_stream.Fillers([]),
    )
    assert result.spoken is True
    assert result.text == "Привет. Как дела? Всё хорошо."
    assert state.prepared == ["Привет.", "Как дела?", "Всё хорошо."]
    assert len(state.played) == 3


def test_json_answer_is_never_spoken():
    """Сторож: без него Джони однажды зачитает вслух {"command": ...}."""
    voice, state = _voice()
    result = say_stream.consume(
        iter(['{"command": ', '"громкость 5"}']),
        voice, fillers=say_stream.Fillers(["Секунду"]),
    )
    assert result.spoken is False
    assert state.prepared == []
    assert state.fallback == []
    assert result.text == '{"command": "громкость 5"}'


def test_filler_plays_before_the_first_phrase():
    """Филлер — единственное, что звучит в первые секунды: синтез первой фразы
    быстрее не станет."""
    voice, state = _voice()
    say_stream.consume(
        iter(["Дела отличные, спасибо. "]),
        voice, fillers=say_stream.Fillers(["Секунду"]),
    )
    assert state.prepared[0] == "Секунду"


def test_filler_is_silent_on_the_command_branch():
    """Человек попросил действие, а не разговор: «секундочку» перед выполнением
    команды — лишний звук."""
    voice, state = _voice()
    say_stream.consume(
        iter(['{"command": "громкость 5"}']),
        voice, fillers=say_stream.Fillers(["Секунду"]),
    )
    assert "Секунду" not in state.prepared


def test_next_phrase_is_synthesised_while_the_previous_one_plays():
    """Ядро всей затеи. Если синтез ждёт конца проигрывания, пауза между
    фразами равна времени синтеза и выигрыш исчезает на второй же фразе."""
    order = []

    def prepare(text):
        order.append(f"синтез:{text}")
        return types.SimpleNamespace(path=f"/tmp/{text}.mp3", temporary=True)

    def play(path):
        order.append(f"играю:{path}")
        # Держим «проигрывание», пока синтез второй фразы не успеет начаться.
        import time
        time.sleep(0.15)

    voice = say_stream.Voice(prepare=prepare, play=play, fallback=lambda text: None)
    say_stream.consume(
        iter(["Раз. ", "Два. "]), voice, fillers=say_stream.Fillers([])
    )
    # Синтез «Два.» обязан стоять в списке РАНЬШЕ, чем закончилось
    # проигрывание «Раз.» — то есть раньше «играю:/tmp/Два..mp3».
    assert order.index("синтез:Два.") < order.index("играю:/tmp/Два..mp3")


def test_fish_failure_speaks_the_remainder_in_one_piece():
    """Пофразное переключение голоса туда-обратно звучит как поломка. Остаток
    доигрывается одним куском запасным голосом."""
    voice, state = _voice(fail_after=1)
    result = say_stream.consume(
        iter(["Раз. ", "Два. ", "Три."]), voice, fillers=say_stream.Fillers([])
    )
    assert result.spoken is True
    assert state.fallback == ["Два. Три."]


def test_broken_stream_is_announced_out_loud():
    """Молчание после половины ответа неотличимо от законченного ответа, и
    человек не переспросит."""
    def chunks():
        yield "Начал отвечать. "
        raise say_stream.StreamBroken("сеть пропала")

    voice, state = _voice()
    result = say_stream.consume(chunks(), voice, fillers=say_stream.Fillers([]))
    assert result.broken is True
    assert result.spoken is True
    assert state.fallback and "связь" in state.fallback[-1].lower()


def test_broken_stream_before_any_speech_stays_silent():
    """Ничего не успели сказать — пусть отвечает следующий провайдер, а не
    Джони с извинениями."""
    def chunks():
        raise say_stream.StreamBroken("сеть пропала")
        yield ""

    voice, state = _voice()
    result = say_stream.consume(chunks(), voice, fillers=say_stream.Fillers([]))
    assert result.broken is True
    assert result.spoken is False
    assert state.fallback == []


def test_stop_breaks_the_whole_pipeline_not_just_the_current_phrase():
    """«Стоп» обязан отменить и то, что ещё не синтезировано: иначе Джони
    договаривает уже отменённый ответ, а Fish берёт за это деньги."""
    cancel = threading.Event()
    cancel.set()
    voice, state = _voice()
    result = say_stream.consume(
        iter(["Раз. ", "Два. ", "Три."]), voice,
        fillers=say_stream.Fillers([]), cancel=cancel,
    )
    assert state.prepared == []
    assert state.played == []
    assert result.spoken is False


def test_empty_stream_is_not_an_error():
    voice, state = _voice()
    result = say_stream.consume(iter([]), voice, fillers=say_stream.Fillers([]))
    assert result.text == ""
    assert result.spoken is False
    assert result.broken is False


def test_short_json_without_a_dot_is_still_not_spoken():
    """Ответ {"command": "громкость 5"} — 26 знаков и ни одной точки, то есть
    решение о ветке внутри цикла принято НЕ БУДЕТ. Без решения после цикла
    хвост ушёл бы в озвучку, и Джони зачитал бы JSON вслух."""
    voice, state = _voice()
    result = say_stream.consume(
        iter(['{"command": "громкость 5"}']), voice, fillers=say_stream.Fillers([])
    )
    assert result.spoken is False
    assert state.prepared == []


def test_stop_mid_stream_cleans_up_already_synthesised_files(tmp_path):
    """«Стоп» может прийти, когда следующая фраза уже засинтезирована, но ещё
    не сыграна: очередь на проигрывание отбрасывает её, но временный mp3-файл
    должен быть удалён, а не остаться в TEMP навсегда."""
    cancel = threading.Event()
    made = []
    second_ready = threading.Event()
    # exception внутри play() (например AssertionError) конвейер тихо
    # проглотил бы как обычный сбой проигрывания — проверять таймаут нужно
    # здесь, в основном потоке теста, а не внутри play().
    second_ready_in_time = []

    def prepare(text):
        path = tmp_path / f"{text}.mp3"
        path.write_bytes(b"data")
        made.append(path)
        if text == "Два.":
            second_ready.set()
        return types.SimpleNamespace(path=str(path), temporary=True)

    def play(path):
        if path.endswith("Раз..mp3"):
            # Ждём, пока «Два.» точно засинтезируется и встанет в очередь на
            # проигрывание, и только тогда просим остановиться.
            second_ready_in_time.append(second_ready.wait(timeout=1))
            cancel.set()

    voice = say_stream.Voice(prepare=prepare, play=play, fallback=lambda text: None)
    say_stream.consume(
        iter(["Раз. ", "Два. ", "Три."]), voice,
        fillers=say_stream.Fillers([]), cancel=cancel,
    )
    # Без этой проверки тест мог бы пройти вхолостую: истёк таймаут — «Два.»
    # не подоспела, и утверждения ниже ничего не проверяют про очередь.
    assert second_ready_in_time == [True], "«Два.» не успела засинтезироваться за 1с"
    assert made, "тест должен был что-то засинтезировать до стопа"
    assert all(not path.exists() for path in made)


def test_module_does_not_log_what_it_speaks():
    """Содержимое ответа не должно оседать на диске: history.log открывается
    кнопкой в панели и попадает на скриншоты. Сторож на весь модуль — снять
    его можно только осознанно."""
    import inspect

    assert "logger" not in inspect.getsource(say_stream)


def test_json_prefixed_by_prose_is_not_spoken_but_prose_is():
    """Модель иногда отвечает «Хорошо. {"command": ...}»: решение «разговор»
    принимается уже на восьмом знаке буфера («Хорошо. ») — задолго до
    DECIDE_AFTER (40), — и looks_like_command(buffer[:40]) на этом коротком
    начале ложно говорит «разговор». Без защиты на уровне ОТДЕЛЬНОЙ фразы
    хвост с JSON ушёл бы в синтез, и Джони зачитал бы вслух
    {"command": "громкость 5"} — то, чего не должно происходить никогда.
    «Хорошо.» же безобидно и уместно как подтверждение — его озвучиваем."""
    voice, state = _voice()
    result = say_stream.consume(
        iter(["Хорошо. ", '{"command": "громкость 5"}']),
        voice, fillers=say_stream.Fillers([]),
    )
    assert result.spoken is True
    assert state.prepared == ["Хорошо."]
    assert state.played == ["/tmp/1.mp3"]


def test_stream_stays_silent_after_a_suppressed_json_phrase():
    """JSON может встретиться не в первой фразе решения, а посреди уже
    «разговорной» ветки — и защёлкнуть командную ветку по ходу дела. Всё,
    что придёт ПОСЛЕ этого (даже отдельным куском от Groq), обязано остаться
    неозвученным — иначе часть текста вокруг JSON всё равно прорвётся наружу."""
    voice, state = _voice()
    say_stream.consume(
        iter([
            "Хорошо. ",
            '{"command": "громкость 5"}.',
            " И ещё что-то, что не должно прозвучать.",
        ]),
        voice, fillers=say_stream.Fillers([]),
    )
    # Единственное, что должно было уйти в синтез, — подтверждение до JSON.
    assert state.prepared == ["Хорошо."]


def test_result_text_keeps_the_json_even_when_nothing_is_spoken():
    """result.text уходит вызывающему на разбор JSON-команды — там должно
    быть ровно то, что не озвучили. Потерять кусок здесь значит потерять
    саму команду, а не просто испортить голос."""
    voice, state = _voice()
    payload = '{"command": "громкость 5", "reason": "перепутал с прошлым"}'
    result = say_stream.consume(
        iter([payload]), voice, fillers=say_stream.Fillers([])
    )
    assert result.spoken is False
    assert result.text == payload


def test_play_failure_evicts_the_broken_cached_file(tmp_path):
    """У филлеров и коротких служебных фраз Prepared.from_cache=True, а файл —
    один и тот же на все ответы (в отличие от temporary=True). Если конвейер
    смотрит только на `temporary` (как обычный, не стриминговый, путь в
    tts_cache._play_prepared делает уже сейчас) и не удаляет битый файл из
    кеша, эта фраза молчит одним и тем же обрывком НАВСЕГДА, до ручной
    чистки models/tts-cache/."""
    cached_path = tmp_path / "cached.mp3"
    cached_path.write_bytes(b"broken")

    def prepare(text):
        return types.SimpleNamespace(path=str(cached_path), temporary=False, from_cache=True)

    def play(path):
        raise RuntimeError("файл битый")

    voice = say_stream.Voice(prepare=prepare, play=play, fallback=lambda text: None)
    say_stream.consume(
        iter(["Секунду. "]), voice, fillers=say_stream.Fillers([])
    )
    assert not cached_path.exists()


def test_play_failure_speaks_the_remainder_in_order():
    """Комментарий в коде обещает, что запасной голос договорит «эту фразу и
    остаток» — но если сбой проигрывания не защёлкивает деградацию (как это
    уже делает сбой синтеза), вторая и третья фразы играют нормально, а
    первая произносится запасным голосом ПОСЛЕ них: человек слышит
    «Два. Три. ... Раз.» вместо «Раз. Два. Три.». Остаток обязан
    договариваться одним куском И в правильном порядке."""
    played = []

    def prepare(text):
        return types.SimpleNamespace(
            path=f"/tmp/{text}.mp3", temporary=True, from_cache=False
        )

    def play(path):
        played.append(path)
        if "Раз" in path:
            raise RuntimeError("устройство занято")

    fallback_calls = []
    voice = say_stream.Voice(prepare=prepare, play=play, fallback=fallback_calls.append)
    result = say_stream.consume(
        iter(["Раз. ", "Два. ", "Три."]), voice, fillers=say_stream.Fillers([])
    )
    assert result.spoken is True
    # Ни «Два.», ни «Три.» не должны были даже пытаться играть — иначе они
    # прозвучали бы раньше, чем запасной голос доберётся до «Раз.».
    assert played == ["/tmp/Раз..mp3"]
    assert fallback_calls == ["Раз. Два. Три."]


def test_prepare_exception_is_treated_as_a_synthesis_failure():
    """Сетевой сбой Fish (обрыв, таймаут) — ОБЫЧНЫЙ способ отказа синтеза, не
    аномалия сверх контракта Voice. Если consume() не приравнивает исключение
    из prepare() к уже существующему пути отказа (Prepared(path=None), от
    которого play() как раз защищён), synth_loop умирает прямо на вызове,
    конец очереди в `audio` никогда не уходит, и player.join() — а с ним и
    сам consume() — висит навсегда. Гоняем consume() в отдельном потоке
    именно поэтому: тест обязан завершиться сам, а не повиснуть вместе с ней.
    """
    def prepare(text):
        raise RuntimeError("сеть пропала")

    def play(path):
        raise AssertionError("играть нечего — синтез должен был отказать")

    fallback_calls = []
    voice = say_stream.Voice(prepare=prepare, play=play, fallback=fallback_calls.append)

    result_box = []

    def run():
        result_box.append(
            say_stream.consume(
                iter(["Раз. ", "Два."]), voice, fillers=say_stream.Fillers([])
            )
        )

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(timeout=5)

    assert not worker.is_alive(), "consume() зависла на исключении из voice.prepare()"
    assert result_box, "consume() не успела вернуть результат за отведённое время"
    assert result_box[0].spoken is True
    assert fallback_calls == ["Раз. Два."]


def test_filler_playback_failure_does_not_demote_the_answer():
    """Филлер едет по той же очереди, что и фразы ответа, и почти всегда
    играет из кеша — битый файл кеша это обычно именно он. Без метки на
    элементе очереди сбой ЕГО проигрывания защёлкивает деградацию (весь
    ответ уходит запасному голосу) и подмешивает ТЕКСТ ФИЛЛЕРА в остаток:
    человек слышит «Секунду Раз. Два. Три.» чужим голосом вместо нормального
    ответа своим. Проверяем оба симптома разом: пустой fallback (текста
    филлера в нём нет и деградации не было) и то, что все три фразы ответа
    действительно дошли до voice.play (а не осели в leftover).
    """
    played = []

    def prepare(text):
        return types.SimpleNamespace(
            path=f"/tmp/{text}.mp3",
            temporary=(text != "Секунду"),
            from_cache=(text == "Секунду"),
        )

    def play(path):
        played.append(path)
        if path == "/tmp/Секунду.mp3":
            raise RuntimeError("файл битый")

    fallback_calls = []
    voice = say_stream.Voice(prepare=prepare, play=play, fallback=fallback_calls.append)
    result = say_stream.consume(
        iter(["Раз. ", "Два. ", "Три."]),
        voice, fillers=say_stream.Fillers(["Секунду"]),
    )
    assert result.spoken is True
    assert fallback_calls == []
    assert played == [
        "/tmp/Секунду.mp3", "/tmp/Раз..mp3", "/tmp/Два..mp3", "/tmp/Три..mp3",
    ]
