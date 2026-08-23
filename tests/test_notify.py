"""Рупор в телеграм. Сеть здесь подставная — настоящая отправка в тестах
означала бы сообщение в телефон человека на каждый прогон pytest.
"""
import io
import json
import urllib.error

import pytest

from johnny import notify
from johnny.notify import NotifyError, from_secrets, send, split_text, try_send

ТОКЕН = "123:СЕКРЕТ"


class Ответ:
    def __init__(self, body: dict):
        self._body = json.dumps(body).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


@pytest.fixture
def телеграм(monkeypatch):
    """Записывает вызовы и отвечает «ок». Возвращает список (url, payload)."""
    вызовы = []

    def подделка(request, timeout=None):
        вызовы.append((request.full_url, json.loads(request.data.decode("utf-8"))))
        return Ответ({"ok": True, "result": {"message_id": 1}})

    monkeypatch.setattr(notify.urllib.request, "urlopen", подделка)
    return вызовы


def test_отправка_уходит_с_текстом_и_адресатом(телеграм):
    assert send("привет", token=ТОКЕН, chat_id="42") == 1
    url, payload = телеграм[0]
    assert url.endswith("/sendMessage")
    assert payload == {"chat_id": "42", "text": "привет"}


def test_разметка_не_включается(телеграм):
    # parse_mode на непарном подчёркивании даёт 400, и напоминание не приедет
    # вовсе. Простой текст доезжает всегда.
    send("файл отчет_итог_2026.txt готов *важно*", token=ТОКЕН, chat_id="42")
    assert "parse_mode" not in телеграм[0][1]


def test_длинный_текст_уезжает_несколькими_сообщениями(телеграм):
    строка = "событие " * 100
    текст = "\n".join([строка] * 20)
    отправлено = send(текст, token=ТОКЕН, chat_id="42")
    assert отправлено == len(телеграм) > 1
    assert all(len(payload["text"]) <= 3800 for _, payload in телеграм)


def test_режем_по_строкам_а_не_посреди_слова():
    куски = split_text("\n".join(f"строка {i}" for i in range(10)), limit=30)
    assert all(not кусок.startswith(" ") for кусок in куски)
    assert "\n".join(куски).replace("\n", "") == "".join(f"строка {i}" for i in range(10))


def test_одна_строка_длиннее_предела_всё_равно_уезжает():
    куски = split_text("а" * 100, limit=30)
    assert len(куски) == 4 and "".join(куски) == "а" * 100


def test_пустое_сообщение_никуда_не_шлём(телеграм):
    with pytest.raises(NotifyError):
        send("   ", token=ТОКЕН, chat_id="42")
    assert телеграм == []


def test_без_токена_понятная_ошибка():
    with pytest.raises(NotifyError, match="telegram_bot_token"):
        send("привет", token="", chat_id="42")


def test_без_chat_id_ошибка_подсказывает_как_чинить():
    with pytest.raises(NotifyError, match="--setup"):
        send("привет", token=ТОКЕН, chat_id="")


def test_телеграм_объяснил_отказ_и_объяснение_видно(monkeypatch):
    def отказ(request, timeout=None):
        raise urllib.error.HTTPError(
            request.full_url, 400, "Bad Request", {},
            io.BytesIO(json.dumps({"description": "chat not found"}).encode()),
        )

    monkeypatch.setattr(notify.urllib.request, "urlopen", отказ)
    with pytest.raises(NotifyError, match="chat not found"):
        send("привет", token=ТОКЕН, chat_id="42")


def test_токен_не_попадает_в_текст_ошибки(monkeypatch):
    # Телеграм кладёт токен прямо в URL, поэтому URL нельзя ни логировать, ни
    # подставлять в сообщение об ошибке — иначе он уедет в johnny.log.
    def отказ(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, io.BytesIO(b""))

    monkeypatch.setattr(notify.urllib.request, "urlopen", отказ)
    with pytest.raises(NotifyError) as ошибка:
        send("привет", token=ТОКЕН, chat_id="42")
    assert "СЕКРЕТ" not in str(ошибка.value)


def test_нет_сети_это_понятная_ошибка_а_не_трассировка(monkeypatch):
    def нет_сети(request, timeout=None):
        raise urllib.error.URLError("имя не разрешается")

    monkeypatch.setattr(notify.urllib.request, "urlopen", нет_сети)
    with pytest.raises(NotifyError, match="нет связи"):
        send("привет", token=ТОКЕН, chat_id="42")


def test_ответ_ok_false_это_отказ(monkeypatch):
    monkeypatch.setattr(
        notify.urllib.request, "urlopen",
        lambda request, timeout=None: Ответ({"ok": False, "description": "bot was blocked"}),
    )
    with pytest.raises(NotifyError, match="bot was blocked"):
        send("привет", token=ТОКЕН, chat_id="42")


def test_try_send_проглатывает_сбой(monkeypatch):
    # Джони не должен падать посреди ответа из-за пропавшего у телефона интернета.
    monkeypatch.setattr(
        notify.urllib.request, "urlopen",
        lambda request, timeout=None: Ответ({"ok": False, "description": "нет"}),
    )
    assert try_send("привет", token=ТОКЕН, chat_id="42") is False


def test_try_send_на_успехе(телеграм):
    assert try_send("привет", token=ТОКЕН, chat_id="42") is True


# -- секреты и настройка --

def test_ключи_читаются_из_секретов():
    assert from_secrets({"telegram_bot_token": " т ", "telegram_chat_id": 42}) == ("т", "42")


def test_пустые_секреты_дают_пустые_строки():
    assert from_secrets({}) == ("", "")
    assert from_secrets({"telegram_bot_token": None, "telegram_chat_id": None}) == ("", "")


def test_поиск_чатов_отдаёт_свежие_первыми(monkeypatch):
    updates = {
        "ok": True,
        "result": [
            {"message": {"chat": {"id": 1, "first_name": "Старый"}}},
            {"message": {"chat": {"id": 2, "first_name": "Стас", "last_name": "Ф"}}},
        ],
    }
    monkeypatch.setattr(
        notify.urllib.request, "urlopen", lambda request, timeout=None: Ответ(updates)
    )
    assert notify.find_chats(ТОКЕН) == [("2", "Стас Ф"), ("1", "Старый")]


def test_поиск_чатов_не_уносит_очередь(monkeypatch):
    # getUpdates со сдвигом offset УДАЛЯЕТ сообщения. Пока бот только пишет,
    # это незаметно, но на двустороннем боте съело бы команды человека.
    вызовы = []

    def подделка(request, timeout=None):
        вызовы.append(json.loads(request.data.decode("utf-8")))
        return Ответ({"ok": True, "result": []})

    monkeypatch.setattr(notify.urllib.request, "urlopen", подделка)
    notify.find_chats(ТОКЕН)
    assert "offset" not in вызовы[0]


def test_поиск_чатов_переживает_обновление_без_сообщения(monkeypatch):
    updates = {"ok": True, "result": [{"my_chat_member": {}}, {"message": {}}]}
    monkeypatch.setattr(
        notify.urllib.request, "urlopen", lambda request, timeout=None: Ответ(updates)
    )
    assert notify.find_chats(ТОКЕН) == []
