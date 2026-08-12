import json

from .http_client import in_cooldown, mark_failure, post, post_stream, warn_once

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


class StreamBroken(Exception):
    """Поток оборвался НЕ по своей воле (HTTP-ошибка, разрыв сети).

    Отдельный тип, а не пустой возврат: нормально закончившийся поток и
    оборванный требуют разного поведения. После нормального конца Джони просто
    замолкает, после обрыва — обязан сказать, что связь пропала, иначе
    половина ответа неотличима от целого.
    """


def make_streaming_provider(api_key: str, model: str):
    """Функция prompt -> генератор кусков текста по мере генерации.

    Второй вход в тот же сервис, а не замена make_provider: тому по-прежнему
    нужен ответ ЦЕЛИКОМ (коррекция команд, цепочки шагов, punctuate), и
    собирать его из кусков там незачем.
    """

    def run(prompt: str):
        if in_cooldown("groq"):
            # Тот же щит, что в make_provider: недавно уже не достучались,
            # не ждём заново полный таймаут на этот же вопрос.
            return
        try:
            response = post_stream(
                _URL,
                {"Authorization": f"Bearer {api_key}"},
                {
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.3,
                    "max_tokens": 200,
                    "stream": True,
                },
                _TIMEOUT,
            )
            if response.status_code != 200:
                raise RuntimeError(f"Groq HTTP {response.status_code}: {response.text[:120]}")
            for line in response.iter_lines(decode_unicode=True):
                # Пустые строки и комментарии (": keep-alive") — легальная
                # часть SSE, а не сбой.
                if not line or not line.startswith("data:"):
                    continue
                payload = line[len("data:"):].strip()
                if payload == "[DONE]":
                    return
                try:
                    piece = json.loads(payload)["choices"][0]["delta"].get("content") or ""
                except (json.JSONDecodeError, KeyError, IndexError):
                    # Обрезанный на разрыве кусок JSON не должен ронять всё:
                    # уже озвученное останется, остаток дочитаем.
                    continue
                if piece:
                    yield piece
        except Exception as error:
            mark_failure("groq")
            warn_once("groq", f"Groq оборвал поток ({error})")
            raise StreamBroken(str(error)) from error

    return run
