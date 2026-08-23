"""Рупор: отправить текст в телеграм. Больше эта штука не умеет ничего.

Границы здесь — главное, ровно как в git_tools. Модуль НЕ знает, что такое
календарь, погода и сводка; он не решает, стоит ли сообщение отправлять, и не
собирает его текст. Ему дают готовую строку, токен и адресата — он отправляет.

Ради этого он и написан отдельным файлом: тот же вызов понадобится трём
разным хозяевам, которые ничего не знают друг о друге, — облачной сводке по
расписанию, самому Джони с ПК («рендер доделался, сэр») и двустороннему боту.
Если рупор начнёт разбираться в поводах, каждый из троих придётся учить
заново.

Токен и chat_id живут в config/secrets.yaml (он в .gitignore). В журнал не
попадают никогда: ни в тексте ошибки, ни в отладке — телеграм кладёт токен
прямо в URL, поэтому URL целиком тоже нельзя логировать, и ниже он ни разу не
уходит в logger.
"""

import json
import logging
import urllib.error
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

_API = "https://api.telegram.org"

# Телеграм режет сообщения длиннее 4096 знаков — причём не обрезает, а
# отвечает ошибкой, то есть сообщение пропадает целиком. Режем сами и с
# запасом: считает он в UTF-16, а не в символах питона, и на эмодзи цифры
# расходятся.
_MAX_CHARS = 3800

# Сеть здесь не в горячем пути: сводку никто не ждёт у микрофона. Но и вечно
# висеть нельзя — в GitHub Actions зависший запрос сожрёт минуты бесплатного
# тарифа.
_TIMEOUT = 15.0


class NotifyError(Exception):
    """Телеграм не принял сообщение либо не ответил."""


def from_secrets(secrets: dict) -> tuple[str, str]:
    """(токен, chat_id) из уже прочитанного secrets.yaml. Пустые строки, если нет.

    Отдельной функцией, чтобы имена ключей были записаны ровно в одном месте:
    их читают и Джони с ПК, и облачный digest.py, и настроечная команда ниже.
    """
    token = str(secrets.get("telegram_bot_token") or "").strip()
    chat_id = str(secrets.get("telegram_chat_id") or "").strip()
    return token, chat_id


def _call(token: str, method: str, payload: dict | None = None, timeout: float = _TIMEOUT) -> dict:
    """Вызов метода Bot API. Возвращает поле result, бросает NotifyError.

    Почему urllib, а не requests, которым пользуется весь остальной проект:
    этот модуль обязан работать и в облаке, где ставить зависимости — лишний
    шаг сборки на каждый запуск сводки. Здесь нужен один POST с JSON, и это
    ровно то, что стандартная библиотека умеет без обвязки. Обёртка
    http_client нужна была ради браузерного User-Agent для Cloudflare — у
    api.telegram.org такой проблемы нет.
    """
    url = f"{_API}/bot{token}/{method}"
    data = json.dumps(payload or {}).encode("utf-8")
    request = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # Тело ошибки телеграма объясняет причину по-человечески
        # («chat not found», «bot was blocked by the user»), а сам HTTPError —
        # только код. URL в сообщение не подставляем: в нём токен.
        detail = ""
        try:
            detail = json.loads(exc.read().decode("utf-8")).get("description", "")
        except Exception:
            pass
        raise NotifyError(f"телеграм отказал ({exc.code}): {detail or 'без объяснений'}") from exc
    except urllib.error.URLError as exc:
        raise NotifyError(f"нет связи с телеграмом: {exc.reason}") from exc
    except (OSError, ValueError) as exc:
        raise NotifyError(f"телеграм ответил непонятно: {exc}") from exc
    if not body.get("ok"):
        raise NotifyError(f"телеграм отказал: {body.get('description', 'без объяснений')}")
    return body.get("result")


def split_text(text: str, limit: int = _MAX_CHARS) -> list[str]:
    """Длинный текст — на куски по границам строк, а не по счётчику знаков.

    Резать посреди слова в сводке событий значит разорвать пополам строку
    события; читать это на телефоне неприятно. По строкам разрез незаметен
    вовсе, а строка длиннее лимита (такого у нас нет, но чужой текст с шага
    «долгая штука доделалась» может прийти любым) режется уже жёстко —
    иначе она не уедет никогда.
    """
    chunks: list[str] = []
    current = ""
    for line in text.split("\n"):
        while len(line) > limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:limit])
            line = line[limit:]
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def send(text: str, *, token: str, chat_id: str, timeout: float = _TIMEOUT) -> int:
    """Отправить текст. Возвращает число отправленных сообщений (обычно 1).

    Разметку (parse_mode) НЕ включаем, и это осознанно. В сводку попадает то,
    что человек надиктовал голосом, а телеграм на непарный `_` или `*` внутри
    Markdown отвечает 400 — то есть напоминание про врача не приедет вовсе
    из-за подчёркивания в тексте. Простой текст доезжает всегда. Понадобится
    жирный шрифт — включать parse_mode="HTML" и экранировать текст, но это
    отдельное решение с ценой, а не значение по умолчанию.
    """
    text = (text or "").strip()
    if not text:
        raise NotifyError("пустое сообщение отправлять некуда")
    if not token:
        raise NotifyError("нет telegram_bot_token в secrets.yaml")
    if not chat_id:
        raise NotifyError("нет telegram_chat_id в secrets.yaml (см. python -m johnny.notify --setup)")
    sent = 0
    for chunk in split_text(text):
        _call(token, "sendMessage", {"chat_id": chat_id, "text": chunk}, timeout)
        sent += 1
    logger.info("В телеграм отправлено сообщений: %d", sent)
    return sent


def try_send(text: str, *, token: str, chat_id: str, timeout: float = _TIMEOUT) -> bool:
    """То же самое, но сбой — в журнал, а не исключением.

    Для тех, кто зовёт рупор попутно: Джони не должен падать посреди ответа
    из-за того, что у телефона пропал интернет. Облачной сводке наоборот
    нужен send(): там упавший запуск видно в GitHub, а проглоченная ошибка
    означала бы, что сводка молча не приходит неделями.
    """
    try:
        send(text, token=token, chat_id=chat_id, timeout=timeout)
        return True
    except NotifyError as exc:
        logger.warning("Не отправил в телеграм: %s", exc)
        return False


def find_chats(token: str, timeout: float = _TIMEOUT) -> list[tuple[str, str]]:
    """Кто уже писал боту: [(chat_id, как зовут)], свежие первыми.

    Единственный способ узнать свой chat_id — написать боту хоть что-нибудь:
    телеграм не даёт боту искать людей, человек всегда пишет первым. Отсюда и
    порядок настройки: написать /start в @JohnnyMaster_bot, потом позвать это.

    ВАЖНО про очередь. getUpdates УНОСИТ сообщения: пришедший за ними второй
    раз уже ничего не найдёт. Пока бот только пишет, это неважно, но на шаге
    «двусторонний бот» за очередью должен ходить ровно один хозяин — иначе
    команды разделятся между ним и этой функцией случайным образом. Поэтому
    offset здесь не двигаем: сообщения остаются в очереди для будущего
    потребителя, и настройку можно повторять сколько угодно раз.
    """
    updates = _call(token, "getUpdates", {"timeout": 0}, timeout) or []
    seen: dict[str, str] = {}
    for update in reversed(updates):
        message = update.get("message") or update.get("edited_message") or {}
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        if chat_id is None:
            continue
        name = " ".join(
            part
            for part in (chat.get("first_name"), chat.get("last_name"), chat.get("title"))
            if part
        ) or chat.get("username") or "без имени"
        seen.setdefault(str(chat_id), name)
    return list(seen.items())


def _setup(config_dir: str) -> int:
    """Разовая настройка из командной строки: найти chat_id и вписать в secrets.

    Живёт здесь, а не в отдельном скрипте, потому что знает ровно то же, что и
    рупор: имена двух ключей и метод getUpdates.
    """
    from pathlib import Path

    import yaml

    path = Path(config_dir) / "secrets.yaml"
    secrets = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    secrets = secrets or {}
    token, chat_id = from_secrets(secrets)
    if not token:
        print("Нет telegram_bot_token в config/secrets.yaml — вписать и повторить.")
        return 1
    try:
        chats = find_chats(token)
    except NotifyError as exc:
        print(f"Не смог спросить телеграм: {exc}")
        return 1
    if not chats:
        print(
            "Боту ещё никто не писал. Открыть в телеграме @JohnnyMaster_bot,\n"
            "нажать «Start» (или написать любое слово) и запустить это снова."
        )
        return 1
    found, name = chats[0]
    if len(chats) > 1:
        print("Боту писали несколько человек, беру самого свежего:")
        for cid, who in chats:
            print(f"  {cid}  {who}")
    if chat_id == found:
        print(f"chat_id уже записан: {found} ({name})")
    else:
        # Дописываем строкой, а не yaml.dump: дамп переписал бы файл целиком и
        # стёр из него все комментарии (ровно этим болеет panel.py — не
        # повторять). Ключ короткий, кавычек не требует.
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"\ntelegram_chat_id: \"{found}\"   # {name}\n")
        print(f"Записал telegram_chat_id: {found} ({name})")
    try:
        send("Джони на связи. Сюда будут приходить сводки.", token=token, chat_id=found)
        print("Проверочное сообщение отправлено — посмотреть в телефоне.")
    except NotifyError as exc:
        print(f"chat_id нашёлся, но отправка не удалась: {exc}")
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover — разовая ручная настройка
    import sys
    from pathlib import Path

    default_config = Path(__file__).resolve().parent.parent / "config"
    if "--setup" in sys.argv:
        raise SystemExit(_setup(str(default_config)))
    print("Использование: python -m johnny.notify --setup")
    raise SystemExit(2)
