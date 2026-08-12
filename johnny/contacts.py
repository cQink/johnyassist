"""Кто такой «Гоша»: произнесённое имя → человек из people.yaml.

Знает только про имена и строки. Ничего не знает ни про Discord, ни про
дерево доступности — поэтому проверяется обычными тестами без живого клиента.
"""

from .morph import stem_phrase

_DM_MARK = "(личное сообщение)"
_INPUT_MARK = "Написать"


def resolve(said: str, people: dict) -> dict | None:
    """Человек по произнесённому имени в любом падеже. None — не знаем такого."""
    key = stem_phrase(said)
    for name, info in people.items():
        aliases = [str(a) for a in info.get("aliases", [])] or [str(name)]
        if any(stem_phrase(alias) == key for alias in aliases):
            return info
    return None


def split_message(argument: str, people: dict):
    """«гоше привет как дела» → (человек, «привет как дела»).

    Имя отделяется по ДЛИННЕЙШЕМУ совпавшему префиксу, а не по первому слову:
    иначе двусловное имя («дядя вася») распалось бы, и остаток фразы уехал бы
    в текст сообщения. None — имени не узнали или текста не осталось.
    """
    words = argument.split()
    for size in range(len(words) - 1, 0, -1):
        person = resolve(" ".join(words[:size]), people)
        if person is not None:
            return person, " ".join(words[size:])
    return None


def discord_name(person: dict) -> str:
    """Отображаемое имя человека в Discord. Пустая строка — поля нет.

    Обращаться к person["discord"] напрямую нельзя: эти функции работают
    предикатами внутри discord_ui.find, а он оборачивает try/except только
    чтение свойств элемента. KeyError от опечатки в people.yaml долетел бы до
    общего обработчика, и Джони сказал бы невнятное «Не смог выполнить
    команду» вместо понятной причины.
    """
    return str(person.get("discord") or "").strip()


def matches_dm(element_name: str, person: dict) -> bool:
    """Ссылка «не прочитано, Гречка (личное сообщение)» — про этого человека?

    Сравниваем вхождением, а не равенством: Discord дописывает рядом с именем
    статус («не прочитано», «Не беспокоить»), и приписка меняется сама собой.
    """
    name = discord_name(person)
    if not name or _DM_MARK not in element_name:
        return False
    return name.lower() in element_name.lower()


def matches_input(element_name: str, person: dict) -> bool:
    """Поле «Написать @Гречка» — точно того самого человека и только его?

    Это последняя проверка перед вводом текста: поле само называет адресата,
    и сверка с ним — единственное, что физически мешает сообщению уйти не в
    тот чат. Поэтому сверка не по вхождению, а буквальная:

    * имя стоит сразу после «@» — иначе адресатом сойдёт кто угодно, чьё имя
      просто упомянуто в названии поля;
    * после имени ничего нет — «Написать @Гречка, Ярик» это ГРУППОВОЙ чат
      (сообщение прочитают посторонние), а «Написать @Твикс 12» — другой
      человек, чьё имя начинается с имени нашего.

    Регистр не важен нигде: и «Написать», и само имя сверяются в нижнем.
    """
    target = discord_name(person).lower()
    if not target:
        return False
    name = element_name.strip().lower()
    mark = _INPUT_MARK.lower()
    if not name.startswith(mark):
        return False
    return name[len(mark) :].strip() == "@" + target
