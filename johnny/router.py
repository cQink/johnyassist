import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from .config import CommandRule

# Порог похожести для нечёткого совпадения (0..1). Ниже — уходим к Claude.
_FUZZY_THRESHOLD = 0.82

# «Стоп» — зарезервированное слово прерывания (см. controller.py), а не
# обычная команда. В route()/brain ему нельзя попадать: по смыслу оно похоже
# на «останови»/«пауза» из медиа-команд (commands.yaml), и модель-корректор,
# «исправляя» ослышку, уводила его в system/play_pause — Джони переключал
# видео/музыку в ответ на простое «Джони, стоп» без активного действия.
#
# ЖИВОЙ ЛОГ (2026-08-03): Whisper несколько раз распознал слово ЛАТИНИЦЕЙ —
# "stop" — и ни разу это не сработало, пока проверялось только кириллическое
# написание. Английское слово тоже приходится держать как равноценный
# вариант, а не опечатку: Whisper выбирает язык токена независимо, это не
# исправляется настройкой словаря/порога.
_STOP_WORDS = {"стоп", "stop"}


def is_stop_word(text: str) -> bool:
    return _normalize(text) in _STOP_WORDS


def contains_stop_word(text: str) -> bool:
    """«Стоп» ГДЕ УГОДНО в тексте, а не обязательно вся фраза целиком.

    Для потоковой проверки на лету, пока Джони занят (см. controller.py,
    _check_for_stop_while_busy): там текст — это сырой, растущий частичный
    результат Vosk, где рядом почти наверняка будет ещё и имя «Джони» —
    делить его на «вся фраза = ровно слово стоп» уже не нужно и вредно,
    в отличие от is_stop_word, которым проверяется idle-фраза целиком.
    """
    words = _normalize(text).split()
    return any(word in _STOP_WORDS for word in words)

# Разрушительные действия: угадывать их нельзя. «включи компьютер» похоже на
# «выключи компьютер» на 0.97 — никакой порог их не разведёт, поэтому такие
# правила участвуют ТОЛЬКО в точном совпадении.
_UNSAFE = frozenset({"shutdown", "restart", "sleep"})

# Количественные числительные 0–100. Порядковых («первое», «второй») здесь
# сознательно НЕТ: их перевод сломал бы команды вроде «включи первое видео».
_UNITS = {
    "ноль": 0, "один": 1, "одна": 1, "два": 2, "две": 2, "три": 3, "четыре": 4,
    "пять": 5, "шесть": 6, "семь": 7, "восемь": 8, "девять": 9, "десять": 10,
    "одиннадцать": 11, "двенадцать": 12, "тринадцать": 13, "четырнадцать": 14,
    "пятнадцать": 15, "шестнадцать": 16, "семнадцать": 17, "восемнадцать": 18,
    "девятнадцать": 19,
}
_TENS = {
    "двадцать": 20, "тридцать": 30, "сорок": 40, "пятьдесят": 50,
    "шестьдесят": 60, "семьдесят": 70, "восемьдесят": 80, "девяносто": 90,
    "сто": 100,
}


def is_unsafe_action(action: str, argument: str) -> bool:
    """Действие, которое нельзя угадывать: только точное совпадение.

    Единственное определение «разрушительности» в проекте: им пользуется как
    сам роутер (см. `_is_unsafe`), так и brain — модель не должна уметь
    протащить такое действие в обход этой проверки ни через «исправленную»
    команду, ни через свободный action.

    Звонок сюда попадает не потому, что ломает компьютер, а потому, что
    необратим по-человечески: у сообщения ошибку исправляет удаление, а
    случайный звонок живому человеку отозвать нельзя. «Позвони Гоше» не
    должно вылетать из «покажи Гошу» по похожести.
    """
    if action == "discord_call":
        return True
    return action == "system" and argument in _UNSAFE


def _is_unsafe(rule: CommandRule) -> bool:
    return is_unsafe_action(rule.action, rule.template)


# Порог для литеральной части шаблонных команд. Ниже общего 0.82, потому что
# сравниваем короткие куски: «стронкость»≈«громкость» это 0.74.
_TEMPLATE_THRESHOLD = 0.72


def _words_to_digits(text: str) -> str:
    """«двадцать пять» → «25», «пять» → «5». Десятки склеиваются с единицами."""
    words = text.split()
    out: list[str] = []
    i = 0
    while i < len(words):
        word = words[i]
        if word in _TENS:
            value = _TENS[word]
            nxt = words[i + 1] if i + 1 < len(words) else ""
            if value < 100 and nxt in _UNITS and 1 <= _UNITS[nxt] <= 9:
                out.append(str(value + _UNITS[nxt]))
                i += 2
                continue
            out.append(str(value))
        elif word in _UNITS:
            out.append(str(_UNITS[word]))
        else:
            out.append(word)
        i += 1
    return " ".join(out)


@dataclass
class RoutedAction:
    action: str
    argument: str
    # Как совпало: «точно» / «похоже (0.74)» / «claude». compare=False, чтобы
    # способ не влиял на сравнение действий в тестах и в коде.
    via: str = field(default="точно", compare=False)


def _normalize(text: str) -> str:
    text = text.lower().strip()
    text = text.replace("ё", "е")             # ё=е: «вперёд»≈«вперед», «всё»≈«все»
    text = re.sub(r"[.,!?;:]+$", "", text)   # убрать хвостовую пунктуацию
    text = re.sub(r"\s+", " ", text)          # схлопнуть пробелы
    return _words_to_digits(text)             # «пять» → «5»


def _pattern_to_regex(pattern: str) -> re.Pattern:
    parts = _normalize(pattern).split("*")
    body = "(.+?)".join(re.escape(p) for p in parts)
    return re.compile("^" + body + "$")


def _fuzzy_match(norm: str, commands: list[CommandRule]) -> tuple[float, RoutedAction] | None:
    """Ближайшая команда БЕЗ шаблона (фраза целиком), если Whisper услышал криво."""
    best = None
    best_ratio = _FUZZY_THRESHOLD
    for rule in commands:
        if "*" in rule.pattern or _is_unsafe(rule):
            continue
        ratio = SequenceMatcher(None, norm, _normalize(rule.pattern)).ratio()
        if ratio >= best_ratio:
            best_ratio = ratio
            best = rule
    if best is None:
        return None
    return best_ratio, RoutedAction(
        action=best.action, argument=best.template, via=f"похоже ({best_ratio:.2f})"
    )


def _split_pattern(pattern: str) -> tuple[list[str], list[str]] | None:
    """«открой канал * на твиче» → (['открой','канал'], ['на','твиче']).

    None, если звёздочек не ровно одна: при нескольких слотах границы
    переменных частей не определить, такие шаблоны пропускаем.
    """
    norm = _normalize(pattern)
    if norm.count("*") != 1:
        return None
    head, tail = norm.split("*")
    return head.split(), tail.split()


def _part_ratio(said: list[str], expected: list[str]) -> tuple[float, int]:
    """Похожесть куска фразы на литерал шаблона + вес (длина литерала)."""
    if not expected:
        return 1.0, 0
    text = " ".join(expected)
    return SequenceMatcher(None, " ".join(said), text).ratio(), len(text)


def _min_word_ratio(said: list[str], expected: list[str]) -> float:
    """Наименьшая похожесть среди слов литерала, сравненных попарно.

    Похожесть куска целиком (см. `_part_ratio`) считается по СКЛЕЕННОЙ
    строке — один совсем чужой слог маскируется соседними хорошо совпавшими
    словами («найди на карте» ≈ «найди на твиче» на 0.79, хотя «карте» и
    «твиче» похожи всего на 0.4). Здесь каждое слово сравнивается со своей
    парой отдельно, поэтому такую подмену не спрятать за длинным литералом.
    """
    if not expected:
        return 1.0
    return min(SequenceMatcher(None, s, e).ratio() for s, e in zip(said, expected))


def _fuzzy_match_template(
    norm: str, commands: list[CommandRule]
) -> tuple[float, RoutedAction] | None:
    """Ближайшая ШАБЛОННАЯ команда: сравниваем только литералы, слот — аргумент."""
    best = None
    best_ratio = _TEMPLATE_THRESHOLD
    words = norm.split()
    for rule in commands:
        if _is_unsafe(rule):
            continue
        parts = _split_pattern(rule.pattern)
        if parts is None:
            continue
        head, tail = parts
        if len(words) < len(head) + len(tail) + 1:  # аргументу нужно хотя бы слово
            continue
        argument = " ".join(words[len(head) : len(words) - len(tail)])
        if not argument:
            continue
        head_words = words[: len(head)]
        tail_words = words[len(words) - len(tail) :] if tail else []
        r_head, w_head = _part_ratio(head_words, head)
        r_tail, w_tail = _part_ratio(tail_words, tail)
        weight = w_head + w_tail
        if not weight:
            continue
        # Взвешенное среднее ниже используется для выбора ЛУЧШЕГО кандидата,
        # но само по себе не гарантирует, что каждый кусок в отдельности
        # достаточно похож (длинное совпадение с одной стороны маскирует
        # полный провал с другой/внутри) — поэтому каждое слово должно
        # пройти порог само по себе, помимо среднего.
        if _min_word_ratio(head_words, head) < _TEMPLATE_THRESHOLD:
            continue
        if _min_word_ratio(tail_words, tail) < _TEMPLATE_THRESHOLD:
            continue
        ratio = (r_head * w_head + r_tail * w_tail) / weight
        if ratio >= best_ratio:
            best_ratio = ratio
            best = (rule, argument)
    if best is None:
        return None
    rule, argument = best
    return best_ratio, RoutedAction(
        action=rule.action,
        argument=rule.template.format(argument),
        via=f"похоже ({best_ratio:.2f})",
    )


def route_exact(
    text: str, commands: list[CommandRule], *, literal_only: bool = False
) -> RoutedAction | None:
    """Только точное совпадение, без всякого угадывания.

    literal_only=True игнорирует шаблоны со «звёздочкой» и совпадает лишь с
    фиксированными фразами. Это нужно цепочкам: фразу, целиком совпавшую с
    фиксированной командой, резать по союзам нельзя — она заведомо одна.
    """
    norm = _normalize(text)
    for rule in commands:
        if literal_only and "*" in rule.pattern:
            continue
        match = _pattern_to_regex(rule.pattern).match(norm)
        if match:
            argument = rule.template.format(*match.groups())
            return RoutedAction(action=rule.action, argument=argument)
    return None


def route(text: str, commands: list[CommandRule]) -> RoutedAction | None:
    # 1. Точное совпадение (в т.ч. шаблоны со «*»).
    exact = route_exact(text, commands)
    if exact is not None:
        return exact
    norm = _normalize(text)
    # 2. Нечёткое: и целые фразы, и шаблоны. Берём совпадение с большей похожестью.
    candidates = [
        found
        for found in (_fuzzy_match(norm, commands), _fuzzy_match_template(norm, commands))
        if found is not None
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda pair: pair[0])[1]
