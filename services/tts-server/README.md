# Локальный TTS-сервис Johnny

FastAPI-обёртка над локальными TTS-моделями по контракту из
[docs/voice_and_autonomy_roadmap.md](../../docs/voice_and_autonomy_roadmap.md) §5:
текст на вход, mp3 на выход. Никакой телеметрии — текст не покидает машину.

Сервис **не обязателен** для работы Johnny: клиент
[`johnny/tts_local.py`](../../johnny/tts_local.py) честно ловит 503 и уходит
на fish → edge → pyttsx3. Машина без GPU просто не запускает этот сервис —
Johnny заговорит голосом Джарвиса (fish) или Дмитрия (edge).

## Требования

| Модель | VRAM | RAM | Примечание |
|--------|------|-----|------------|
| NeMo FastPitch + HiFi-GAN | 6 GB | 16 GB | RTX 3060 Ti+, английская речь |
| Coqui XTTS v2 (inference) | 4 GB | 8 GB | RTX 3060+, русская речь, zero-shot клон |

Порядок попыток при старте: **NeMo → XTTS v2 → None**.
Ни одна модель не поднялась — сервис живёт и отвечает 503: для Johnny это
штатное состояние, а не ошибка. Быстрая честная 503 лучше долгой паузы:
клиенту дешевле сразу озвучить запасным голосом, чем ждать.

Зависимости ставятся в **отдельное** окружение — они тянут torch/nemo-toolkit,
и в основной venv Johnny (requirements.txt) их намеренно не кладём
(roadmap §7: «NeMo / torch тяжёлые deps — отдельный Docker-сервис, не в основном venv»).

## Запуск

```bash
python -m venv .venv
.venv\Scripts\pip install -U "nemo_toolkit[tts]" fastapi uvicorn pydantic soundfile  # или TTS (XTTS v2)
.venv\Scripts\uvicorn tts_server:app --host 127.0.0.1 --port 8765
```

Первое поднятие модели качает веса и занимает минуты; потом модель грузится
в память один раз при старте сервиса (не при первом запросе) — первый
synthesize не ждёт загрузки.

Проверка:

```bash
curl http://127.0.0.1:8765/health        # {"status":"ok","backend":"nemo"} или "xtts"/null
curl -X POST http://127.0.0.1:8765/synthesize \
  -H "Content-Type: application/json" \
  -d '{"text":"Слушаю","voice_id":"johnny_v1","style":"calm"}' \
  -o out.mp3
```

## Контракт API

| Метод | Путь | Тело | Ответ |
|-------|------|------|-------|
| GET | `/health` | — | `{"status","backend"}` |
| POST | `/synthesize` | `{"text","voice_id","style"}` | `200 audio/mpeg` |
| POST | `/synthesize` | — | `400 {"error"}` (пустой текст) |
| POST | `/synthesize` | — | `503 {"error"}` (модель не поднялась / синтез упал) |

`voice_id` и `style` опциональны: `None` — голос модели по умолчанию.
`style` (calm | confident | friendly) — что умеет сервис, у XTTS это
безусловный no-op (не ломает ничего, просто игнорируется).

Тело 200 — **только mp3**. Клиент валидирует сигнатуру и отбрасывает
всё остальное (это наследие живого бага: fish.audio отвечал 200 с HTML —
Cloudflare-страницей — и эта «речь» кешировалась навсегда; см.
`johnny/tts_cache.py`). Поэтому wav отдавать нельзя.

## Голос

- **NeMo** — `tts_en_fastpitch` + `tts_en_hifigan` (английская речь, без клонирования).
- **XTTS v2** — мультиязычный, `language="ru"`. Zero-shot клон голоса: положите
  образец (≈6–30 с чистой речи, wav/mp3) и укажите путь переменной окружения:

```bash
set JOHNNY_VOICE_SAMPLE=C:\voice\sample.wav
```

Без образца XTTS говорит своим голосом по умолчанию.

## Подключение к Johnny

В `config/settings.yaml`:

```yaml
tts_provider: local            # local — локальный сервис ПЕРВЫМ, fish/edge — запас
tts_local_url: "http://127.0.0.1:8765/synthesize"   # или LAN/VPS
tts_voice_id: "johnny_v1"      # id голоса в сервисе (None — сервисный по умолчанию)
tts_style: "calm"              # calm | confident | friendly
```

Правила цепочки (закреплены тестами `tests/test_speaker.py`):

- `tts_provider: local` без адреса — локальный сервис выключен, говорим fish/edge.
- fish остаётся **вторым звеном** при `local`: машина без запущенного
  GPU-сервиса не теряет голос Джарвиса.
- `tts_provider: edge` — единственный режим, отключающий fish начисто
  (в облако не ходим вовсе).
- У каждого звена свой cooldown: упавший локальный сервис не глушит попытки
  к fish и наоборот (ключи `tts-local` / `fish` в `johnny/tts_cache.py`).

## Тесты

Клиентская сторона покрыта `tests/test_tts_local.py` (контракт тела, 503,
не-mp3 тело, fallback, кеш, cooldown-ключ). Интеграция с цепочкой —
`tests/test_speaker.py` (выбор провайдера, fish как второе звено, edge без
сетевых попыток). Серверный код намеренно без pytest: его реальный тест —
это запущенный сервис + живой клиент (ручной QA-чеклист в roadmap §6).
