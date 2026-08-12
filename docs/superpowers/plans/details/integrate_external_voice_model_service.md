## Integrate external voice model service (NVIDIA NeMo / nemotron-labs-voicechat)

Цель: Подключить и протестировать внешний голосовой сервис (local или cloud) и интегрировать с `johnny`.

Критерии успеха:
- Сервис принимает текст и возвращает аудио.
- Интегрированный endpoint доступен через `johnny/http_client.py` и `johnny/tts_fish.py`.

Шаги:
1. Выбрать окружение (local GPU / Colab / cloud VPS).
2. Развернуть модель и обёртку API (FastAPI).
3. Реализовать клиент в `johnny` и тесты проигрывания.
4. Добавить fallback на другие TTS если сервис недоступен.

Зависимости: torch, nemo-toolkit и GPU

Приватность/безопасность: не отправлять приватные данные в публичный облачный endpoint без согласия.

Оценка: 3-7 дней
