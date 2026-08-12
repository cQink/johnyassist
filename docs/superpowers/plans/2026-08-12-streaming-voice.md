# Стриминг голоса (Groq по токенам, Fish по фразам) — план реализации

> **Для исполнителя:** выполнять по задачам, сверху вниз. Шаги отмечены
> чекбоксами. В проекте **нет git** (`Is a git repository: false`), поэтому
> вместо коммита в конце каждой задачи — прогон всего набора тестов и отметка
> в `current_work_plan.md`. Набор в 1058 тестов и есть здешняя защита от
> регрессий.

**Цель:** сократить паузу до первого слова разговорного ответа: Groq отдаёт
текст по токенам, Fish поёт его по фразам, а филлер закрывает остаток паузы.

**Архитектура:** конвейер из трёх стадий и двух очередей. Нарезчик режет поток
Groq на фразы, поток синтеза готовит mp3 следующей фразы, пока поток
воспроизведения играет предыдущую. Потокового аудио нет и не будет: MCI играет
только готовые файлы (см. спеку).

**Стек:** `requests` (уже есть, добавляется только `stream=True`), `queue`,
`threading`, существующие `tts_cache`/`tts_fish`/`sounds`. **Ни одной новой
зависимости.**

**Спека:** [../specs/2026-08-12-streaming-voice-design.md](../specs/2026-08-12-streaming-voice-design.md)

## Global Constraints

- Python 3.14, Windows. Комментарии, сообщения и docstring — по-русски, как во
  всём проекте.
- Ни одной новой строки в `requirements.txt`.
- Прогон набора: `"D:\Python\python.exe" -m pytest -q --ignore=tests/test_face_index_connector.py`
  (модуль лиц не собирается — на машине нет `cv2`, это было до нас).
- Тесты объясняют ПОЧЕМУ проверяемое поведение важно, а не пересказывают код.
- `streaming: false` обязан возвращать в точности сегодняшнее поведение на
  каждом шаге плана. Это единственный откат.
- Конвейер работает только на голосе fish. На edge/pyttsx3 стриминг выключается
  сам — синтез и проигрывание там слиты внутри `edge_tts` и врозь не
  разбираются.

## Структура файлов

| Файл | Ответственность | Задача |
|---|---|---|
| `johnny/http_client.py` | `post_stream()` — POST со `stream=True` | 1 |
| `johnny/brain_groq.py` | `make_streaming_provider()`, `StreamBroken` | 2 |
| `johnny/tts_cache.py` | разделение синтеза и проигрывания | 3 |
| `johnny/say_stream.py` (новый) | нарезка, решение о ветке, филлеры, конвейер | 4, 5, 6 |
| `johnny/speaker.py` | `StreamVoice`, `Speaker.say_stream()` | 7 |
| `johnny/config.py`, `config/settings.yaml` | настройки стриминга и филлеры | 8 |
| `johnny/brain.py`, `johnny/app.py` | разговорная ветка через конвейер | 9 |

---

## Task 1: Потоковый POST

**Files:**
- Modify: `johnny/http_client.py` (дописать после `post`, строка 44)
- Test: `tests/test_http_client.py` (дописать в конец)

**Interfaces:**
- Produces: `post_stream(url: str, headers: dict, payload: dict, timeout: float) -> requests.Response`

- [ ] **Шаг 1: тест**

Дописать в конец `tests/test_http_client.py`:

```python
def test_post_stream_asks_requests_not_to_buffer(monkeypatch):
    """Без stream=True requests скачивает ответ целиком перед возвратом — то
    есть SSE-поток Groq пришёл бы одним куском в конце, и стриминга бы не было
    вовсе, причём молча: код выглядел бы рабочим."""
    seen = {}

    def fake_post(url, **kwargs):
        seen.update(kwargs)
        return "ответ"

    monkeypatch.setattr(http_client.requests, "post", fake_post)
    assert http_client.post_stream("http://x", {}, {"a": 1}, 5.0) == "ответ"
    assert seen["stream"] is True


def test_post_stream_keeps_the_browser_user_agent(monkeypatch):
    """Groq стоит за Cloudflare, который режет клиентов без User-Agent, —
    потоковому запросу заголовок нужен ровно так же, как обычному."""
    seen = {}

    def fake_post(url, **kwargs):
        seen.update(kwargs)
        return "ответ"

    monkeypatch.setattr(http_client.requests, "post", fake_post)
    http_client.post_stream("http://x", {"Authorization": "Bearer k"}, {}, 5.0)
    assert seen["headers"]["User-Agent"] == http_client.USER_AGENT
    assert seen["headers"]["Authorization"] == "Bearer k"
```

Если в начале файла ещё нет импорта — добавить `import johnny.http_client as http_client`.

- [ ] **Шаг 2: убедиться, что падает**

Запуск: `"D:\Python\python.exe" -m pytest tests/test_http_client.py -q`
Ожидание: `AttributeError: module 'johnny.http_client' has no attribute 'post_stream'`

- [ ] **Шаг 3: реализация** — в `johnny/http_client.py`, сразу после `post`:

```python
def post_stream(url: str, headers: dict, payload: dict, timeout: float) -> requests.Response:
    """POST, ответ которого читается по мере поступления (SSE у Groq).

    Отличие от post ровно одно — stream=True, и оно принципиально: без него
    requests скачивает тело целиком прежде, чем вернуть управление, то есть
    весь смысл стриминга пропадает МОЛЧА — код при этом выглядит рабочим.

    timeout здесь — время до ПЕРВОГО байта, а не на весь ответ: длинный поток
    законно идёт дольше, и общего потолка на него нет.
    """
    return requests.post(
        url,
        headers={**headers, "User-Agent": USER_AGENT},
        json=payload,
        timeout=timeout,
        stream=True,
    )
```

- [ ] **Шаг 4: тесты зелёные**

Запуск: `"D:\Python\python.exe" -m pytest tests/test_http_client.py -q`
Ожидание: все тесты файла проходят, включая два новых.

---

## Task 2: Стриминговый провайдер Groq

**Files:**
- Modify: `johnny/brain_groq.py`
- Test: `tests/test_brain_groq.py` (дописать в конец)

**Interfaces:**
- Consumes: `http_client.post_stream` из задачи 1
- Produces: `StreamBroken(Exception)`,
  `make_streaming_provider(api_key: str, model: str) -> Callable[[str], Iterator[str]]`

Существующий `make_provider` НЕ трогаем: он нужен коррекции команд, цепочкам
шагов и `punctuate`, где ответ нужен целиком и озвучивать его никто не будет.

- [ ] **Шаг 1: тест**

Дописать в конец `tests/test_brain_groq.py`:

```python
class FakeStream:
    """Подставной ответ requests со stream=True."""

    def __init__(self, lines, status_code=200, text=""):
        self._lines = lines
        self.status_code = status_code
        self.text = text

    def iter_lines(self, decode_unicode=False):
        for line in self._lines:
            yield line


def _sse(*pieces):
    """Строки SSE в формате OpenAI, как их шлёт Groq."""
    out = []
    for piece in pieces:
        out.append('data: {"choices":[{"delta":{"content":"%s"}}]}' % piece)
    out.append("data: [DONE]")
    return out


def test_streaming_yields_pieces_as_they_arrive(monkeypatch):
    monkeypatch.setattr(
        brain_groq, "post_stream", lambda *a, **k: FakeStream(_sse("При", "вет"))
    )
    provider = brain_groq.make_streaming_provider("k", "m")
    assert list(provider("вопрос")) == ["При", "вет"]


def test_streaming_asks_groq_for_a_stream(monkeypatch):
    """Без "stream": true в теле Groq ответит обычным JSON, и iter_lines отдаст
    его одной строкой — разбор молча вернёт пустоту."""
    seen = {}

    def fake(url, headers, payload, timeout):
        seen.update(payload)
        return FakeStream(_sse("да"))

    monkeypatch.setattr(brain_groq, "post_stream", fake)
    list(brain_groq.make_streaming_provider("k", "m")("вопрос"))
    assert seen["stream"] is True


def test_streaming_stops_on_done_marker(monkeypatch):
    """После [DONE] Groq может держать соединение — не остановившись, Джони
    молчал бы до сетевого таймаута уже ПОСЛЕ готового ответа."""
    lines = _sse("готово") + ['data: {"choices":[{"delta":{"content":"лишнее"}}]}']
    monkeypatch.setattr(brain_groq, "post_stream", lambda *a, **k: FakeStream(lines))
    assert list(brain_groq.make_streaming_provider("k", "m")("в")) == ["готово"]


def test_streaming_skips_keepalive_and_broken_lines(monkeypatch):
    """SSE легально содержит пустые строки и комментарии, а последний кусок
    приходит обрезанным при разрыве. Падать на них нельзя — уже озвученное
    останется, а остаток дочитаем."""
    lines = ["", ": keep-alive", 'data: {"choices":[{"delta":{}}]}',
             "data: {битый", 'data: {"choices":[{"delta":{"content":"ок"}}]}',
             "data: [DONE]"]
    monkeypatch.setattr(brain_groq, "post_stream", lambda *a, **k: FakeStream(lines))
    assert list(brain_groq.make_streaming_provider("k", "m")("в")) == ["ок"]


def test_streaming_in_cooldown_yields_nothing(monkeypatch):
    """Тот же щит, что у make_provider: не ждать сетевой таймаут на каждую
    фразу, когда интернет только что пропал."""
    monkeypatch.setattr(brain_groq, "in_cooldown", lambda key: True)
    called = []
    monkeypatch.setattr(brain_groq, "post_stream", lambda *a, **k: called.append(1))
    assert list(brain_groq.make_streaming_provider("k", "m")("в")) == []
    assert called == []


def test_streaming_raises_stream_broken_on_http_error(monkeypatch):
    """Обрыв обязан отличаться от нормального конца потока: после нормального
    конца Джони молчит, после обрыва — говорит, что связь пропала."""
    monkeypatch.setattr(
        brain_groq, "post_stream",
        lambda *a, **k: FakeStream([], status_code=429, text="rate limit"),
    )
    with pytest.raises(brain_groq.StreamBroken):
        list(brain_groq.make_streaming_provider("k", "m")("в"))


def test_streaming_failure_starts_cooldown(monkeypatch):
    marked = []
    monkeypatch.setattr(brain_groq, "mark_failure", lambda key: marked.append(key))
    monkeypatch.setattr(brain_groq, "in_cooldown", lambda key: False)

    def boom(*a, **k):
        raise OSError("сеть пропала")

    monkeypatch.setattr(brain_groq, "post_stream", boom)
    with pytest.raises(brain_groq.StreamBroken):
        list(brain_groq.make_streaming_provider("k", "m")("в"))
    assert marked == ["groq"]
```

В начале файла должны быть `import pytest` и `import johnny.brain_groq as brain_groq` — добавить, если их нет.

- [ ] **Шаг 2: убедиться, что падает**

Запуск: `"D:\Python\python.exe" -m pytest tests/test_brain_groq.py -q`
Ожидание: `AttributeError: module 'johnny.brain_groq' has no attribute 'make_streaming_provider'`

- [ ] **Шаг 3: реализация** — заменить первую строку `johnny/brain_groq.py`
      на импорт с `post_stream` и дописать модуль в конец:

Первая строка файла становится такой:

```python
import json

from .http_client import in_cooldown, mark_failure, post, post_stream, warn_once
```

В конец файла:

```python
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
```

- [ ] **Шаг 4: тесты зелёные**

Запуск: `"D:\Python\python.exe" -m pytest tests/test_brain_groq.py -q`
Ожидание: все тесты файла проходят, старые в том числе.

---

## Task 3: Разделить синтез и проигрывание

**Files:**
- Modify: `johnny/tts_cache.py`
- Test: `tests/test_tts_fish.py` (дописать в конец)

**Interfaces:**
- Produces: `Prepared(path: str | None, temporary: bool)`,
  `make_cached_prepare(synthesize, *, provider, voice_key, cache_dir) -> Callable[[str], Prepared]`
- `make_cached_tts` сохраняет прежнюю сигнатуру и прежнее поведение.

**Зачем.** Сегодня `say(text)` синтезирует И играет одним вызовом. Конвейеру это
не годится: синтез следующей фразы обязан идти, ПОКА играет предыдущая, иначе
пауза между фразами равна времени синтеза и весь выигрыш съедается на второй же
фразе. Разделяем на `prepare(text) -> Prepared` и проигрывание.

Рефакторинг **поведение-сохраняющий**: все существующие тесты `test_tts_fish.py`
и `test_tts_local.py` обязаны остаться зелёными без единой правки.

- [ ] **Шаг 1: тест**

Дописать в конец `tests/test_tts_fish.py`:

```python
def test_prepare_returns_a_file_instead_of_playing_it(tmp_path):
    """Ядро конвейера: синтез отдаёт ФАЙЛ, а играет его кто-то другой и позже.
    Слитые вместе, они не дают синтезировать следующую фразу во время
    проигрывания предыдущей."""
    prepare = tts_cache.make_cached_prepare(
        lambda text: b"ID3" + b"x" * 100,
        provider="fish", voice_key="v", cache_dir=tmp_path,
    )
    prepared = prepare("привет")
    assert prepared.path is not None
    assert Path(prepared.path).exists()


def test_prepare_reuses_the_cache_for_short_phrases(tmp_path):
    """Филлеры короткие и звучат сотнями раз: второй раз они обязаны браться
    с диска, иначе филлер сам станет задержкой, которую призван скрыть."""
    calls = []

    def synth(text):
        calls.append(text)
        return b"ID3" + b"x" * 100

    prepare = tts_cache.make_cached_prepare(
        synth, provider="fish", voice_key="v", cache_dir=tmp_path
    )
    first = prepare("Секунду")
    second = prepare("Секунду")
    assert calls == ["Секунду"]
    assert first.path == second.path
    assert first.temporary is False


def test_prepare_marks_long_answers_as_temporary(tmp_path):
    """Ответы модели дословно не повторяются — их файлы удаляются после
    проигрывания, иначе кеш зарастает мусором."""
    prepare = tts_cache.make_cached_prepare(
        lambda text: b"ID3" + b"x" * 100,
        provider="fish", voice_key="v", cache_dir=tmp_path,
    )
    prepared = prepare("а" * (tts_cache.MAX_CACHED_CHARS + 1))
    assert prepared.temporary is True


def test_prepare_reports_failure_instead_of_raising(tmp_path):
    """Отказ синтеза в середине ответа не должен ронять конвейер: остаток
    доигрывает запасной голос, а решает это зовущий."""
    def boom(text):
        raise RuntimeError("fish отказал")

    prepare = tts_cache.make_cached_prepare(
        boom, provider="fish-test-fail", voice_key="v", cache_dir=tmp_path
    )
    assert prepare("привет").path is None


def test_prepare_rejects_a_body_that_is_not_mp3(tmp_path):
    """Cloudflare отдаёт 200 со страницей вместо mp3 — попав в кеш, она
    проигрывалась бы вечно."""
    prepare = tts_cache.make_cached_prepare(
        lambda text: b"<html>not mp3</html>",
        provider="fish-test-html", voice_key="v", cache_dir=tmp_path,
    )
    assert prepare("привет").path is None
```

В начале файла должны быть `from pathlib import Path` и
`import johnny.tts_cache as tts_cache` — добавить, если их нет.

- [ ] **Шаг 2: убедиться, что падает**

Запуск: `"D:\Python\python.exe" -m pytest tests/test_tts_fish.py -q`
Ожидание: `AttributeError: module 'johnny.tts_cache' has no attribute 'make_cached_prepare'`

- [ ] **Шаг 3: реализация** — в `johnny/tts_cache.py` добавить импорт
      `dataclass` к существующим импортам:

```python
from dataclasses import dataclass
```

Затем вставить ПЕРЕД `make_cached_tts`:

```python
@dataclass
class Prepared:
    """Результат синтеза: файл, готовый к проигрыванию.

    path=None — синтез не удался, и решать, что делать (запасной голос),
    будет зовущий: у конвейера и у обычного say разные правильные ответы.
    temporary=True — файл временный, удалить после проигрывания.
    """

    path: str | None
    temporary: bool = False


def make_cached_prepare(synthesize, *, provider: str, voice_key: str, cache_dir):
    """prepare(text) -> Prepared: синтез в файл, БЕЗ проигрывания.

    Разделение синтеза и проигрывания нужно конвейеру стриминга: следующая
    фраза синтезируется, пока играет предыдущая. Слитые вместе (см.
    make_cached_tts, который построен на этой же функции), они делают паузу
    между фразами равной времени синтеза.
    """

    def prepare(text: str) -> Prepared:
        if not text:
            return Prepared(None)
        cached = cache_path(cache_dir, text, voice_key) if len(text) <= MAX_CACHED_CHARS else None
        if cached is not None and cached.exists():
            return Prepared(str(cached), temporary=False)

        if in_cooldown(provider):
            return Prepared(None)

        try:
            data = synthesize(text)
        except Exception as error:
            mark_failure(provider)
            warn_once(provider, f"{provider} недоступен ({error}) — озвучиваю запасным голосом")
            return Prepared(None)

        if not looks_like_mp3(data):
            # Провайдер ответил 200 с телом, которое не mp3 (страница
            # Cloudflare, JSON про тариф). В кеш это класть нельзя.
            mark_failure(provider)
            warn_once(provider, f"{provider} отдал не mp3 — озвучиваю запасным голосом")
            return Prepared(None)

        path = cached or Path(tempfile.gettempdir()) / (
            f"johnny_{provider}_{os.getpid()}_{random.randint(0, 1_000_000)}.mp3"
        )
        try:
            store(path, data)
        except Exception as error:
            warn_once(f"{provider}-store", f"Не смог сохранить синтез ({error})")
            return Prepared(None)
        if cached is not None:
            trim(cache_dir)
        return Prepared(str(path), temporary=cached is None)

    return prepare
```

- [ ] **Шаг 4: переписать `make_cached_tts` поверх `prepare`**

Заменить тело `make_cached_tts` целиком на:

```python
def make_cached_tts(synthesize, *, provider: str, voice_key: str, fallback, cache_dir, play=None):
    """say() поверх synthesize(text) -> bytes; при любой неудаче — fallback.

    provider — ключ для cooldown и лога (свой у каждого сервиса: отказ
    локального NeMo не должен глушить попытки к fish и наоборот).

    Синтез живёт в make_cached_prepare — тот же код обслуживает конвейер
    стриминга, где файл готовится заранее, а играется позже.
    """
    play = sounds.play_file if play is None else play
    prepare = make_cached_prepare(
        synthesize, provider=provider, voice_key=voice_key, cache_dir=cache_dir
    )

    def say(text: str) -> None:
        if not text:
            return
        prepared = prepare(text)
        if prepared.path is None:
            fallback(text)
            return
        try:
            play(prepared.path)
        except Exception as error:
            warn_once(
                f"{provider}-play", f"Ошибка при проигрывании ({error}) — озвучиваю запасным голосом"
            )
            if not prepared.temporary:
                # Файл из кеша не проигрался. Иначе, если пересинтез в
                # следующий раз тоже не удастся, битый файл остался бы под тем
                # же именем и проигрывался бы (безуспешно) вечно.
                Path(prepared.path).unlink(missing_ok=True)
            fallback(text)
        finally:
            if prepared.temporary:
                try:
                    Path(prepared.path).unlink(missing_ok=True)
                except OSError:
                    pass

    return say
```

- [ ] **Шаг 5: тесты зелёные, включая старые**

Запуск: `"D:\Python\python.exe" -m pytest tests/test_tts_fish.py tests/test_tts_local.py -q`
Ожидание: все тесты обоих файлов проходят. **Ни один старый тест не правится.**
Если старый тест упал — это регресс рефакторинга, чинить реализацию, а не тест.

- [ ] **Шаг 6: весь набор**

Запуск: `"D:\Python\python.exe" -m pytest -q --ignore=tests/test_face_index_connector.py`
Ожидание: 1058 + новые passed.

---

## Task 4: Нарезка потока на фразы и решение о ветке

**Files:**
- Create: `johnny/say_stream.py`
- Test: `tests/test_say_stream.py`

**Interfaces:**
- Produces: `cut(buffer: str, limit: int, sentences: int = 1) -> tuple[str, str]`,
  `looks_like_command(head: str) -> bool`, `DECIDE_AFTER: int`

- [ ] **Шаг 1: тест**

```python
"""Нарезка потока модели на фразы и решение «это разговор или команда».

Ошибка здесь слышна сразу: либо Джони зачитывает вслух JSON, либо ждёт конца
ответа и никакого стриминга не получается.
"""

import johnny.say_stream as say_stream


def test_cut_takes_the_first_finished_sentence():
    phrase, rest = say_stream.cut("Привет. Как дела", 120)
    assert phrase == "Привет."
    assert rest == "Как дела"


def test_cut_waits_while_the_sentence_is_unfinished():
    """Отдать полфразы в синтез — значит услышать обрыв на полуслове."""
    phrase, rest = say_stream.cut("Привет, как", 120)
    assert phrase == ""
    assert rest == "Привет, как"


def test_cut_gives_up_waiting_at_the_limit():
    """Модель иногда сыплет текст вообще без точек. Без потолка первая фраза
    дождалась бы конца ответа — то есть стриминга бы не было."""
    text = "слово " * 40
    phrase, rest = say_stream.cut(text, 60)
    assert phrase
    assert len(phrase) <= 60
    assert not phrase.endswith("сл")   # режем по границе слова, не посреди


def test_cut_can_take_two_sentences_at_once():
    """Дальше первой фразы спешить некуда: Джони уже говорит, а каждый лишний
    вызов Fish — это и деньги, и ещё одна слышимая пауза."""
    phrase, rest = say_stream.cut("Раз. Два. Три", 120, sentences=2)
    assert phrase == "Раз. Два."
    assert rest == "Три"


def test_cut_takes_what_there_is_if_asked_for_more():
    phrase, rest = say_stream.cut("Раз. Два", 120, sentences=2)
    assert phrase == "Раз."
    assert rest == "Два"


def test_cut_keeps_the_question_mark_with_the_sentence():
    phrase, _ = say_stream.cut("Правда? Да", 120)
    assert phrase == "Правда?"


def test_command_branch_is_recognised_by_a_brace():
    """Модель отвечает JSON на исправленную команду. Озвучить его — значит
    прочитать вслух {"command": "громкость 5"}."""
    assert say_stream.looks_like_command('{"command": "громкость 5"}') is True


def test_command_branch_is_recognised_by_a_fence():
    assert say_stream.looks_like_command("```json") is True


def test_ordinary_answer_is_not_a_command():
    assert say_stream.looks_like_command("Дела отлично, спасибо") is False
```

- [ ] **Шаг 2: убедиться, что падает**

Запуск: `"D:\Python\python.exe" -m pytest tests/test_say_stream.py -q`
Ожидание: `ModuleNotFoundError: No module named 'johnny.say_stream'`

- [ ] **Шаг 3: реализация** — создать `johnny/say_stream.py`:

```python
"""Конвейер озвучки: текст Groq по кускам → фразы → mp3 → колонки.

Смысл модуля — совместить три ожидания, которые сегодня идут подряд: пока
модель договаривает ответ, первая фраза уже синтезируется, а пока она играет,
синтезируется вторая. Общее время ответа от этого почти не меняется — меняется
момент, когда Джони НАЧИНАЕТ говорить, а именно он и ощущается как «долго
молчит».

Потокового аудио здесь нет: MCI играет только готовые файлы (см. sounds.py), а
декодер mp3 в память — новая зависимость. Каждая фраза остаётся отдельным mp3.
Подробности решения — docs/superpowers/specs/2026-08-12-streaming-voice-design.md
"""

import re

# Конец предложения вместе с закрывающими кавычками и скобками: «Да!» — фраза
# кончается после кавычки, а не после восклицательного знака.
_BOUNDARY = re.compile(r"[.!?…]+[\"»)\]]*")

# Сколько знаков ждём, прежде чем решить «разговор или команда». По первому
# символу решать нельзя: модель изредка предваряет JSON словами, и такой ответ
# выглядел бы разговорным.
DECIDE_AFTER = 40


def looks_like_command(head: str) -> bool:
    """Похоже ли начало потока на JSON-команду, а не на разговор.

    Решение принимается ОДИН РАЗ и не пересматривается: озвученного не вернуть,
    а метаться между ветками посреди ответа хуже, чем ошибиться один раз.
    """
    return "{" in head or "```" in head


def cut(buffer: str, limit: int, sentences: int = 1) -> tuple[str, str]:
    """Отрезать готовую к озвучке фразу. Возвращает (фраза, остаток).

    Пустая фраза = ещё рано, копим дальше. limit — потолок ожидания в знаках:
    без него текст без единой точки никогда не дошёл бы до синтеза.
    """
    ends = [match.end() for match in _BOUNDARY.finditer(buffer)]
    if ends:
        end = ends[min(sentences, len(ends)) - 1]
        return buffer[:end].strip(), buffer[end:].lstrip()
    if len(buffer) >= limit:
        # По границе слова: обрывок посреди слова слышен как заикание.
        space = buffer.rfind(" ", 0, limit)
        end = space if space > 0 else limit
        return buffer[:end].strip(), buffer[end:].lstrip()
    return "", buffer
```

- [ ] **Шаг 4: тесты зелёные**

Запуск: `"D:\Python\python.exe" -m pytest tests/test_say_stream.py -q`
Ожидание: `9 passed`

---

## Task 5: Филлеры

**Files:**
- Modify: `johnny/say_stream.py` (дописать в конец)
- Test: `tests/test_say_stream.py` (дописать в конец)

**Interfaces:**
- Produces: `Fillers(phrases: list[str])` с методом `pick() -> str`

- [ ] **Шаг 1: тест**

```python
def test_filler_returns_one_of_the_phrases():
    fillers = say_stream.Fillers(["Секунду", "Момент"])
    assert fillers.pick() in ("Секунду", "Момент")


def test_filler_never_repeats_itself_twice_in_a_row():
    """Повтор одной и той же фразы подряд слышен сразу и звучит поломкой.
    random.choice (как в _ACK_PHRASES) этого не гарантирует."""
    fillers = say_stream.Fillers(["Секунду", "Момент", "Сейчас"])
    said = [fillers.pick() for _ in range(30)]
    assert all(first != second for first, second in zip(said, said[1:]))


def test_single_filler_is_allowed_to_repeat():
    """Иначе список из одной фразы не смог бы вернуть ничего вовсе."""
    fillers = say_stream.Fillers(["Секунду"])
    assert fillers.pick() == "Секунду"
    assert fillers.pick() == "Секунду"


def test_empty_list_means_no_filler():
    """Пустой список — это способ выключить филлеры, не выключая стриминг."""
    assert say_stream.Fillers([]).pick() == ""
```

- [ ] **Шаг 2: убедиться, что падает**

Запуск: `"D:\Python\python.exe" -m pytest tests/test_say_stream.py -q`
Ожидание: `AttributeError: module 'johnny.say_stream' has no attribute 'Fillers'`

- [ ] **Шаг 3: реализация** — дописать в `johnny/say_stream.py`
      (и добавить `import random` к импортам в начале файла):

```python
class Fillers:
    """Короткие реплики, которые играют, пока готовится первая фраза.

    Именно они убирают паузу до первого слова: быстрее, чем за время синтеза,
    первую фразу не получить, а филлер после первого раза лежит в кеше и
    играет с диска мгновенно (все фразы короче tts_cache.MAX_CACHED_CHARS —
    это требование к списку, а не совпадение).
    """

    def __init__(self, phrases):
        self._phrases = [str(phrase).strip() for phrase in (phrases or []) if str(phrase).strip()]
        self._last = ""

    def pick(self) -> str:
        """Случайная фраза, но не та же, что в прошлый раз. "" = филлеров нет."""
        if not self._phrases:
            return ""
        choices = [phrase for phrase in self._phrases if phrase != self._last]
        # Список из одной фразы: повтор разрешён, иначе выбирать не из чего.
        chosen = random.choice(choices or self._phrases)
        self._last = chosen
        return chosen
```

- [ ] **Шаг 4: тесты зелёные**

Запуск: `"D:\Python\python.exe" -m pytest tests/test_say_stream.py -q`
Ожидание: `13 passed`

---

## Task 6: Конвейер

**Files:**
- Modify: `johnny/say_stream.py` (дописать в конец)
- Test: `tests/test_say_stream.py` (дописать в конец)

**Interfaces:**
- Consumes: `cut`, `looks_like_command`, `Fillers`, `DECIDE_AFTER` из задач 4–5
- Produces: `StreamResult(text: str, spoken: bool, broken: bool)`,
  `Voice(prepare, play, fallback)`,
  `consume(chunks, voice, *, fillers, first_limit=120, cancel=None) -> StreamResult`

`Voice` — три функции, которые конвейер зовёт, и больше он про голос ничего не
знает: `prepare(text) -> Prepared`, `play(path) -> None`,
`fallback(text) -> None` (синтезирует И играет запасным голосом).

- [ ] **Шаг 1: тест**

```python
import threading
import types

import pytest


def _voice(fail_after=None):
    """Подставной голос: помнит, что синтезировали и что играли."""
    state = types.SimpleNamespace(prepared=[], played=[], fallback=[])

    def prepare(text):
        state.prepared.append(text)
        if fail_after is not None and len(state.prepared) > fail_after:
            return types.SimpleNamespace(path=None, temporary=True)
        return types.SimpleNamespace(path=f"/tmp/{len(state.prepared)}.mp3", temporary=True)

    def play(path):
        state.played.append(path)

    def fallback(text):
        state.fallback.append(text)

    return say_stream.Voice(prepare=prepare, play=play, fallback=fallback), state


def test_conversation_is_spoken_phrase_by_phrase():
    voice, state = _voice()
    result = say_stream.consume(
        iter(["Привет. ", "Как дела? ", "Всё хорошо."]),
        voice, fillers=say_stream.Fillers([]),
    )
    assert result.spoken is True
    assert result.text == "Привет. Как дела? Всё хорошо."
    assert state.prepared == ["Привет.", "Как дела?", "Всё хорошо."]
    assert len(state.played) == 3


def test_json_answer_is_never_spoken():
    """Сторож: без него Джони однажды зачитает вслух {"command": ...}."""
    voice, state = _voice()
    result = say_stream.consume(
        iter(['{"command": ', '"громкость 5"}']),
        voice, fillers=say_stream.Fillers(["Секунду"]),
    )
    assert result.spoken is False
    assert state.prepared == []
    assert state.fallback == []
    assert result.text == '{"command": "громкость 5"}'


def test_filler_plays_before_the_first_phrase():
    """Филлер — единственное, что звучит в первые секунды: синтез первой фразы
    быстрее не станет."""
    voice, state = _voice()
    say_stream.consume(
        iter(["Дела отличные, спасибо. "]),
        voice, fillers=say_stream.Fillers(["Секунду"]),
    )
    assert state.prepared[0] == "Секунду"


def test_filler_is_silent_on_the_command_branch():
    """Человек попросил действие, а не разговор: «секундочку» перед выполнением
    команды — лишний звук."""
    voice, state = _voice()
    say_stream.consume(
        iter(['{"command": "громкость 5"}']),
        voice, fillers=say_stream.Fillers(["Секунду"]),
    )
    assert "Секунду" not in state.prepared


def test_next_phrase_is_synthesised_while_the_previous_one_plays():
    """Ядро всей затеи. Если синтез ждёт конца проигрывания, пауза между
    фразами равна времени синтеза и выигрыш исчезает на второй же фразе."""
    order = []
    playing = threading.Event()

    def prepare(text):
        order.append(f"синтез:{text}")
        return types.SimpleNamespace(path=f"/tmp/{text}.mp3", temporary=True)

    def play(path):
        order.append(f"играю:{path}")
        playing.set()
        # Держим «проигрывание», пока синтез второй фразы не успеет начаться.
        import time
        time.sleep(0.15)

    voice = say_stream.Voice(prepare=prepare, play=play, fallback=lambda text: None)
    say_stream.consume(
        iter(["Раз. ", "Два. "]), voice, fillers=say_stream.Fillers([])
    )
    # Синтез «Два.» обязан стоять в списке РАНЬШЕ, чем закончилось
    # проигрывание «Раз.» — то есть раньше «играю:/tmp/Два..mp3».
    assert order.index("синтез:Два.") < order.index("играю:/tmp/Два..mp3")


def test_fish_failure_speaks_the_remainder_in_one_piece():
    """Пофразное переключение голоса туда-обратно звучит как поломка. Остаток
    доигрывается одним куском запасным голосом."""
    voice, state = _voice(fail_after=1)
    result = say_stream.consume(
        iter(["Раз. ", "Два. ", "Три."]), voice, fillers=say_stream.Fillers([])
    )
    assert result.spoken is True
    assert state.fallback == ["Два. Три."]


def test_broken_stream_is_announced_out_loud():
    """Молчание после половины ответа неотличимо от законченного ответа, и
    человек не переспросит."""
    def chunks():
        yield "Начал отвечать. "
        raise say_stream.StreamBroken("сеть пропала")

    voice, state = _voice()
    result = say_stream.consume(chunks(), voice, fillers=say_stream.Fillers([]))
    assert result.broken is True
    assert result.spoken is True
    assert state.fallback and "связь" in state.fallback[-1].lower()


def test_broken_stream_before_any_speech_stays_silent():
    """Ничего не успели сказать — пусть отвечает следующий провайдер, а не
    Джони с извинениями."""
    def chunks():
        raise say_stream.StreamBroken("сеть пропала")
        yield ""

    voice, state = _voice()
    result = say_stream.consume(chunks(), voice, fillers=say_stream.Fillers([]))
    assert result.broken is True
    assert result.spoken is False
    assert state.fallback == []


def test_stop_breaks_the_whole_pipeline_not_just_the_current_phrase():
    """«Стоп» обязан отменить и то, что ещё не синтезировано: иначе Джони
    договаривает уже отменённый ответ, а Fish берёт за это деньги."""
    cancel = threading.Event()
    cancel.set()
    voice, state = _voice()
    result = say_stream.consume(
        iter(["Раз. ", "Два. ", "Три."]), voice,
        fillers=say_stream.Fillers([]), cancel=cancel,
    )
    assert state.prepared == []
    assert state.played == []
    assert result.spoken is False


def test_empty_stream_is_not_an_error():
    voice, state = _voice()
    result = say_stream.consume(iter([]), voice, fillers=say_stream.Fillers([]))
    assert result.text == ""
    assert result.spoken is False
    assert result.broken is False


def test_short_json_without_a_dot_is_still_not_spoken():
    """Ответ {"command": "громкость 5"} — 26 знаков и ни одной точки, то есть
    решение о ветке внутри цикла принято НЕ БУДЕТ. Без решения после цикла
    хвост ушёл бы в озвучку, и Джони зачитал бы JSON вслух."""
    voice, state = _voice()
    result = say_stream.consume(
        iter(['{"command": "громкость 5"}']), voice, fillers=say_stream.Fillers([])
    )
    assert result.spoken is False
    assert state.prepared == []


def test_module_does_not_log_what_it_speaks():
    """Содержимое ответа не должно оседать на диске: history.log открывается
    кнопкой в панели и попадает на скриншоты. Сторож на весь модуль — снять
    его можно только осознанно."""
    import inspect

    assert "logger" not in inspect.getsource(say_stream)
```

- [ ] **Шаг 2: убедиться, что падает**

Запуск: `"D:\Python\python.exe" -m pytest tests/test_say_stream.py -q`
Ожидание: `AttributeError: module 'johnny.say_stream' has no attribute 'Voice'`

- [ ] **Шаг 3: реализация** — дописать в `johnny/say_stream.py`.
      К импортам в начале файла добавить `import queue`, `import threading`,
      `from dataclasses import dataclass` и
      `from .brain_groq import StreamBroken  # noqa: F401 — переэкспорт`:

```python
# Сколько фраз может ждать своей очереди. Предел нужен не ради памяти: без него
# нарезчик убежит вперёд и насинтезирует (за деньги, у Fish) фразы, которые
# «стоп» отменит через секунду.
_QUEUE_LIMIT = 3

_BROKEN_PHRASE = "Связь оборвалась, договорить не могу"


@dataclass
class StreamResult:
    """Чем кончился конвейер.

    text — весь накопленный текст (он же уходит в разбор JSON, если ветка
    оказалась командной). spoken — звучал ли голос: зовущему нельзя произносить
    ответ ВТОРОЙ раз. broken — поток оборвался, а не закончился.
    """

    text: str
    spoken: bool
    broken: bool = False


@dataclass
class Voice:
    """Три функции, которыми конвейер пользуется, и больше он про голос ничего
    не знает: prepare(text) -> Prepared, play(path), fallback(text).

    Именно эта граница делает конвейер тестируемым без сети, без Fish и без
    звуковой карты.
    """

    prepare: object
    play: object
    fallback: object


def _stopped(cancel) -> bool:
    return cancel is not None and cancel.is_set()


def consume(chunks, voice, *, fillers, first_limit: int = 120, cancel=None) -> StreamResult:
    """Провести поток кусков текста через нарезку, синтез и проигрывание.

    Вызывающий поток работает нарезчиком, синтез и проигрывание идут своими
    потоками — иначе следующая фраза не готовится во время проигрывания
    предыдущей, а ради этого всё и затевалось.
    """
    phrases: queue.Queue = queue.Queue(maxsize=_QUEUE_LIMIT)
    audio: queue.Queue = queue.Queue(maxsize=_QUEUE_LIMIT)
    # Фразы, до которых синтез не добрался из-за отказа Fish: их договорит
    # запасной голос ОДНИМ куском, а не пофразно — иначе голос менялся бы
    # туда-обратно на границах фраз, и это звучит как поломка.
    leftover: list[str] = []
    played_anything = threading.Event()

    def synth_loop():
        degraded = False
        while True:
            text = phrases.get()
            if text is None:
                audio.put(None)
                return
            if _stopped(cancel):
                continue
            if degraded:
                leftover.append(text)
                continue
            prepared = voice.prepare(text)
            if prepared.path is None:
                degraded = True
                leftover.append(text)
                continue
            # Текст едет вместе с файлом: если файл не проиграется, договорить
            # эту фразу запасным голосом можно, только зная её текст.
            audio.put((text, prepared))

    def play_loop():
        while True:
            item = audio.get()
            if item is None:
                return
            text, prepared = item
            if _stopped(cancel):
                continue
            try:
                voice.play(prepared.path)
                played_anything.set()
            except Exception:
                # Проигрывание отвалилось — эту фразу и остаток договорит
                # запасной голос, как и при отказе синтеза.
                leftover.append(text)
            finally:
                if getattr(prepared, "temporary", False):
                    try:
                        Path(prepared.path).unlink(missing_ok=True)
                    except OSError:
                        pass

    synth = threading.Thread(target=synth_loop, name="say-stream-synth", daemon=True)
    player = threading.Thread(target=play_loop, name="say-stream-play", daemon=True)
    synth.start()
    player.start()

    collected: list[str] = []
    buffer = ""
    decided = False
    command_branch = False
    spoken = False
    broken = False
    sentences = 1

    try:
        for piece in chunks:
            if _stopped(cancel):
                break
            collected.append(piece)
            buffer += piece
            if not decided:
                # Решаем один раз: набралось DECIDE_AFTER знаков или пришла
                # первая законченная фраза — что раньше.
                phrase, _ = cut(buffer, first_limit)
                if len(buffer) < DECIDE_AFTER and not phrase:
                    continue
                decided = True
                command_branch = looks_like_command(buffer[:DECIDE_AFTER])
                if not command_branch:
                    filler = fillers.pick()
                    if filler:
                        phrases.put(filler)
            if command_branch:
                # Командная ветка: копим молча до конца, озвучивать нечего.
                continue
            while True:
                phrase, buffer = cut(buffer, first_limit, sentences)
                if not phrase:
                    break
                phrases.put(phrase)
                spoken = True
                # Дальше первой фразы спешить некуда — Джони уже говорит, а
                # каждый лишний вызов Fish это и деньги, и слышимая пауза.
                sentences = 2
    except StreamBroken:
        broken = True

    if not decided and buffer.strip():
        # Поток кончился, а решения так и не было: ответ оказался короче
        # DECIDE_AFTER и без единой точки. Ровно так выглядит короткий JSON
        # ({"command": "громкость 5"} — 26 знаков), поэтому решить ОБЯЗАНЫ и
        # здесь. Без этой ветки хвост уходил бы в озвучку, и Джони зачитывал
        # бы вслух JSON — то, против чего стоит сторож №1 в спеке.
        decided = True
        command_branch = looks_like_command(buffer[:DECIDE_AFTER])

    if not command_branch and buffer.strip() and not _stopped(cancel):
        phrases.put(buffer.strip())
        spoken = True

    phrases.put(None)
    synth.join()
    player.join()

    text = "".join(collected)
    if _stopped(cancel):
        return StreamResult(text=text, spoken=False, broken=broken)

    tail = " ".join(part for part in leftover if part).strip()
    if tail:
        voice.fallback(tail)
    if broken and spoken:
        voice.fallback(_BROKEN_PHRASE)
    return StreamResult(text=text, spoken=spoken, broken=broken)
```

К импортам в начале файла добавить также `from pathlib import Path`.

- [ ] **Шаг 4: тесты зелёные**

Запуск: `"D:\Python\python.exe" -m pytest tests/test_say_stream.py -q`
Ожидание: `25 passed`

- [ ] **Шаг 5: весь набор**

Запуск: `"D:\Python\python.exe" -m pytest -q --ignore=tests/test_face_index_connector.py`
Ожидание: ни одного упавшего.

---

## Task 7: Голос для конвейера и `Speaker.say_stream`

**Files:**
- Modify: `johnny/speaker.py`
- Test: `tests/test_speaker.py` (дописать в конец)

**Interfaces:**
- Consumes: `say_stream.Voice`, `say_stream.consume`, `tts_cache.make_cached_prepare`
- Produces: `Speaker.say_stream(chunks, cancel=None) -> say_stream.StreamResult | None`
  (None = конвейер недоступен, зовущий идёт старым путём)

**Почему конвейер только на fish.** У edge-Дмитрия синтез и проигрывание слиты
внутри `edge_tts.Communicate(...).save()` и врозь не разбираются, у pyttsx3 файла
нет вовсе. Значит на них `say_stream` возвращает None, и работает сегодняшний
путь. Обещать стриминг там, где его нет, — то же, за что в этом проекте
выброшены команды «откуда эта картинка» и «найди это лицо».

- [ ] **Шаг 1: тест**

```python
def test_say_stream_is_unavailable_without_fish(monkeypatch):
    """На edge-голосе синтез и проигрывание неразделимы — конвейер обязан
    честно сказать «не могу», а не изображать стриминг."""
    settings = types.SimpleNamespace(
        response_mode="voice", tts_provider="edge", tts_voice="ru-RU-DmitryNeural",
        tts_volume=1.0, fish_model_id="", tts_local_url="", tts_voice_id="",
        tts_style="", streaming=True, streaming_fillers=[], streaming_first_chunk=120,
    )
    speaker = speaker_module.make_speaker(settings, secrets={})
    assert speaker.say_stream(iter(["привет"])) is None


def test_say_stream_is_unavailable_when_streaming_is_off(monkeypatch):
    """Выключатель обязан возвращать РОВНО прежнее поведение — это
    единственный откат, если стриминг окажется хуже."""
    settings = types.SimpleNamespace(
        response_mode="voice", tts_provider="fish", tts_voice="ru-RU-DmitryNeural",
        tts_volume=1.0, fish_model_id="voice-1", tts_local_url="", tts_voice_id="",
        tts_style="", streaming=False, streaming_fillers=["Секунду"],
        streaming_first_chunk=120,
    )
    speaker = speaker_module.make_speaker(settings, secrets={"fish_api_key": "k"})
    assert speaker.say_stream(iter(["привет"])) is None


def test_say_stream_runs_the_pipeline_when_fish_is_configured(monkeypatch):
    seen = {}

    def fake_consume(chunks, voice, **kwargs):
        seen["chunks"] = list(chunks)
        seen["fillers"] = kwargs["fillers"]
        return say_stream.StreamResult(text="привет", spoken=True)

    monkeypatch.setattr(speaker_module.say_stream, "consume", fake_consume)
    settings = types.SimpleNamespace(
        response_mode="voice", tts_provider="fish", tts_voice="ru-RU-DmitryNeural",
        tts_volume=1.0, fish_model_id="voice-1", tts_local_url="", tts_voice_id="",
        tts_style="", streaming=True, streaming_fillers=["Секунду"],
        streaming_first_chunk=120,
    )
    speaker = speaker_module.make_speaker(settings, secrets={"fish_api_key": "k"})
    result = speaker.say_stream(iter(["привет"]))
    assert result.spoken is True
    assert seen["chunks"] == ["привет"]


def test_say_stream_is_silent_in_off_mode():
    """response_mode: off значит молчать — стриминг не исключение."""
    settings = types.SimpleNamespace(
        response_mode="off", tts_provider="fish", tts_voice="ru-RU-DmitryNeural",
        tts_volume=1.0, fish_model_id="voice-1", tts_local_url="", tts_voice_id="",
        tts_style="", streaming=True, streaming_fillers=[], streaming_first_chunk=120,
    )
    speaker = speaker_module.make_speaker(settings, secrets={"fish_api_key": "k"})
    assert speaker.say_stream(iter(["привет"])) is None
```

В начале файла должны быть `import types`, `import johnny.speaker as speaker_module`
и `import johnny.say_stream as say_stream` — добавить, если их нет.

- [ ] **Шаг 2: убедиться, что падает**

Запуск: `"D:\Python\python.exe" -m pytest tests/test_speaker.py -q`
Ожидание: `AttributeError: 'Speaker' object has no attribute 'say_stream'`

- [ ] **Шаг 3: реализация** — в `johnny/speaker.py` добавить импорт в начало
      файла:

```python
from . import say_stream
```

Затем добавить метод в класс `Speaker`, сразу после `say`:

```python
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
```

Изменить `__init__` класса `Speaker`:

```python
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
```

Добавить сборку голоса для конвейера — перед `make_speaker`:

```python
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
```

И изменить конец `make_speaker`:

```python
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
```

- [ ] **Шаг 4: тесты зелёные**

Запуск: `"D:\Python\python.exe" -m pytest tests/test_speaker.py -q`
Ожидание: все тесты файла проходят, старые в том числе.

---

## Task 8: Настройки

**Files:**
- Modify: `johnny/config.py` (класс `Settings` и конструктор в `load_config`)
- Modify: `config/settings.yaml`
- Test: `tests/test_config.py` (дописать в конец)

**Interfaces:**
- Produces: `Settings.streaming: bool`, `Settings.streaming_first_chunk: int`,
  `Settings.streaming_fillers: list[str]`

- [ ] **Шаг 1: тест**

```python
def test_streaming_is_off_by_default():
    """Новое поведение включает человек: стриминг меняет то, КАК звучит Джони,
    и по умолчанию звучать он должен как вчера."""
    settings = config.Settings(
        wake_word="джони", vosk_model_path="p", response_mode="voice",
        whisper_model="medium", whisper_device="cpu",
    )
    assert settings.streaming is False
    assert settings.streaming_fillers == []


def test_streaming_settings_are_read_from_yaml(tmp_path):
    (tmp_path / "apps.yaml").write_text("{}", encoding="utf-8")
    (tmp_path / "commands.yaml").write_text("{}", encoding="utf-8")
    (tmp_path / "settings.yaml").write_text(
        "wake_word: джони\n"
        "vosk_model_path: p\n"
        "response_mode: voice\n"
        "whisper_model: medium\n"
        "whisper_device: cpu\n"
        "streaming: true\n"
        "streaming_first_chunk: 90\n"
        "streaming_fillers:\n"
        "  - Секунду\n"
        "  - Момент\n",
        encoding="utf-8",
    )
    loaded = config.load_config(tmp_path)
    assert loaded.settings.streaming is True
    assert loaded.settings.streaming_first_chunk == 90
    assert loaded.settings.streaming_fillers == ["Секунду", "Момент"]


def test_fillers_longer_than_the_cache_limit_are_dropped(tmp_path):
    """Филлер длиннее MAX_CACHED_CHARS не кешируется и синтезируется каждый
    раз — то есть сам становится задержкой, которую призван скрыть."""
    (tmp_path / "apps.yaml").write_text("{}", encoding="utf-8")
    (tmp_path / "commands.yaml").write_text("{}", encoding="utf-8")
    (tmp_path / "settings.yaml").write_text(
        "wake_word: джони\n"
        "vosk_model_path: p\n"
        "response_mode: voice\n"
        "whisper_model: medium\n"
        "whisper_device: cpu\n"
        "streaming_fillers:\n"
        "  - Секунду\n"
        f"  - {'очень длинная фраза ' * 5}\n",
        encoding="utf-8",
    )
    loaded = config.load_config(tmp_path)
    assert loaded.settings.streaming_fillers == ["Секунду"]
```

В начале файла должен быть `import johnny.config as config` — добавить, если нет.

- [ ] **Шаг 2: убедиться, что падает**

Запуск: `"D:\Python\python.exe" -m pytest tests/test_config.py -q`
Ожидание: `AttributeError: 'Settings' object has no attribute 'streaming'`

- [ ] **Шаг 3: реализация** — в `johnny/config.py`, в `class Settings`, сразу
      после `git_workdir`:

```python
    # Стриминг голоса: Groq отдаёт ответ по токенам, Fish поёт его по фразам,
    # филлер закрывает паузу до первой фразы. Выключено по умолчанию: стриминг
    # меняет то, КАК звучит Джони (между фразами слышна пауза, интонация через
    # границу не тянется), и такое решение принимает человек, а не настройка
    # по умолчанию. false = сегодняшнее поведение целиком.
    streaming: bool = False
    # Потолок ожидания первой фразы в знаках. Модель иногда сыплет текст без
    # единой точки — без потолка первая фраза дождалась бы конца ответа, то
    # есть стриминга бы не было вовсе.
    streaming_first_chunk: int = 120
    # Реплики, которые играют, ПОКА готовится первая фраза. Каждая обязана быть
    # короче tts_cache.MAX_CACHED_CHARS (40) — иначе она не попадёт в кеш,
    # будет синтезироваться каждый раз и сама станет задержкой, которую
    # призвана скрыть. Слишком длинные отбрасываются при загрузке.
    streaming_fillers: list[str] = field(default_factory=list)
```

И в `load_config`, в конструктор `Settings(...)`, рядом с остальными:

```python
        streaming=bool(s.get("streaming", False)),
        streaming_first_chunk=int(s.get("streaming_first_chunk", 120)),
        streaming_fillers=_short_fillers(s.get("streaming_fillers") or []),
```

Перед `load_config` добавить:

```python
def _short_fillers(raw) -> list[str]:
    """Филлеры, которые влезают в кеш TTS. Длинные отбрасываем молча.

    Порог здесь не для красоты: кешируются реплики короче
    tts_cache.MAX_CACHED_CHARS, а некешируемый филлер синтезируется при каждом
    ответе и сам становится той задержкой, ради устранения которой он и нужен.
    Значение продублировано числом намеренно: тянуть в конфиг импорт tts_cache
    (а с ним sounds и requests) ради одной константы дороже, чем этот комментарий.
    """
    limit = 40
    return [str(phrase).strip() for phrase in raw if 0 < len(str(phrase).strip()) <= limit]
```

- [ ] **Шаг 4: значения** — в `config/settings.yaml`, после блока `git_workdir`:

```yaml
# Стриминг голоса: Groq отдаёт ответ по токенам, Fish поёт его по фразам, а
# филлер закрывает паузу до первой фразы. Сокращает молчание ПЕРЕД ответом;
# общее время ответа почти не меняется. Работает только на голосе fish.
# false = прежнее поведение целиком.
streaming: true
streaming_first_chunk: 120     # потолок первой фразы в знаках
# Каждый филлер короче 40 знаков — иначе не попадёт в кеш и сам станет
# задержкой. Длинные отбрасываются при загрузке.
streaming_fillers:
  - Секунду
  - Сейчас
  - Минуту
  - Так
  - Сейчас скажу
  - Дайте подумать
  - Смотрю
  - Один момент
  - Сейчас разберусь
  - Так, секунду
  - Ага
  - Сейчас гляну
  - Момент
  - Понял, думаю
```

- [ ] **Шаг 5: тесты зелёные**

Запуск: `"D:\Python\python.exe" -m pytest tests/test_config.py -q`
Ожидание: все тесты файла проходят.

---

## Task 9: Разговорная ветка через конвейер

**Files:**
- Modify: `johnny/brain.py`
- Modify: `johnny/app.py:144-198` (функция `_brain_fallback`)
- Test: `tests/test_brain.py`, `tests/test_app.py` (дописать в конец)

**Interfaces:**
- Consumes: `Speaker.say_stream` из задачи 7, `brain_groq.make_streaming_provider`
  из задачи 2
- Produces: `BrainResult.spoken: bool`,
  `brain.interpret_streamed(text, commands, stream_provider, speak_stream, memory_block="") -> BrainResult | None`

Разбор ответа у обоих путей обязан быть ОДИН И ТОТ ЖЕ, иначе стриминговая ветка
со временем начнёт понимать JSON иначе, чем обычная. Поэтому сначала выделяем
общие куски из `interpret`, и только потом строим второй вход.

- [ ] **Шаг 1: тест**

Дописать в `tests/test_brain.py`:

```python
def test_streamed_answer_is_marked_as_already_spoken():
    """Главное в интеграции: реплику, прозвучавшую по ходу потока, нельзя
    произнести второй раз — человек услышал бы её дважды."""
    def speak(chunks):
        return brain.say_stream.StreamResult(text="Дела отлично", spoken=True)

    result = brain.interpret_streamed("как дела", [], lambda prompt: iter([]), speak)
    assert result.reply == "Дела отлично"
    assert result.spoken is True


def test_streamed_json_is_parsed_exactly_like_the_ordinary_path(sample_commands):
    """Командная ветка не озвучивается и разбирается тем же кодом: разойдясь,
    два разбора начали бы понимать один и тот же JSON по-разному."""
    def speak(chunks):
        return brain.say_stream.StreamResult(
            text='{"command": "громкость 5"}', spoken=False
        )

    result = brain.interpret_streamed("громкость пять", sample_commands,
                                      lambda prompt: iter([]), speak)
    assert result.spoken is False
    assert result.routed is not None
    assert result.routed.action == "system"


def test_streamed_returns_none_when_the_pipeline_is_unavailable():
    """None от say_stream значит «конвейера нет» — зовущий обязан уйти на
    обычный interpret, а не замолчать."""
    result = brain.interpret_streamed("как дела", [], lambda prompt: iter([]),
                                      lambda chunks: None)
    assert result is None


def test_streamed_returns_none_when_nothing_was_said_and_stream_broke():
    """Оборвалось до первого слова — пусть отвечает следующий провайдер."""
    def speak(chunks):
        return brain.say_stream.StreamResult(text="", spoken=False, broken=True)

    assert brain.interpret_streamed("как дела", [], lambda p: iter([]), speak) is None


def test_ordinary_interpret_still_reports_not_spoken():
    """Старый путь обязан остаться прежним: его реплику озвучивает app."""
    result = brain.interpret("привет", [], [("тест", lambda prompt: "Здравствуйте")])
    assert result.spoken is False
```

Дописать в `tests/test_app.py`:

```python
def test_streamed_reply_is_not_spoken_twice(monkeypatch):
    """Сторож интеграции: reply, уже прозвучавший в конвейере, app обязан
    пропустить молча."""
    said = []
    speaker = types.SimpleNamespace(
        say=lambda text: said.append(text),
        say_stream=lambda chunks, cancel=None: None,
        play_answer=lambda: False,
    )
    answer = brain.BrainResult(routed=None, reply="Дела отлично", provider="groq", spoken=True)
    app._speak_reply(speaker, answer, cancel=None)
    assert said == []


def test_unstreamed_reply_is_spoken(monkeypatch):
    said = []
    speaker = types.SimpleNamespace(
        say=lambda text: said.append(text),
        say_stream=lambda chunks, cancel=None: None,
        play_answer=lambda: False,
    )
    answer = brain.BrainResult(routed=None, reply="Дела отлично", provider="groq", spoken=False)
    app._speak_reply(speaker, answer, cancel=None)
    assert said == ["Дела отлично"]
```

- [ ] **Шаг 2: убедиться, что падает**

Запуск: `"D:\Python\python.exe" -m pytest tests/test_brain.py tests/test_app.py -q`
Ожидание: `AttributeError: module 'johnny.brain' has no attribute 'interpret_streamed'`

- [ ] **Шаг 3: выделить общие куски в `johnny/brain.py`**

Добавить импорт в начало файла:

```python
from . import say_stream
```

Добавить поле в `BrainResult`, после `steps`:

```python
    # Реплика уже прозвучала по ходу потока (стриминг) — зовущему её
    # произносить НЕ НАДО, иначе человек услышит ответ дважды.
    spoken: bool = field(default=False, compare=False)
```

Вставить перед `interpret` две функции, вынутые из неё без изменения логики:

```python
def _build_prompt(text: str, commands, memory_block: str) -> str:
    """Промпт для модели-корректора. Общий для обычного и потокового пути:
    разойдясь, они начали бы спрашивать модель о разном."""
    # Разрушительные команды (выключение/перезагрузка/сон) не показываем
    # модели вовсе — их нельзя предлагать угадывать даже как «исправление».
    safe_commands = [rule for rule in commands if not is_unsafe_action(rule.action, rule.template)]
    listing = "\n".join(f"- {rule.pattern}" for rule in safe_commands)
    return _PROMPT.format(
        text=text, count=len(safe_commands), commands=listing, memory_block=memory_block
    )


def _parse(raw: str, name: str, commands, spoken: bool = False) -> BrainResult:
    """Разбор ответа модели. Общий для обоих путей — по той же причине."""
    data = _extract_json(raw)
    if data is not None:
        corrected = data.get("command")
        if corrected:
            routed = route(corrected, commands)
            if routed is not None and is_unsafe_action(routed.action, routed.argument):
                routed = None
            return BrainResult(routed=routed, reply=None, provider=name, spoken=spoken)
        steps = data.get("steps")
        if steps:
            return BrainResult(
                routed=None, reply=None, provider=name, spoken=spoken,
                steps=chain.resolve([str(step) for step in steps], commands),
            )
        action = data.get("action")
        reply = data.get("reply") or None
        if action in ("open_url", "launch_app", "system"):
            argument = data.get("argument", "")
            if is_unsafe_action(action, argument):
                return BrainResult(routed=None, reply=None, provider=name, spoken=spoken)
            routed = RoutedAction(action=action, argument=argument, via=name)
            return BrainResult(routed=routed, reply=reply, provider=name, spoken=spoken)
        if action == "answer":
            return BrainResult(routed=None, reply=reply, provider=name, spoken=spoken)
        return BrainResult(routed=None, reply=None, provider=name, spoken=spoken)
    return BrainResult(routed=None, reply=raw.strip(), provider=name, spoken=spoken)
```

Заменить тело `interpret` (всё после docstring) на:

```python
    commands = commands or []
    if providers is None:
        providers = [("claude", brain_claude.run)]
    # providers=[] (в отличие от None) означает «нет ни одного провайдера» и
    # должен остаться пустым: `providers or [...]` не отличал бы пустой
    # список от None и молча уходил бы на живой claude -p.
    prompt = _build_prompt(text, commands, memory_block)

    raw, name = "", ""
    for name, provider in providers:
        raw = provider(prompt)
        if raw and raw.strip():
            break
    if not raw or not raw.strip():
        return None  # пусто у всех = моделей нет на связи
    return _parse(raw, name, commands)
```

- [ ] **Шаг 4: добавить потоковый вход** — в конец `johnny/brain.py`:

```python
def interpret_streamed(text, commands, stream_provider, speak_stream, memory_block: str = ""):
    """Как interpret, но текст идёт потоком и разговорный ответ звучит по ходу.

    speak_stream(chunks) -> say_stream.StreamResult | None. None означает «на
    этом голосе конвейера нет» — зовущий обязан уйти на обычный interpret, а не
    замолчать.

    Разбор ответа делает тот же _parse, что и обычный путь: разойдясь, два
    разбора со временем начали бы понимать один и тот же JSON по-разному.
    """
    commands = commands or []
    prompt = _build_prompt(text, commands, memory_block)
    result = speak_stream(stream_provider(prompt))
    if result is None:
        return None
    raw = (result.text or "").strip()
    if not raw:
        # Ни слова не пришло: пусть отвечает следующий провайдер обычным путём.
        return None
    if result.broken and not result.spoken:
        # Оборвалось до первого слова — то же самое, что не ответить вовсе.
        return None
    return _parse(raw, "groq", commands, spoken=result.spoken)
```

- [ ] **Шаг 5: подключить в `johnny/app.py`**

Добавить импорт стримингового провайдера в начало файла, к существующему
импорту из `.brain`:

```python
from .brain import interpret, interpret_streamed, make_providers, punctuate
from .brain_groq import make_streaming_provider
```

Вставить перед `_brain_fallback` две функции:

```python
def _speak_reply(speaker, answer, cancel) -> None:
    """Произнести реплику модели, если она ещё не прозвучала.

    Сторож против двойной озвучки: в стриминге реплика звучит ПО ХОДУ потока,
    и повторное say() дало бы человеку тот же ответ дважды.
    """
    if answer.spoken:
        return
    if cancel is None or not cancel.is_set():
        speaker.say(answer.reply)


def _streamed_answer(text, config, speaker, memory_block, cancel):
    """Ответ через конвейер или None, если конвейер недоступен.

    None здесь — нормальное состояние (выключен стриминг, не fish-голос, нет
    ключа Groq), и зовущий просто идёт прежним путём.
    """
    api_key = (config.secrets or {}).get("groq_api_key")
    if not api_key or not getattr(config.settings, "streaming", False):
        return None
    if getattr(speaker, "say_stream", None) is None:
        return None
    provider = make_streaming_provider(api_key, config.settings.groq_model)
    return interpret_streamed(
        text, config.commands, provider,
        lambda chunks: speaker.say_stream(chunks, cancel=cancel),
        memory_block=memory_block,
    )
```

В `_brain_fallback` заменить строку 154 (вызов `interpret`) на:

```python
    answer = _streamed_answer(text, config, speaker, memory_block, cancel)
    if answer is None:
        answer = interpret(
            text, config.commands, make_providers(config), memory_block=memory_block
        )
```

И заменить блок на строках 191–194 на:

```python
    if answer.reply:
        _speak_reply(speaker, answer, cancel)
        return Outcome(answer.provider or "модель")
    if answer.spoken:
        # Поток отзвучал, но связного reply не осталось (например, оборвался
        # в середине и уже сказал об этом сам). Говорить «Не понял команду»
        # поверх этого — врать: Джони как раз ответил.
        return Outcome(answer.provider or "модель")
```

- [ ] **Шаг 6: тесты зелёные**

Запуск: `"D:\Python\python.exe" -m pytest tests/test_brain.py tests/test_app.py -q`
Ожидание: все тесты обоих файлов проходят, старые в том числе.

- [ ] **Шаг 7: весь набор**

Запуск: `"D:\Python\python.exe" -m pytest -q --ignore=tests/test_face_index_connector.py`
Ожидание: 1058 + новые passed, ни одного упавшего.

---

## Task 10: Живой замер и документация

**Files:**
- Create: `tools/measure_stream.py`
- Modify: `docs/superpowers/plans/current_work_plan.md`

Моки не доказывают, что стало быстрее. Замер обязателен: разница в полсекунды
на слух не доказуема, а именно ради неё всё и делалось.

- [ ] **Шаг 1: скрипт замера** — создать `tools/measure_stream.py`:

```python
"""Замер задержки до первого слова: со стримингом и без.

Запуск: "D:\\Python\\python.exe" tools/measure_stream.py

Печатает числа, которые идут в current_work_plan.md. Без них утверждение
«стало быстрее» ничем не подкреплено.
"""

import sys
import time

sys.path.insert(0, ".")

from johnny.brain_groq import make_provider, make_streaming_provider  # noqa: E402
from johnny.config import load_config  # noqa: E402
from johnny.tts_cache import make_cached_prepare  # noqa: E402
from johnny.tts_fish import _CACHE_DIR, synthesize  # noqa: E402

QUESTION = "Расскажи в двух предложениях, чем полезен голосовой ассистент."


def main() -> None:
    config = load_config("config")
    key = (config.secrets or {}).get("groq_api_key")
    fish_key = (config.secrets or {}).get("fish_api_key")
    if not key or not fish_key:
        print("Нужны groq_api_key и fish_api_key в config/secrets.yaml")
        return

    started = time.monotonic()
    whole = make_provider(key, config.settings.groq_model)(QUESTION)
    groq_whole = time.monotonic() - started
    print(f"Groq целиком:        {groq_whole:.2f} с ({len(whole)} знаков)")

    started = time.monotonic()
    stream = make_streaming_provider(key, config.settings.groq_model)(QUESTION)
    first_piece = next(stream, "")
    groq_first = time.monotonic() - started
    for _ in stream:
        pass
    print(f"Groq первый кусок:   {groq_first:.2f} с ({first_piece!r})")

    prepare = make_cached_prepare(
        lambda text: synthesize(text, fish_key, config.settings.fish_model_id),
        provider="fish-measure", voice_key=config.settings.fish_model_id,
        cache_dir=_CACHE_DIR,
    )
    started = time.monotonic()
    prepare("Голосовой ассистент экономит время.")
    fish_one = time.monotonic() - started
    print(f"Fish одна фраза:     {fish_one:.2f} с")

    started = time.monotonic()
    prepare(whole or "Голосовой ассистент экономит время и руки.")
    fish_whole = time.monotonic() - started
    print(f"Fish весь ответ:     {fish_whole:.2f} с")

    print()
    print(f"Пауза до первого слова БЕЗ стриминга: {groq_whole + fish_whole:.2f} с")
    print(f"Пауза до первого слова СО стримингом: ~{groq_first + fish_one:.2f} с")
    print("(филлер звучит ещё раньше — сразу после решения о ветке)")


if __name__ == "__main__":
    main()
```

- [ ] **Шаг 2: прогнать замер**

Запуск: `"D:\Python\python.exe" tools/measure_stream.py`
Ожидание: четыре числа и две итоговые строки. Если «со стримингом» не меньше
«без стриминга» — **остановиться и сказать владельцу**, а не продолжать: значит
время ест не то, что мы чинили, и спека промахнулась мимо причины.

- [ ] **Шаг 3: живая проверка голосом**

Включить `streaming: true` в `config/settings.yaml`, запустить Джони и задать
вопрос, требующий развёрнутого ответа («расскажи, чем ты полезен»).

Ожидание: сначала филлер («Секунду»), затем первая фраза — заметно раньше, чем
раньше начинался ответ. Между фразами слышна небольшая пауза: это ожидаемо и
описано в спеке.

- [ ] **Шаг 4: проверить откат**

Поставить `streaming: false`, повторить тот же вопрос.
Ожидание: поведение в точности прежнее — ни филлера, ни пауз между фразами.

- [ ] **Шаг 5: проверить «стоп» на длинном ответе**

Задать вопрос, требующий длинного ответа, и сказать «стоп» посреди речи.
Ожидание: Джони замолкает и **не договаривает** оставшиеся фразы.

- [ ] **Шаг 6: записать в `current_work_plan.md`**

Отметить пункт сделанным и перечислить числами: задержка до первого токена
Groq, время Fish на одну фразу и на весь ответ, пауза до первого слова с
конвейером и без. Отдельно назвать: почему нет потокового аудио (MCI играет
файлы, декодер mp3 — новая зависимость), почему конвейер только на fish
(у edge синтез и проигрывание неразделимы), и что `tts_cache` теперь разделён
на `make_cached_prepare` и `make_cached_tts` поверх него.

---

## Что этот план сознательно НЕ делает

- **Не делает потоковое аудио.** MCI играет готовые файлы; декодер mp3 в память
  — новая зависимость. Если бесшовность понадобится, начинать надо с замены
  плеера, а не с транспорта Fish.
- **Не трогает сильную модель и `claude -p`.** У Opus задержка до первого слова
  2–7 с (после паузы — до 39 с), и стриминг её не лечит: во время паузы
  приходить нечему. Замер 2026-08-08 уже это показал.
- **Не снимает `max_tokens: 200`.** Потолок стоит осознанно и к паузе до
  первого слова отношения не имеет.
- **Не добавляет стриминг на edge-голос.** Там синтез и проигрывание слиты
  внутри `edge_tts`; обещать стриминг там, где его нет, — то же, за что из
  проекта выброшены команды «откуда эта картинка» и «найди это лицо».
