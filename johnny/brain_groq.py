from .http_client import in_cooldown, mark_failure, post, warn_once

_URL = "https://api.groq.com/openai/v1/chat/completions"
_TIMEOUT = 10.0


def make_provider(api_key: str, model: str):
    """Функция prompt -> текст ответа. Пустая строка = не смог, пусть отвечает
    следующий провайдер (claude -p)."""

    def run(prompt: str) -> str:
        if in_cooldown("groq"):
            # Недавно уже не достучались до Groq: не ждём заново 10 секунд
            # таймаута на этот же вопрос, сразу отдаём ход claude -p.
            return ""
        try:
            response = post(
                _URL,
                {"Authorization": f"Bearer {api_key}"},
                {
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    # Ответ либо строгий JSON, либо короткая реплика вслух —
                    # фантазия тут не нужна.
                    "temperature": 0.3,
                    # Промпт просит 1-3 коротких предложения, но ничто это не
                    # гарантирует: без предела ответ может растянуться и не
                    # уложиться в 10-секундный бюджет синтеза речи.
                    "max_tokens": 200,
                },
                _TIMEOUT,
            )
            if response.status_code != 200:
                raise RuntimeError(f"Groq HTTP {response.status_code}: {response.text[:120]}")
            content = response.json()["choices"][0]["message"]["content"]
            if not content:
                raise RuntimeError(f"Groq вернул пустой content: {content}")
            return content
        except Exception as error:
            mark_failure("groq")
            warn_once("groq", f"Groq недоступен ({error}) — спрашиваю claude")
            return ""

    return run
