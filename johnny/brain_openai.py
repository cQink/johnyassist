"""GPT-5.6 Sol по API-ключу — второй «доп мозг», взаимозаменяемый с Opus 5.

Какой из двух отвечает, решает `strong_brain` в settings.yaml. Смысл двух
слотов не в том, чтобы опрашивать обоих (это двойная цена за один ответ), а в
том, чтобы модель была сменной: владелец собирается заменить её локальной, и
менять для этого код не придётся — достаточно строки в настройках.

Почему здесь `http_client`, а рядом, в `brain_anthropic`, официальный SDK.
Клиент Anthropic — сознательное исключение (форма Messages API часто меняется,
и типизированный клиент ловит это раньше человека). У OpenAI мы зовём
`/v1/chat/completions` — тот же самый JSON-POST, что уже ходит к Groq, и ради
него тянуть в зависимости целый SDK нечем оправдать.

Форма запроса отличается от Groq двумя полями, и оба легко перепутать:

  - `max_completion_tokens`, а не `max_tokens`: у рассуждающих моделей потолок
    считает размышление ВМЕСТЕ с видимым ответом, поэтому параметр и переназван.
  - `reasoning_effort` — рычаг цены и задержки. Уровни none | minimal | low |
    medium | high | xhigh | max.

`temperature` тут, в отличие от Opus 5, всё ещё принимается — но не передаётся:
менять разброс ответа у модели, которая и так думает перед ответом, значит
крутить ручку, эффект которой мы не мерили.
"""

from __future__ import annotations

from .http_client import in_cooldown, mark_failure, post, warn_once

_URL = "https://api.openai.com/v1/chat/completions"

MODEL = "gpt-5.6-sol"

NAME = "gpt"

# 20 секунд — тот же потолок, что у Opus 5: столько человек готов молча ждать
# ответа голосом, дальше он думает, что ассистент завис.
TIMEOUT_SECONDS = 20.0

# Размышление и ответ делят один бюджет, поэтому сотнями токенов, как у Groq,
# тут не обойтись: они уйдут на размышление, а видимый текст придёт пустым.
MAX_COMPLETION_TOKENS = 4096

EFFORT = "low"

# Ключи, которые сервис не принял (401/403/404). Такое само не чинится, а
# cooldown вернул бы нас к тому же отказу через минуту, снова заняв 20 секунд
# человеческого ожидания.
#
# Помним ПАРУ (ключ, адрес): ключ посредника на официальном адресе отвечает 401,
# а на своём работает — запомнив один ключ, мы выключили бы и рабочую пару.
_rejected: set[tuple[str, str]] = set()


def _endpoint(base_url: str) -> str:
    """Адрес /v1/chat/completions. Пусто = официальный api.openai.com.

    Посредники (у владельца — agentrouter.org) дают КОРЕНЬ, а не полный путь:
    так же, как его принимает SDK Anthropic по соседству. Поэтому хвост
    дописываем сами, а уже дописанный не удваиваем — иначе адрес молча
    превратится в `.../v1/chat/completions/v1/chat/completions`.
    """
    root = (base_url or "").strip().rstrip("/")
    if not root:
        return _URL
    if root.endswith("/chat/completions"):
        return root
    if root.endswith("/v1"):
        return f"{root}/chat/completions"
    return f"{root}/v1/chat/completions"


def make_provider(api_key: str, model: str = MODEL, base_url: str = ""):
    """Функция prompt -> текст ответа. Пустая строка = не смог, пусть отвечает
    следующий провайдер — тот же контракт, что у brain_groq.make_provider."""
    url = _endpoint(base_url)
    rejection_key = (api_key, base_url)

    def run(prompt: str) -> str:
        if rejection_key in _rejected or in_cooldown(NAME):
            return ""
        try:
            response = post(
                url,
                {"Authorization": f"Bearer {api_key}"},
                {
                    "model": model or MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_completion_tokens": MAX_COMPLETION_TOKENS,
                    "reasoning_effort": EFFORT,
                },
                TIMEOUT_SECONDS,
            )
            if response.status_code in (401, 403, 404):
                _rejected.add(rejection_key)
                warn_once(
                    NAME,
                    f"Ключ GPT не принят (HTTP {response.status_code}) — модель выключена до перезапуска",
                )
                return ""
            if response.status_code != 200:
                raise RuntimeError(f"HTTP {response.status_code}: {response.text[:120]}")
            content = response.json()["choices"][0]["message"]["content"]
            if not content or not content.strip():
                # Пустой content при HTTP 200 — обычно весь бюджет ушёл на
                # размышление либо сработал отказ. Это ошибка ответа, а не сети.
                raise RuntimeError("пустой content")
            return content
        except Exception as error:
            mark_failure(NAME)
            warn_once(NAME, f"GPT недоступен ({error}) — спрашиваю следующего")
            return ""

    return run
