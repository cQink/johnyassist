"""Перевод меню столовой. Сеть подставная — настоящая ходила бы в Groq за деньги.

Главное свойство здесь — не качество перевода, а то, что сводка переживает
ЛЮБОЙ ответ модели: пустой, многословный, оборванный, вместе с упавшей сетью.
Непереведённое меню человек прочтёт, а сорвавшуюся из-за обеда сводку — нет.
"""
import urllib.error

import pytest

from johnny import translate
from johnny.translate import dish_to_russian, make_asker

БЛЮДО = "Chili con carne med ris"


def test_перевод_подставляется():
    assert dish_to_russian(БЛЮДО, ask=lambda p: "Чили кон карне с рисом") == "Чили кон карне с рисом"


def test_блюдо_уходит_в_промпт():
    промпты = []
    dish_to_russian(БЛЮДО, ask=lambda p: промпты.append(p) or "перевод")
    assert БЛЮДО in промпты[0]


def test_без_переводчика_остаётся_оригинал():
    # Ключа Groq может не быть — это нормальное состояние, а не поломка.
    assert dish_to_russian(БЛЮДО, ask=None) == БЛЮДО


def test_пустое_блюдо_никуда_не_ходит():
    def нельзя(_):
        raise AssertionError("переводить нечего")

    assert dish_to_russian("", ask=нельзя) == ""


@pytest.mark.parametrize("ответ", ["", "   ", "\n\n"])
def test_пустой_ответ_откатывается_к_оригиналу(ответ):
    """Пустой content — не выдумка, а живой случай.

    gpt-oss тратит на рассуждение тот же лимит, что и на ответ: с маленьким
    max_tokens приходит finish_reason=length и ПУСТОЙ content. Поймано
    26.08.2026 на первом же запуске.
    """
    assert dish_to_russian(БЛЮДО, ask=lambda p: ответ) == БЛЮДО


def test_болтливый_ответ_отбрасывается():
    многословно = "Конечно! Это блюдо переводится как «чили кон карне с рисом», " * 3
    assert dish_to_russian(БЛЮДО, ask=lambda p: многословно) == БЛЮДО


def test_пояснение_после_перевода_отсекается():
    # Модель любит добавить строку от себя; берём первую непустую.
    ответ = "Чили кон карне с рисом\n\n(популярное мексиканское блюдо)"
    assert dish_to_russian(БЛЮДО, ask=lambda p: ответ) == "Чили кон карне с рисом"


def test_кавычки_снимаются():
    assert dish_to_russian(БЛЮДО, ask=lambda p: '«Чили с рисом»') == "Чили с рисом"


def test_длинное_но_правдоподобное_название_проходит():
    # Кириллица длиннее латиницы, порог обязан это переживать.
    длинное = "Куриные наггетсы с кисло-сладким соусом и рисом"
    assert dish_to_russian("Chicken nuggets med sötsur sås & ris", ask=lambda p: длинное) == длинное


def test_упавшая_сеть_не_роняет_сводку():
    def падает(_):
        raise urllib.error.URLError("нет сети")

    assert dish_to_russian(БЛЮДО, ask=падает) == БЛЮДО


def test_неожиданный_ответ_api_не_роняет_сводку():
    def кривой(_):
        raise KeyError("choices")

    assert dish_to_russian(БЛЮДО, ask=кривой) == БЛЮДО


# -- сборка запроса --

def test_без_ключа_переводчика_нет():
    assert make_asker("") is None and make_asker(None) is None


def test_запрос_уходит_с_браузерным_user_agent(monkeypatch):
    """Groq за Cloudflare: без этого заголовка приходит 403.

    В http_client.py про это оставлена записка, но здесь свой запрос на urllib,
    и первый же запуск наступил на те же грабли — перевод молча откатывался к
    шведскому. Проверяем заголовок, а не поведение Cloudflare.
    """
    захвачено = {}

    class Ответ:
        def read(self):
            return '{"choices": [{"message": {"content": "готово"}}]}'.encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def подделка(request, timeout=None):
        захвачено["headers"] = {k.lower(): v for k, v in request.headers.items()}
        захвачено["body"] = __import__("json").loads(request.data.decode())
        return Ответ()

    monkeypatch.setattr(translate.urllib.request, "urlopen", подделка)
    assert make_asker("ключ")("промпт") == "готово"
    assert "Mozilla" in захвачено["headers"]["user-agent"]
    assert захвачено["headers"]["authorization"] == "Bearer ключ"


def test_потолок_токенов_щедрый_и_рассуждение_короткое(monkeypatch):
    # Оба параметра — следствие живого замера, а не вкус: с max_tokens 60
    # ответ приходил пустым, потому что лимит съедало рассуждение.
    захвачено = {}

    class Ответ:
        def read(self):
            return b'{"choices": [{"message": {"content": "ok"}}]}'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(
        translate.urllib.request, "urlopen",
        lambda request, timeout=None: (
            захвачено.update(__import__("json").loads(request.data.decode())) or Ответ()
        ),
    )
    make_asker("ключ")("промпт")
    assert захвачено["max_tokens"] >= 200
    assert захвачено["reasoning_effort"] == "low"


def test_точка_в_конце_срезается():
    # Остальные строки сводки без точки; одна с точкой выглядит опечаткой.
    assert dish_to_russian(БЛЮДО, ask=lambda p: "острое рагу с фасолью.") == "острое рагу с фасолью"


def test_многоточие_не_трогаем():
    assert dish_to_russian(БЛЮДО, ask=lambda p: "рагу и ещё что-то...") == "рагу и ещё что-то..."


def test_промпт_требует_объяснения_а_не_транслитерации():
    """Пин на решение владельца (26.08.2026): «кон карне» ему ничего не говорит.

    Первая версия промпта велела сохранять узнаваемые названия и выдавала
    «чили кон карне с рисом» — из такой строки не понять даже, мясное блюдо
    или нет. Откат этой формулировки не покраснел бы ничем, кроме этого теста.
    """
    промпты = []
    dish_to_russian(БЛЮДО, ask=lambda p: промпты.append(p) or "рагу")
    assert "НЕ транслитерируй" in промпты[0]
    assert "из чего блюдо" in промпты[0]


def test_длинное_объяснение_проходит():
    # Объяснение длиннее названия по определению: «Kebabgryta med ris» это
    # 18 знаков, а «тушёное мясо кебаб с рисом» уже 27.
    длинное = "запечённая хрустящая рыба с холодным травяным соусом и картофелем"
    assert dish_to_russian("Sprödbakad fisk med kall örtsås & potatis", ask=lambda p: длинное) == длинное


def test_короткое_название_с_длинным_объяснением_тоже_проходит():
    assert dish_to_russian("Kebabgryta med ris",
                           ask=lambda p: "тушёное мясо кебаб с рисом") == "тушёное мясо кебаб с рисом"
