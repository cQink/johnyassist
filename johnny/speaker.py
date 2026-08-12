import logging
import random
import threading

logger = logging.getLogger(__name__)

_ACK_PHRASES = ["Слушаю", "Да, сэр", "Слушаю вас"]


class Speaker:
    def __init__(self, mode: str, tts=None, beep=None, play_wakeup=None, play_answer=None):
        self.mode = mode
        self._tts = tts
        self._beep = beep
        self._play_wakeup = play_wakeup  # callable() -> bool (проиграл ли звук)
        self._play_answer = play_answer  # callable() -> bool

    def say(self, text: str) -> None:
        if self.mode == "voice" and self._tts is not None:
            self._tts(text)
        elif self.mode == "beep" and self._beep is not None:
            self._beep()
        # mode == "off": молчание

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
    return Speaker(mode, tts=tts, beep=beep, play_wakeup=play_wakeup, play_answer=play_answer)
