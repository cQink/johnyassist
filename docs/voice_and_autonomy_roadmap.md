# Roadmap: персонализированный голос и автономность Johnny

Документ фиксирует фазы, технологии и политики для перехода от текущего TTS-стека к персональному голосу и контролируемой автономности ассистента.

**Связанные задачи:** [prepare_voice_and_autonomy_roadmap.md](superpowers/plans/details/prepare_voice_and_autonomy_roadmap.md), [integrate_external_voice_model_service.md](superpowers/plans/details/integrate_external_voice_model_service.md)

---

## 1. Текущее состояние

### Голос (TTS)

| Слой | Реализация | Файл |
|------|------------|------|
| Фасад | `Speaker` — режимы `voice` / `beep` / `off` | `johnny/speaker.py` |
| Цепочка fallback | fish (Джарвис) → edge-tts (Дмитрий) → pyttsx3 (офлайн) | `johnny/speaker.py`, `johnny/tts_fish.py` |
| Облачный клон | Fish Audio API, кеш коротких фраз | `johnny/tts_fish.py` |
| Конфиг | `tts_provider`, `fish_model_id`, `tts_voice`, `response_mode` | `johnny/config.py` |

Цепочка уже поддерживает **graceful degradation**: при недоступности fish включается edge, затем pyttsx3. Cooldown и валидация mp3 защищают от зависаний.

### Автономность и состояние

| Механизм | Назначение | Файл |
|----------|------------|------|
| `AssistantController` | Цикл прослушивания, пауза, отмена, busy | `johnny/controller.py` |
| Фоновое выполнение | Команды в worker-потоке, «стоп» во время речи | `johnny/controller.py` |
| Память | short-term (5 turns) + long-term (yaml) | `johnny/memory.py` |
| Действия | Реестр `@registry.register` | `johnny/actions/registry.py` |

Явного слоя «уровней автономности» пока нет — все команды выполняются сразу после распознавания, кроме отмены через «стоп».

---

## 2. Сравнение TTS-технологий

### Критерии выбора

- Качество русской речи и интонации
- Латency (целевое: < 2 с до начала воспроизведения для коротких фраз)
- Возможность клонирования / персонализации
- Офлайн vs облако
- Требования к GPU и стоимость эксплуатации

### Варианты

| Технология | Качество RU | GPU | Латency | Персонализация | Примечание |
|------------|-------------|-----|---------|----------------|------------|
| **Fish Audio** (текущий) | Высокое | Нет (API) | 1–3 с | Клон по reference_id | Уже интегрирован; зависимость от API и тарифа |
| **edge-tts** (текущий fallback) | Хорошее | Нет | 1–2 с | Нет (фикс. голоса MS) | Бесплатно, нужен интернет |
| **pyttsx3** (текущий fallback) | Низкое | Нет | < 0.5 с | Ограничено системными голосами | Полностью офлайн |
| **NVIDIA NeMo / Riva** | Высокое | Да (6+ GB VRAM) | 0.5–1.5 с локально | Fine-tune, voice cloning | Лучший кандидат для **локального** сервиса |
| **Coqui TTS / XTTS v2** | Хорошее | Да (4+ GB VRAM) | 1–3 с | Zero-shot clone (~6 с аудио) | Проще развернуть, чем NeMo |
| **Tacotron 2 + WaveGlow** | Среднее (RU слабее) | Да | 1–2 с | Требует обучения | Устаревает; не рекомендуется как основной путь |
| **ElevenLabs / PlayHT** (commercial) | Очень высокое | Нет (API) | 1–2 с | Клон по образцу | Платно; данные уходят в облако |
| **VoiceStudio** (debpalash) | Зависит от модели | Опционально | — | Студия для обучения | Инструмент подготовки, не runtime TTS |

### Рекомендация

1. **Краткосрочно (Phase 1):** оставить fish → edge → pyttsx3; добавить абстракцию `TTSBackend` для подключения новых провайдеров без правок `Speaker`.
2. **Среднесрочно (Phase 2):** локальный сервис на **Coqui XTTS v2** или **NeMo TTS** (FastAPI, см. задачу integrate_external_voice_model_service) — при наличии GPU.
3. **Долгосрочно (Phase 3):** fine-tune / клон собственного голоса через VoiceStudio + локальный inference; commercial API только как резерв.

### Требования к GPU (локальный сервис)

| Модель | VRAM | RAM | Примечание |
|--------|------|-----|------------|
| XTTS v2 (inference) | 4 GB | 8 GB | RTX 3060 и выше |
| NeMo FastPitch + HiFi-GAN | 6 GB | 16 GB | RTX 3060 Ti+ |
| NeMo voice cloning (fine-tune) | 12+ GB | 32 GB | RTX 4070+ или cloud |
| Nemotron VoiceChat (full pipeline) | 8+ GB | 16 GB | ASR + TTS; см. integrate_external_voice_model_service |

Без GPU: облачные API (fish, edge, ElevenLabs) или CPU-inference XTTS (медленно, ~5–10 с на фразу).

---

## 3. Фазы разработки

### Phase 1 — Prototyping (1–2 недели)

**Цель:** абстракция TTS и первый локальный/облачный эксперiment без смены UX.

| Задача | Результат |
|--------|-----------|
| Ввести интерфейс `TTSBackend.synthesize(text) -> bytes` | `johnny/tts/base.py` |
| Обернуть fish, edge, pyttsx3 в backends | `johnny/tts/fish.py`, `edge.py`, `local.py` |
| Конфиг `tts_backends: [local_nemo, fish, edge, pyttsx3]` — цепочка fallback | `config.yaml` |
| Прототип FastAPI-сервиса (XTTS или NeMo) в `services/tts-server/` | Docker + README |
| A/B сравнение: latency, MOS (субъективно 1–5), размер mp3 | Таблица в `docs/tts_benchmarks.md` |

**Критерий выхода:** переключение провайдера одной строкой конфига; fallback работает как сейчас.

### Phase 2 — Integration (2–3 недели)

**Цель:** production-ready локальный TTS-сервис и эмоциональные варианты.

| Задача | Результат |
|--------|-----------|
| Клиент в `johnny/tts/nemo_client.py` через `http_client.py` | Retry, cooldown, health-check |
| Health-check при старте Johnny: если local TTS недоступен — пропустить в цепочке | Лог + метрика |
| Параметры голоса: `voice_style` (calm / confident / friendly) — префикс промпта или SSML | config + Speaker |
| Кеш расширить: эмоциональные служебные фразы | `models/tts-cache/` |
| Интеграционные тесты с mock-сервером | `tests/test_tts_backends.py` |

**Критерий выхода:** локальный TTS на GPU машине пользователя; fish остаётся fallback для машин без GPU.

### Phase 3 — Production (3–4 недели)

**Цель:** персональный голос, мониторинг, политики автономности.

| Задача | Результат |
|--------|-----------|
| Pipeline записи эталонного голоса (30–60 мин чистой речи) | `docs/voice_recording_guide.md` |
| Fine-tune / clone через VoiceStudio или NeMo | Модель в `models/voice/` |
| Метрики: latency p50/p95, error rate, cache hit rate | Логирование + опционально Prometheus |
| Слой автономности (см. §4) | `johnny/autonomy.py` |
| UX: индикатор «Джонни думает / говорит / выполняет» в tray | `johnny/tray.py` |

**Критерий выхода:** стабильный персональный голос; явные границы автономных действий; документированные метрики.

---

## 4. Политики автономности

### Уровни (AutonomyLevel)

| Уровень | Описание | Примеры |
|---------|----------|---------|
| **L0 — Reactive** | Только по явной команде после wake word | «Джонни, открой браузер» |
| **L1 — Confirm** | Предложение + подтверждение голосом | «Напомнить через час? — Да / Нет» |
| **L2 — Trusted** | Выполнение без подтверждения для whitelist | Громкость, пауза музыки, повтор команды |
| **L3 — Proactive** | Инициатива по расписанию / контексту | «Вы каждый день в 9:00 открываете IDE — открыть?» |
| **L4 — Autonomous** | Цепочки задач без участия пользователя | «Собери отчёт и отправь в Discord» |

**Стартовая конфигурация:** L0 для всех действий, L2 только для безопасного whitelist (`volume`, `pause`, `repeat`, `stop`).

### Классификация действий

| Категория | Уровень по умолчанию | Требует подтверждения |
|-----------|---------------------|------------------------|
| Чтение / озвучка | L0 | Нет |
| Управление медиа, громкость | L2 | Нет |
| Открытие URL, приложений | L1 | Да, если URL не в whitelist |
| Ввод текста, клавиатура | L1 | Всегда |
| Discord / мессенджеры | L1 | Отправка сообщений — всегда |
| Файловая система (удаление, запись) | L1 | Всегда |
| Системные (выключение, autostart) | L1 | Всегда |
| Социальные / OSINT / face | L0 | Запрещено без явной команды и consent |

### Принципы безопасности

1. **Deny by default:** новое действие регистрируется с L1, понижение — только явно в конфиге.
2. **Kill switch:** «Джонни, стоп» прерывает любой уровень (уже реализовано в `controller.py`).
3. **Audit log:** все L2+ действия пишутся в `history.log` с меткой уровня.
4. **No exfiltration:** L3/L4 не отправляют данные во внешние API без `secrets` и флага `allow_cloud`.
5. **Rate limits:** proactive (L3) — не чаще 1 инициативы в 15 минут без ответа пользователя.

### Самовосполнение (self-healing)

| Сбой | Авто-действие | Уровень |
|------|---------------|---------|
| TTS недоступен | Fallback по цепочке | L2 (автоматически) |
| Микрофон отключён | Пауза + уведомление в tray | L2 |
| 5 подряд ошибок цикла | Останов прослушивания | L2 (уже есть) |
| Fish API 429 | Cooldown + edge | L2 (уже есть) |
| Brain API down | Локальный ответ «не могу связаться с моделью» | L2 |

Расширение: автоперезапуск listener после `MicrophoneError` с exponential backoff (макс. 3 попытки).

---

## 5. Архитектура интеграции голосового сервиса

```
┌─────────────┐     HTTP/gRPC      ┌──────────────────┐
│   Johnny    │ ─────────────────► │  TTS Server      │
│  Speaker    │   POST /synthesize │  (FastAPI)       │
│  tts_client │ ◄───────────────── │  XTTS / NeMo     │
└─────────────┘     audio/mpeg     └──────────────────┘
       │                                    │
       │ fallback                           │ GPU
       ▼                                    ▼
  fish → edge → pyttsx3              models/voice/
```

**Контракт API (минимальный):**

```http
POST /synthesize
Content-Type: application/json

{"text": "Слушаю", "voice_id": "johnny_v1", "style": "calm"}

→ 200 audio/mpeg
→ 503 (Johnny пропускает backend в цепочке)
```

**Размещение:**

| Режим | Когда | Плюсы / минусы |
|-------|-------|----------------|
| Local (localhost:8765) | Есть GPU | Приватность, низкая latency |
| LAN (другой ПК в сети) | Johnny на ноутбуке, GPU на десктопе | Гибкость |
| Cloud VPS | Нет локального GPU | Latency + privacy trade-off |
| Colab / ephemeral | Прототипирование | Не для production |

---

## 6. План тестирования и метрики UX

### Функциональные тесты

| Область | Тест | Файл |
|---------|------|------|
| Fallback chain | mock 503 на fish → edge вызывается | `tests/test_tts_backends.py` |
| Cache | повторная фраза не бьёт в API | `tests/test_tts_fish.py` (расширить) |
| Latency | synthesize < 3 s (mock) | `tests/test_tts_backends.py` |
| Autonomy | L1 action блокируется без confirm | `tests/test_autonomy.py` |
| Stop | команда прерывает TTS и worker | `tests/test_controller.py` (есть) |

### Метрики UX

| Метрика | Целевое значение | Как мерить |
|---------|------------------|------------|
| Time-to-first-audio | < 2 s (короткая фраза) | timestamp в логе |
| TTS error rate | < 1% сессий | счётчик fallback |
| Wake-to-action | < 5 s (простая команда) | history.log |
| False wake rate | < 2 / час | ручной лог / опция в config |
| User interrupt success | «стоп» срабатывает в 95% | тест + поле в history |
| MOS (субъективно) | ≥ 4/5 для персонального голоса | опрос после Phase 2 |

### Чеклист ручного QA (каждый релиз TTS)

- [ ] Короткая фраза («Слушаю») — без обрезки
- [ ] Длинный ответ модели (> 200 символов) — без таймаута
- [ ] Нет интернета — pyttsx3 или edge fail gracefully
- [ ] Fish cooldown — edge подхватывает без 10 с паузы
- [ ] «Стоп» во время длинной речи — воспроизведение обрывается

---

## 7. Зависимости и риски

| Риск | Митигация |
|------|-----------|
| Нет GPU у пользователя | Облачный fish/edge как default |
| Клон голоса без согласия | Документ + флаг `voice_cloning_consent` в config |
| Высокая latency локальной модели | Streaming TTS (Phase 3), prefetch служебных фраз |
| Автономность ломает UX | Начинать с L0/L1; L3 только opt-in |
| NeMo / torch тяжёлые deps | Отдельный Docker-сервис, не в основном venv |

---

## 8. Следующие шаги (immediate)

1. **Refactor for extensibility** — `TTSBackend` логически связан с plugin-слоем; делать параллельно или сразу после.
2. **integrate_external_voice_model_service** — FastAPI-обёртка по контракту из §5.
3. Отметить Phase 1 kickoff в `current_work_plan.md` после merge этого документа.

---

*Версия: 2026-08-07. Автор: подготовлено в рамках задачи prepare_voice_and_autonomy_roadmap.*
