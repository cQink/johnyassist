"""Claude Opus 5 по API-ключу — «доп мозг» в цепочке провайдеров.

Зачем отдельный модуль, когда `brain_claude` уже называется «claude». Тот
запускает CLI `claude -p` подпроцессом: он ходит под чужой авторизацией, живёт
3–5 секунд и под `pythonw` (трей) уже один раз молча падал на резолве
`claude.cmd`. Здесь — обычный сетевой вызов по ключу из secrets.yaml, без
подпроцесса и без зависимости от того, что установлено на машине.

Решения, которые важно не «упростить» обратно:

  - Ходим через официальный SDK `anthropic`, а не через свой `http_client`, как
    остальные интеграции. Это сознательное исключение из конвенции проекта:
    форма запроса у Messages API за год менялась несколько раз (см. ниже про
    temperature и thinking), и повторять её руками — способ узнать об очередной
    правке от рассерженного человека, а не от типизированного клиента.
  - `temperature` НЕ передаётся. На Opus 5 этого параметра больше нет, и любой
    из тройки temperature/top_p/top_k возвращает 400. Скопировать строку из
    `brain_groq` (там `temperature: 0.3`) — сломать модуль целиком.
  - `thinking` не передаётся тоже, и это не забывчивость: у Opus 5 размышление
    включено по умолчанию, а старая форма `{"type": "enabled",
    "budget_tokens": N}` теперь отвечает 400. Отключать его (`disabled`) можно
    только при effort не выше `high`, и оно даёт известный побочный эффект —
    теги `<thinking>` протекают в видимый текст, а его мы читаем вслух.
    Дешевле опустить параметр и крутить `effort`.
  - `max_tokens` считает размышление И ответ ОДНИМ бюджетом. Тут же вторая
    причина не копировать `brain_groq`: его `max_tokens: 200` на Opus 5 не
    «короткий ответ», а обрыв на середине размышления и пустой текст.
  - `stop_reason` проверяется ДО чтения `content`. Отказ классификатора
    приходит с HTTP 200 и пустым списком блоков — привычное `content[0]` на нём
    падает IndexError'ом, то есть исключением там, где сервис ответил нормально.
"""

from __future__ import annotations

from .http_client import in_cooldown, mark_failure, warn_once

MODEL = "claude-opus-5"

# Имя провайдера в цепочке и в ключах cooldown. Отдельно от "claude": тот —
# CLI-подпроцесс, и глушить их общим cooldown'ом нельзя, они падают по разным
# причинам.
NAME = "opus"

# 20 секунд — потолок терпения человека, который ждёт ответа ГОЛОСОМ. Значение
# SDK по умолчанию (10 минут) тут означало бы намертво замерший ассистент.
#
# Замер 2026-08-08 через посредника agentrouter.org, промпт на 227 команд:
# 2–7 с в обычном случае, но первые вызовы после паузы — 29–39 с. Причём до
# первого слова уходит почти всё это время, а генерация занимает 0.5–2 с, так
# что стримингом ожидание не разбить: во время паузы приходить нечему.
# Поднять потолок до 40 с значило бы иногда молчать 40 секунд — хуже, чем
# сказать «не понял» и дать переспросить, тем более что повторный вызов почти
# всегда быстрый (маршрутизатор к этому моменту прогрет).
TIMEOUT_SECONDS = 20.0

# Повторы отключены сознательно, хотя SDK по умолчанию делает два. Повтор
# растягивает ожидание кратно (20 с → 60 с), а запасной путь у нас и так есть:
# пустая строка отсюда уводит вопрос к следующему провайдеру немедленно. Роль
# «подождать и попробовать снова» играет cooldown на 60 секунд.
MAX_RETRIES = 0

# Бюджет на размышление плюс ответ. Джони отвечает 1–3 предложениями, но
# урезать до сотен токенов нельзя — размышление съест их первым, и вернётся
# пустой текст, оплаченный целиком.
MAX_TOKENS = 4096

# Рычаг цены и задержки вместо temperature. Opus 5 силён на `low`, а ответ
# голосового ассистента — не диссертация. Уровни: low | medium | high | xhigh |
# max, по умолчанию (без параметра) был бы high.
EFFORT = "low"

# Ключи, по которым сервис ответил «не тот ключ» или «нет доступа». В отличие
# от сети такие отказы сами не чинятся: без этого списка Джони раз в минуту
# (после каждого cooldown) снова тратил бы 20 секунд человеческого ожидания на
# заведомо отказной запрос.
#
# Помним ПАРУ (ключ, адрес), а не ключ: ключ посредника на api.anthropic.com
# отвечает 401, а на своём адресе работает — запомнив один ключ, мы выключили
# бы и рабочую пару тоже.
_rejected: set[tuple[str, str]] = set()

# Клиент на пару (ключ, адрес), один на весь запуск. make_providers() зовётся на
# КАЖДУЮ фразу (johnny/app.py), поэтому клиент обязан пережить вызов: свой пул
# соединений на каждое слово — это лишний TLS-хендшейк там, где человек ждёт
# ответа.
_clients: dict[tuple[str, str], object] = {}

# Сколько раз подряд пара (ключ, адрес) отвечала таймаутом. Обнуляется удачей.
#
# Зачем считать: таймаут и обрыв связи — разные беды, а cooldown лечит только
# вторую. По замеру 2026-08-08 первый вызов после паузы берёт 29–39 с, а
# следующий 2–7 с: маршрутизатор прогревается. Уходя в cooldown после ПЕРВОГО
# же таймаута, Джони замолкает на минуту ровно тогда, когда следующий вопрос
# был бы отвечен быстро. Поэтому первый таймаут прощаем, а второй подряд уже
# считаем сбоем — иначе вернулись бы к трате 20 секунд на каждую фразу, от
# которой cooldown и защищает.
_timeouts: dict[tuple[str, str], int] = {}

# Сколько таймаутов подряд считать «просто медленно», а не «сломалось».
TIMEOUTS_BEFORE_COOLDOWN = 2


def _client(api_key: str, base_url: str = ""):
    """Клиент SDK для пары (ключ, адрес), один на весь запуск. None — пакета нет."""
    cache_key = (api_key, base_url)
    if cache_key in _clients:
        return _clients[cache_key]
    try:
        import anthropic
    except ImportError:
        # Пакет необязательный, как argostranslate у переводчика: без него
        # Джони работает, просто без сильной модели.
        warn_once(NAME, "Нет пакета anthropic (pip install anthropic) — Opus 5 выключен")
        _clients[cache_key] = None
        return None
    # base_url пустой не передаём вовсе: SDK сам подставит официальный адрес, а
    # base_url="" сделал бы запрос к пустому хосту.
    extra = {"base_url": base_url} if base_url else {}
    _clients[cache_key] = anthropic.Anthropic(
        api_key=api_key, timeout=TIMEOUT_SECONDS, max_retries=MAX_RETRIES, **extra
    )
    return _clients[cache_key]


def _text_of(message) -> str:
    """Видимый текст ответа.

    `content` — список блоков разного типа: размышление приходит отдельным
    блоком и вслух не читается. Берём только текстовые, иначе Джони озвучит
    ход рассуждения вместо ответа.
    """
    parts = [
        block.text
        for block in (getattr(message, "content", None) or [])
        if getattr(block, "type", "") == "text" and getattr(block, "text", "")
    ]
    return "\n".join(parts).strip()


def make_provider(api_key: str, model: str = MODEL, base_url: str = ""):
    """Функция prompt -> текст ответа. Пустая строка = не смог, пусть отвечает
    следующий провайдер — тот же контракт, что у brain_groq.make_provider.

    base_url пустой = официальный api.anthropic.com. Непустой нужен посредникам
    (у владельца — agentrouter.org): ключ у них свой, и на официальном адресе
    он неизвестен.
    """
    rejection_key = (api_key, base_url)

    def run(prompt: str) -> str:
        if rejection_key in _rejected or in_cooldown(NAME):
            return ""
        client = _client(api_key, base_url)
        if client is None:
            return ""
        try:
            message = client.messages.create(
                model=model or MODEL,
                max_tokens=MAX_TOKENS,
                output_config={"effort": EFFORT},
                messages=[{"role": "user", "content": prompt}],
            )
            # Отказ классификатора — это HTTP 200 с пустым или обрезанным
            # content, а не исключение. Проверяем до чтения блоков.
            if getattr(message, "stop_reason", "") == "refusal":
                raise RuntimeError("модель отказалась отвечать")
            answer = _text_of(message)
            if not answer:
                raise RuntimeError(f"пустой ответ (stop_reason={getattr(message, 'stop_reason', '')})")
            _timeouts.pop(rejection_key, None)   # ответил — счётчик медленных обнулён
            return answer
        except Exception as error:
            if _is_permanent(error):
                # Ключ неверен или доступа нет — молчим до перезапуска, а не
                # раз в минуту по 20 секунд.
                _rejected.add(rejection_key)
                warn_once(NAME, f"Ключ Opus 5 не принят ({error}) — модель выключена до перезапуска")
                return ""
            if _is_timeout(error):
                seen = _timeouts.get(rejection_key, 0) + 1
                _timeouts[rejection_key] = seen
                if seen < TIMEOUTS_BEFORE_COOLDOWN:
                    # Медленно — не значит сломано. Cooldown тут отнял бы у
                    # человека минуту молчания вместо одного «переспросите».
                    #
                    # Ключ предупреждения отдельный от общего: иначе разовая
                    # медлительность съела бы единственный показ настоящего
                    # «недоступен», и в логе осталась бы не та причина.
                    warn_once(
                        f"{NAME}-slow",
                        f"Opus 5 не успел за {TIMEOUT_SECONDS:.0f} с — можно переспросить",
                    )
                    return ""
            else:
                _timeouts.pop(rejection_key, None)
            mark_failure(NAME)
            warn_once(NAME, f"Opus 5 недоступен ({error}) — спрашиваю следующего")
            return ""

    return run


def _is_timeout(error: Exception) -> bool:
    """Сервис жив, но не успел, — в отличие от оборванной связи.

    Тип, как и у _is_permanent, берём по имени класса: модуль обязан работать и
    когда пакета anthropic нет вовсе, а `except anthropic.APITimeoutError`
    требовал бы его импортировать здесь.
    """
    names = {cls.__name__ for cls in type(error).__mro__}
    return bool(names & {"APITimeoutError", "Timeout", "TimeoutError", "ReadTimeout"})


def _is_permanent(error: Exception) -> bool:
    """Отказ, который не пройдёт сам: не тот ключ, нет прав, нет такой модели.

    Тип берём мягко, через имена классов, а не `except anthropic.X`: модуль
    обязан работать и когда пакет не установлен вовсе, а на голом `status_code`
    не отличить 401 от того же 429.
    """
    status = getattr(error, "status_code", None)
    return status in (401, 403, 404)
