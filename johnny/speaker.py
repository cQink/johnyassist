import logging
import random
import threading

from . import say_stream

logger = logging.getLogger(__name__)

_ACK_PHRASES = ["Слушаю", "Да, сэр", "Слушаю вас"]


class Speaker:
    def __init__(self, mode: str, tts=None, beep=None, play_wakeup=None, play_answer=None,
                 stream_voice=None, fillers=None, first_limit: int = 120):
        self.mode = mode
        self._tts = tts
        self._beep = beep
        self._play_wakeup = play_wakeup  # callable() -> bool (проиграл ли звук)
        self._play_answer = play_answer  # callable() -> bool
        # Конвейер стриминга: None = недоступен (не fish, или выключен).
        self._stream_voice = stream_voice
        self._fillers = fillers if fillers is not None else say_stream.Fillers([])
        self._first_limit = first_limit

    def say(self, text: str) -> None:
        if self.mode == "voice" and self._tts is not None:
            self._tts(text)
        elif self.mode == "beep" and self._beep is not None:
            self._beep()
        # mode == "off": молчание

    def say_stream(self, chunks, cancel=None):
        """Озвучить поток кусков текста конвейером. None = конвейер недоступен.

        None означает «иди старым путём» и возвращается честно: на edge-голосе
        синтез и проигрывание неразделимы, а при streaming: false конвейера нет
        по решению человека.
        """
        if self.mode != "voice" or self._stream_voice is None:
            return None
        return say_stream.consume(
            chunks,
            self._stream_voice,
            fillers=self._fillers,
            first_limit=self._first_limit,
            cancel=cancel,
        )

    def acknowledge(self) -> None:
        """Услышал «Джонни»: свой wakeup-звук, иначе — сигнал + голос."""
        if self.mode == "off":
            return
        if self._play_wakeup is not None and self._play_wakeup():
            return
        if self._beep is not None:
            self._beep()
        if self.mode == "voice" and self._tts is not None:
            self._tts(random.choice(_ACK_PHRASES))

    def play_answer(self) -> bool:
        """Подтверждение-«ответ» после команды. True = озвучено (свой звук
        или тишина в off), False = звука нет, пусть вызывающий скажет текст."""
        if self.mode == "off":
            return True
        if self._play_answer is not None and self._play_answer():
            return True
        return False


def _make_tts(volume: float = 1.0):
    try:
        import pyttsx3
    except ModuleNotFoundError:
        def say(text: str) -> None:
            return None

        return say

    engine = pyttsx3.init()
    engine.setProperty("volume", volume)
    for voice in engine.getProperty("voices"):
        if "russian" in voice.name.lower() or "ru" in (voice.id or "").lower():
            engine.setProperty("voice", voice.id)
            break

    def say(text: str) -> None:
        engine.say(text)
        engine.runAndWait()

    return say


def _edge_synth_and_play(text: str, voice: str) -> None:
    """Синтезировать текст через edge-tts (облако MS, бесплатно) и проиграть mp3.

    Требует интернет. Файл — во временную папку, после воспроизведения удаляется.
    """
    import asyncio
    import os
    import tempfile

    import edge_tts

    from . import sounds

    path = os.path.join(
        tempfile.gettempdir(), f"johnny_tts_{os.getpid()}_{random.randint(0, 1_000_000)}.mp3"
    )
    try:
        asyncio.run(edge_tts.Communicate(text, voice).save(path))
        sounds.play_file(path)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def _make_edge_tts(voice: str, fallback):
    """say() через edge-tts; при сбое (нет интернета/ошибка) — офлайн-голос fallback."""

    def say(text: str) -> None:
        if not text:
            return
        try:
            _edge_synth_and_play(text, voice)
        except Exception:
            fallback(text)

    return say


def _make_beep(volume: float = 1.0):
    """Мягкий синусоидный «дон» с плавным затуханием (не режет уши)."""
    try:
        import numpy as np
        import sounddevice as sd
    except ModuleNotFoundError:
        def beep():
            return None

        return beep

    sr = 44100
    dur = 0.16
    t = np.linspace(0, dur, int(sr * dur), False)
    tone = 0.22 * volume * np.sin(2 * np.pi * 620.0 * t)
    fade = int(sr * 0.03)  # плавные вход/выход, чтобы не щёлкало
    envelope = np.ones_like(tone)
    envelope[:fade] = np.linspace(0.0, 1.0, fade)
    envelope[-fade:] = np.linspace(1.0, 0.0, fade)
    wave = (tone * envelope).astype("float32")

    def beep():
        sd.play(wave, sr)
        sd.wait()

    return beep


def _start_local_warm_up(url: str, token: str) -> None:
    """Разбудить TTS-сервис заранее, в фоне — ВНЕ голосового пути.

    Cloud Run со scale-to-zero поднимает модель десятки секунд, а голосовой
    таймаут короткий (tts_local._TIMEOUT, 5 с): без прогрева ПЕРВАЯ же фраза
    Джони гарантированно уезжает на запасной голос, и человек слышит не тот
    голос, который настроил. Прогрев именно здесь, на единственном условии
    включения сервиса, — разъехавшись с ним, он будил бы выключенный сервис
    или молчал при включённом.

    Поток-демон, а не await/join: warm_up ждёт ответа до минуты, и запуск
    Джони не должен упираться в спящее облако. warm_up никогда не бросает и
    возвращает bool, так что потоку нечего ронять, а демон не задержит выход.
    """
    from .tts_local import warm_up

    def run() -> None:
        woke = warm_up(url, token=token)
        # Адрес — из settings.yaml (он в репозитории), это не секрет; token
        # в лог не попадает ни целиком, ни частями.
        logger.info("Прогрев TTS-сервиса %s: %s", url, "проснулся" if woke else "не ответил")

    threading.Thread(target=run, daemon=True, name="tts-warm-up").start()


def _make_voice_tts(settings, secrets):
    """Цепочка синтеза: свой TTS-сервис (NeMo/XTTS) → fish-Джарвис → edge-Дмитрий → pyttsx3.

    Сервис включается, только когда задан tts_provider: local и tts_local_url.
    Не запущен сервис — это НОРМАЛЬНОЕ состояние (свой GPU есть не у всех, а
    облачный инстанс мог уснуть), Джони молча заговорит голосом Джарвиса или
    Дмитрия, и только упавший на глазах сервис оставит след в логе warn_once —
    после cooldown попытки к нему повторятся.

    Джарвис включается, только когда сошлось всё: провайдер fish, ключ в
    секретах и id клонированного голоса. Не сошлось — сразу Дмитрий, без
    единой сетевой попытки.
    """
    edge = _make_edge_tts(settings.tts_voice, _make_tts(settings.tts_volume))
    # Свой сервис — первый по приоритету. Импорт внутри функции намеренно:
    # без tts_provider: local модуль (и его долгий путь в кеш) не грузится вовсе.
    if settings.tts_provider == "local" and settings.tts_local_url:
        from .tts_local import make_local_tts

        # Токен — из секретов, не из settings.yaml: settings.yaml лежит в
        # репозитории, secrets.yaml в .gitignore. Пустой токен = заголовка
        # нет (localhost в нём не нуждается).
        token = (secrets or {}).get("tts_local_token", "")
        # Прогрев — сразу, пока грузятся Whisper и vosk (это минуты, см.
        # тайминги запуска в tray.main): к первой фразе сервис уже проснётся.
        _start_local_warm_up(settings.tts_local_url, token)
        return make_local_tts(
            settings.tts_local_url,
            settings.tts_voice_id,
            _make_fish_or_edge(settings, secrets, edge),
            style=settings.tts_style,
            token=token,
        )
    return _make_fish_or_edge(settings, secrets, edge)


def _make_fish_or_edge(settings, secrets, edge):
    """fish-Джарвис при полном наборе (провайдер + ключ + id голоса), иначе edge.

    Провайдер local тоже пускает fish: он стоит ВТОРЫМ звеном за локальным
    сервисом, иначе машина без запущенного GPU-сервиса теряла бы голос
    Джарвиса и падала сразу на Дмитрия. Провайдер edge — единственный, кто
    отключает fish начисто (в облако не ходим вовсе).
    """
    api_key = (secrets or {}).get("fish_api_key")
    if settings.tts_provider in ("fish", "local") and api_key and settings.fish_model_id:
        # Импорт внутри функции намеренно: при tts_provider: edge модуль
        # tts_fish (и его кеш в models/tts-cache/) не грузится вовсе. requests
        # всё равно приезжает транзитивно через brain_groq — это уже не повод
        # для ленивого импорта, но сам tts_fish без нужды тянуть незачем.
        # Не выносить наверх.
        from .tts_fish import make_fish_tts

        return make_fish_tts(api_key, settings.fish_model_id, edge)
    return edge


def _make_stream_voice(settings, secrets):
    """say_stream.Voice или None, если конвейер на этом голосе невозможен.

    Возможен он только на fish: там синтез отдаёт mp3 отдельным шагом
    (tts_cache.make_cached_prepare), и файл можно готовить, пока играет
    предыдущий. У edge синтез и проигрывание слиты внутри edge_tts, у pyttsx3
    файла нет вовсе — обещать там стриминг было бы обманом.
    """
    if not getattr(settings, "streaming", False):
        return None
    api_key = (secrets or {}).get("fish_api_key")
    if settings.tts_provider not in ("fish", "local") or not api_key or not settings.fish_model_id:
        return None

    from . import sounds, tts_cache
    from .tts_fish import _CACHE_DIR, synthesize

    prepare = tts_cache.make_cached_prepare(
        lambda text: synthesize(text, api_key, settings.fish_model_id),
        provider="fish",
        voice_key=settings.fish_model_id,
        cache_dir=_CACHE_DIR,
    )
    # Запасной голос — тот же edge-Дмитрий, что и у обычного say: остаток
    # ответа после отказа Fish договаривает он.
    fallback = _make_edge_tts(settings.tts_voice, _make_tts(settings.tts_volume))
    return say_stream.Voice(prepare=prepare, play=sounds.play_file, fallback=fallback)


def make_speaker(settings, secrets=None) -> Speaker:
    mode = settings.response_mode
    tts = _make_voice_tts(settings, secrets) if mode == "voice" else None
    beep = _make_beep(settings.tts_volume) if mode in ("voice", "beep") else None
    play_wakeup = play_answer = None
    if mode != "off":
        from . import sounds

        sounds.set_volume(settings.tts_volume)
        play_wakeup = lambda: sounds.play_random("wakeup")  # noqa: E731
        play_answer = lambda: sounds.play_random("answer")  # noqa: E731
    stream_voice = _make_stream_voice(settings, secrets) if mode == "voice" else None
    return Speaker(
        mode,
        tts=tts,
        beep=beep,
        play_wakeup=play_wakeup,
        play_answer=play_answer,
        stream_voice=stream_voice,
        fillers=say_stream.Fillers(getattr(settings, "streaming_fillers", [])),
        first_limit=int(getattr(settings, "streaming_first_chunk", 120)),
    )
