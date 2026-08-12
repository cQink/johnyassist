"""Сервер TTS по контракту Johnny (docs/voice_and_autonomy_roadmap.md).

Поднимает модель и отдаёт mp3 на POST /synthesize. Порядок попыток:
NeMo TTS (FastPitch + HiFi-GAN, ~6 GB VRAM), затем Coqui XTTS v2 (~4 GB).
Ни одна не установлена или нет GPU — сервис поднимается и честно отвечает 503:
для Johnny это НОРМА, клиент (johnny/tts_local.py) молча уходит на fish/edge.

Быстрые честные ошибки важнее долгих пауз: клиенту дешевле сразу озвучить
запасным голосом, чем ждать. Поэтому здесь нет ни очередей, ни ретраев.

Размещение — localhost, LAN или облако (Google Cloud Run). В облаке
ОБЯЗАТЕЛЕН токен: за endpoint'ом стоит GPU, и открытый наружу инференс
оплачивает чужие запросы из твоего кармана. См. JOHNNY_TTS_TOKEN ниже.

Запуск (см. README.md рядом):
    uvicorn tts_server:app --host 127.0.0.1 --port 8765

Подключение: в config/settings.yaml — tts_provider: local,
tts_local_url: "http://127.0.0.1:8765/synthesize", tts_voice_id: "johnny_v1".
"""

from __future__ import annotations

import hmac
import json
import logging
import os

from fastapi import FastAPI, Request
from fastapi.responses import Response
from pydantic import BaseModel

logger = logging.getLogger("tts_server")

# Пустой токен = проверки нет. Это осознанный дефолт для localhost, и ровно
# поэтому README требует задать его перед любым деплоем наружу: сервис не
# может отличить «я на localhost» от «я на публичном IP», а тихо ломаться
# на своей же машине он не должен.
_TOKEN = os.environ.get("JOHNNY_TTS_TOKEN", "")

# Поднятие модели занимает десятки секунд — делаем это один раз при старте,
# чтобы первый запрос не ждал минуту. None = ни одна модель не поднялась.
_backend = None
_load_attempted = False


class SynthesizeRequest(BaseModel):
    text: str
    voice_id: str | None = None  # None — голос модели по умолчанию
    style: str | None = None     # опционально: calm | confident | friendly


def _to_mp3(samples, sample_rate: int) -> bytes:
    """float32 -1..1 → mp3. Клиент проверяет тело на сигнатуру mp3 и
    отбрасывает всё остальное, поэтому wav отдавать нельзя."""
    import io

    import numpy as np
    import soundfile as sf

    buffer = io.BytesIO()
    peak = float(np.max(np.abs(samples))) or 1.0
    sf.write(buffer, samples / peak * 0.95, sample_rate, format="MP3")
    return buffer.getvalue()


def _try_nemo():
    """NeMo FastPitch + HiFi-GAN. Бросает, если nemo_toolkit не установлен."""
    from nemo.collections.tts.models import FastPitchModel, HifiGanModel

    spec = FastPitchModel.from_pretrained("tts_en_fastpitch").eval()
    vocoder = HifiGanModel.from_pretrained("tts_en_hifigan").eval()

    class NemoBackend:
        name = "nemo"

        def synthesize(self, text: str, voice_id, style) -> bytes:
            tokens = spec.parse(text)
            mel = spec.generate_spectrogram(tokens=tokens)
            audio = vocoder.convert_spectrogram_to_audio(spec=mel)
            return _to_mp3(audio.cpu().detach().numpy().squeeze(), 22050)

    return NemoBackend()


def _try_xtts():
    """Coqui XTTS v2: zero-shot клон по образцу голоса (см. README про образец)."""
    import os

    from TTS.api import TTS

    model = TTS("tts_models/multilingual/multi-dataset/xtts_v2")
    reference = os.environ.get("JOHNNY_VOICE_SAMPLE", "")

    class XttsBackend:
        name = "xtts"

        def synthesize(self, text: str, voice_id, style) -> bytes:
            import numpy as np

            wav = model.tts(text=text, speaker_wav=reference or None, language="ru")
            return _to_mp3(np.asarray(wav, dtype="float32"), 24000)

    return XttsBackend()


def resolve_backend():
    """Первая поднявшаяся модель, или None. Не бросает: отсутствие GPU и
    зависимостей — рабочее состояние (см. docstring модуля)."""
    for attempt in (_try_nemo, _try_xtts):
        try:
            return attempt()
        except Exception as exc:
            logger.info("%s недоступен: %s", attempt.__name__, exc)
    return None


def _load_backend() -> None:
    global _backend, _load_attempted
    if not _load_attempted:
        _load_attempted = True
        _backend = resolve_backend()
        logger.info("TTS backend: %s", getattr(_backend, "name", "нет"))


def _error(status: int, message: str) -> Response:
    return Response(
        json.dumps({"error": message}).encode("utf-8"),
        status_code=status,
        media_type="application/json",
    )


def _authorized(request: Request) -> bool:
    """Bearer-токен. hmac.compare_digest, а не ==, чтобы сравнение не сливало
    префикс токена по времени ответа."""
    if not _TOKEN:
        return True
    header = request.headers.get("Authorization", "")
    prefix = "Bearer "
    if not header.startswith(prefix):
        return False
    return hmac.compare_digest(header[len(prefix):], _TOKEN)


app = FastAPI(title="Johnny TTS server", version="0.1.0")


@app.get("/health")
def health(request: Request) -> Response:
    """Заодно точка прогрева: клиент дёргает её вне голосового пути, чтобы
    холодный старт облака не съел первую фразу (tts_local.warm_up)."""
    if not _authorized(request):
        return _error(401, "unauthorized")
    _load_backend()
    return Response(
        json.dumps({"status": "ok", "backend": getattr(_backend, "name", None)}),
        media_type="application/json",
    )


@app.post("/synthesize")
def synthesize(req: SynthesizeRequest, request: Request) -> Response:
    if not _authorized(request):
        # 401, а не 503: клиент уйдёт на запасной голос в любом случае, но в
        # логе должно быть видно «неверный токен», а не «модель не поднялась».
        return _error(401, "unauthorized")
    _load_backend()
    if _backend is None:
        return _error(503, "tts model unavailable")
    if not req.text.strip():
        return _error(400, "empty text")
    try:
        data = _backend.synthesize(req.text, req.voice_id, req.style)
    except Exception as exc:
        logger.warning("Синтез упал: %s", exc)
        return _error(503, "synthesis failed")
    return Response(content=data, media_type="audio/mpeg")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8765)
