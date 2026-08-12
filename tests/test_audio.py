import pytest

from johnny.audio import BLOCK_FRAMES, BlockBuffer, MicrophoneError


def _block(value: int = 0) -> bytes:
    """Блок нужного размера, заполненный одним значением int16."""
    return value.to_bytes(2, "little", signed=True) * BLOCK_FRAMES


def test_read_block_returns_what_was_put():
    buf = BlockBuffer()
    buf.put(_block(1))
    assert buf.read_block() == _block(1)


def test_read_block_raises_on_timeout():
    # Микрофон отвалился: callback перестал срабатывать. Без таймаута чтение
    # висело бы вечно, и защита контроллера (счётчик сбоев подряд) никогда
    # бы не сработала.
    buf = BlockBuffer()
    with pytest.raises(MicrophoneError):
        buf.read_block(timeout=0.01)


def test_preroll_keeps_only_recently_read_blocks():
    # Кольцо на 1.5с при блоке 0.25с — это 6 блоков. Седьмой вытесняет первый.
    buf = BlockBuffer(preroll_seconds=1.5)
    for i in range(1, 8):
        buf.put(_block(i))
        buf.read_block()
    assert buf.preroll() == b"".join(_block(i) for i in range(2, 8))


def test_preroll_holds_only_read_blocks():
    # В пре-ролл попадает то, что уже прочитано (Vosk это прослушал), а не
    # то, что ещё лежит в очереди: иначе снимок «назад во времени» захватил
    # бы будущее и то же самое аудио попало бы в запись дважды.
    buf = BlockBuffer()
    buf.put(_block(1))
    buf.put(_block(2))
    buf.read_block()
    assert buf.preroll() == _block(1)


def test_preroll_does_not_consume():
    buf = BlockBuffer()
    buf.put(_block(1))
    buf.read_block()
    assert buf.preroll() == _block(1)
    assert buf.preroll() == _block(1)


def test_flush_clears_queue_and_preroll():
    # Флаш нужен после собственного звука Джони: иначе он запишет свой «пик»
    # или услышит в ответе TTS собственное имя и разбудит сам себя.
    buf = BlockBuffer()
    buf.put(_block(1))
    buf.read_block()
    buf.put(_block(2))
    buf.flush()
    assert buf.preroll() == b""
    with pytest.raises(MicrophoneError):
        buf.read_block(timeout=0.01)


def test_overflow_drops_oldest_block():
    # Пока Джони выполняет команду, никто не читает очередь. Потолок не даёт
    # ей расти в памяти бесконечно, а выбрасывается САМОЕ СТАРОЕ — свежий
    # звук всегда важнее протухшего.
    buf = BlockBuffer(max_blocks=2)
    buf.put(_block(1))
    buf.put(_block(2))
    buf.put(_block(3))
    assert buf.read_block() == _block(2)
    assert buf.read_block() == _block(3)


import numpy as np

from johnny.audio import is_silent, record_until_silence, to_float32

_LOUD = 8000  # заметно выше порога тишины (тот держится на сотнях в int16)

# Живой замер на микрофоне пользователя (Yeti Classic, 2026-07-27, три фразы
# «Джони, сделай громче» с обычного расстояния): речь идёт на RMS
# 0.0012–0.0145, то есть 40–475 в int16, а фон между фразами не поднимается
# выше 0.00065 (21). Порог обязан лежать между этими мирами: речь тише
# порога — и слитная фраза уезжает в ветку с «пиком» посреди слов.
_QUIET_SPEECH = 100  # RMS 0.0031 — рядовой блок тихой фразы
_ROOM_NOISE = 21  # RMS 0.00064 — самый громкий блок фона в том замере


def _fill(buf, *blocks):
    for value in blocks:
        buf.put(_block(value))
    return buf


def test_to_float32_scales_int16_to_unit_range():
    assert to_float32(_block(0)).max() == 0.0
    assert abs(to_float32(_block(32767)).max() - 1.0) < 0.001


def test_is_silent_distinguishes_silence_from_speech():
    assert is_silent(_block(0)) is True
    assert is_silent(_block(_LOUD)) is False


def test_quiet_speech_counts_as_speech_and_room_noise_does_not():
    # Порог достался по наследству от старого рекордера, где решал только,
    # когда обрывать запись. В слитном режиме он решает уже другое — говорил
    # ли человек сразу после имени, — и на живом голосе оказался ВЫШЕ речи:
    # две фразы из трёх выглядели тишиной.
    assert is_silent(_block(_QUIET_SPEECH)) is False
    assert is_silent(_block(_ROOM_NOISE)) is True


def test_quiet_phrase_right_after_name_is_heard_as_joined():
    # Тот самый отказ: сказано слитно, но тихо — и Джони решал, что позвали
    # и ждут, поэтому «Да, сэр» звучало человеку в середину фразы.
    buf = _fill(BlockBuffer(), _ROOM_NOISE, _QUIET_SPEECH, _QUIET_SPEECH, 0, 0, 0, 0, 0)
    raw, started = record_until_silence(buf, silence_seconds=1.0, start_timeout=0.75)
    assert started is True
    assert raw.startswith(_block(_QUIET_SPEECH))


def test_no_speech_at_all_reports_not_started():
    # Ты сказал «Джони» и замолчал — контроллер по этому флагу поймёт, что
    # пора играть звук-подтверждение.
    buf = _fill(BlockBuffer(), 0, 0, 0, 0)
    raw, started = record_until_silence(buf, start_timeout=1.0)
    assert started is False
    assert raw == b""


def test_leading_silence_is_not_recorded():
    buf = _fill(BlockBuffer(), 0, 0, _LOUD, 0, 0, 0, 0, 0)
    raw, started = record_until_silence(buf, silence_seconds=1.0, start_timeout=2.0)
    assert started is True
    # Записано начиная с речи: сам громкий блок + хвост тишины до отсечки.
    assert raw.startswith(_block(_LOUD))


def test_recording_stops_after_silence_following_speech():
    # silence_seconds=1.0 → int(1.0*16000/4000) = 4 тихих блока подряд.
    buf = _fill(BlockBuffer(), _LOUD, 0, 0, 0, 0, _LOUD, _LOUD)
    raw, started = record_until_silence(buf, silence_seconds=1.0, start_timeout=2.0)
    assert started is True
    assert len(raw) == 5 * BLOCK_FRAMES * 2  # речь + 4 тихих, дальше не читали


def test_recording_respects_max_seconds():
    buf = _fill(BlockBuffer(), *([_LOUD] * 10))
    raw, _ = record_until_silence(buf, max_seconds=1.0, start_timeout=2.0)
    assert len(raw) == 4 * BLOCK_FRAMES * 2  # 1.0с = 4 блока


def test_default_max_seconds_does_not_cut_off_long_dictation():
    # Живой баг (2026-08-05): команда «Джони, ввод <длинный текст>» обрывалась
    # посреди фразы — старый дефолт max_seconds=8.0 резал запись через 8с
    # НЕЗАВИСИМО от того, договорил человек или нет (в логе — окно тишины
    # ровно ~7.9-8.5с прямо перед обрезанным текстом). Речь дольше старого
    # потолка (10с) должна дозаписаться до настоящей тишины, а не оборваться.
    buf = _fill(BlockBuffer(), *([_LOUD] * 40), 0, 0, 0, 0, 0)
    raw, started = record_until_silence(buf)
    assert started is True
    assert len(raw) == 44 * BLOCK_FRAMES * 2  # вся речь + хвост тишины, не 32 блока


def test_silence_counter_resets_on_speech():
    # Пауза внутри фразы («Джони… эээ… громкость пять») не должна обрывать
    # запись: счётчик тишины обязан сбрасываться на каждом громком блоке.
    buf = _fill(BlockBuffer(), _LOUD, 0, 0, _LOUD, 0, 0, 0, 0, _LOUD)
    raw, _ = record_until_silence(buf, silence_seconds=1.0, start_timeout=2.0)
    assert len(raw) == 8 * BLOCK_FRAMES * 2


def test_dead_microphone_propagates_error():
    # Пустой буфер = микрофон молчит совсем. Ошибка должна дойти до
    # контроллера, а не превратиться в «тишину» (иначе Джони делал бы вид,
    # что всё хорошо, при отключённом микрофоне).
    with pytest.raises(MicrophoneError):
        record_until_silence(BlockBuffer(), start_timeout=1.0)
