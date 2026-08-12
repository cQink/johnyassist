"""Память Джони — три слоя, разные по времени жизни и по тому, кто их пишет.

  short-term  разговор: последние _MAX_TURNS обменов, остывает через
              _CONTEXT_MAX_AGE секунд молчания. Пишется САМА, на каждом
              ответе модели (см. record_turn).
  long-term   факты о пользователе: файл memory.yaml, живёт до forget().
              Пишется ТОЛЬКО явной командой «запомни» — Джони не решает
              за человека, что о нём стоит записать навсегда.
  prompt      то, что из первых двух реально доезжает до модели —
              build_prompt_block, обрезанный по объёму.

Разговор переживает перезапуск (dialog.yaml): трей и консоль перезапускаются
чаще, чем заканчивается разговор, и «да, давай» после рестарта раньше
упиралось в пустой контекст. Правило «остыло за 10 минут» при этом главнее
персистентности — см. load_dialog."""

import logging
import re
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

# Сколько последних обменов держим и на сколько секунд им доверяем. Дольше
# 10 минут молчания — считаем разговор остывшим: случайное «да» через час
# не должно уехать в вопрос, который давно все забыли.
_MAX_TURNS = 5
_CONTEXT_MAX_AGE = 600.0


@dataclass
class Turn:
    user_text: str
    reply: str
    at: float


_turns: deque = deque(maxlen=_MAX_TURNS)

# Разговор на диске. Рядом с memory.yaml, но это РАЗНЫЕ слои: dialog.yaml
# Джони переписывает сам на каждом ответе, memory.yaml — только по команде
# «запомни». Смешивать их в одном файле нельзя: автозапись затирала бы то,
# что человек просил хранить.
_DIALOG_FILE = Path(__file__).resolve().parent.parent / "dialog.yaml"


def record_turn(user_text: str, reply: str) -> None:
    """Запомнить обмен репликами. Только опрошенные моделью реплики (см.
    app.handle_command, шаг 4) — обычные локальные команды («громкость 5»
    по точному совпадению) контекст не создают, ссылаться там не на что."""
    now = time.monotonic()
    _turns.append(Turn(user_text, reply, now))
    # Часы читаем один раз и передаём: второй независимый monotonic() внутри
    # save_dialog дал бы записи на диске возраст чуть больше, чем у того же
    # обмена в памяти.
    save_dialog(now)


def recent_context(max_age: float = _CONTEXT_MAX_AGE) -> list:
    """Обмены не старше max_age секунд, от самого старого к новому."""
    now = time.monotonic()
    return [turn for turn in _turns if now - turn.at <= max_age]


def save_dialog(now_mono: float | None = None) -> None:
    """Сложить разговор на диск. Время пишем НАСТЕННОЕ (time.time), а не
    monotonic из Turn.at: monotonic считается от старта машины и в другом
    процессе не значит ничего — после перезапуска сравнивать его с новым
    monotonic всё равно что вычитать метры из секунд.

    now_mono — показание monotonic(), от которого считать возраст обменов.
    Передаётся вызывающим (см. record_turn), чтобы не читать часы дважды за
    один обмен.

    Сбой записи не должен ронять ответ, который человек уже ждёт: разговор
    — вещь необязательная, диск может быть занят или полон."""
    now_mono = time.monotonic() if now_mono is None else now_mono
    now_wall = time.time()
    entries = [
        {
            "user_text": turn.user_text,
            "reply": turn.reply,
            # Возраст обмена на момент записи, переведённый в настенное время.
            "at": now_wall - (now_mono - turn.at),
        }
        for turn in _turns
    ]
    try:
        _DIALOG_FILE.write_text(
            yaml.safe_dump(entries, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )
    except OSError:
        logger.warning("Не смог сохранить разговор в %s", _DIALOG_FILE, exc_info=True)


def load_dialog() -> int:
    """Поднять разговор с диска в _turns. Возвращает число поднятых обменов.

    Остывший разговор не поднимаем: правило «10 минут молчания — контекст
    больше не в счёт» (см. _CONTEXT_MAX_AGE) главнее персистентности.
    Иначе «да» наутро уехало бы во вчерашний вопрос — ровно то, от чего
    max_age и защищает; перезапуск тут ничего не меняет.

    Битый файл — не повод падать на старте: разговор необязателен, начнём
    с пустого."""
    if not _DIALOG_FILE.exists():
        return 0
    try:
        data = yaml.safe_load(_DIALOG_FILE.read_text(encoding="utf-8"))
    except (yaml.YAMLError, OSError, UnicodeDecodeError):
        logger.warning("dialog.yaml не читается, начинаю разговор с чистого листа", exc_info=True)
        return 0
    if not isinstance(data, list):
        return 0

    now_mono, now_wall = time.monotonic(), time.time()
    restored = []
    for entry in data:
        if not isinstance(entry, dict) or "user_text" not in entry or "reply" not in entry:
            logger.warning("Пропускаю запись dialog.yaml неправильной формы: %r", entry)
            continue
        try:
            age = now_wall - float(entry.get("at", 0.0))
        except (TypeError, ValueError):
            continue
        # Часы могли перевести назад (или файл принесли с другой машины):
        # отрицательный возраст — не повод выкидывать обмен, но и доверять
        # ему как «только что» нельзя. Считаем его свежим, но не будущим.
        age = max(age, 0.0)
        if age > _CONTEXT_MAX_AGE:
            continue
        restored.append(Turn(entry["user_text"], entry["reply"], now_mono - age))

    _turns.clear()
    _turns.extend(restored)
    return len(restored)


_MEMORY_FILE = Path(__file__).resolve().parent.parent / "memory.yaml"
# Доля слов запроса, которые обязаны найтись в тексте факта, чтобы forget()
# посчитал его найденным. НЕ SequenceMatcher по целым строкам: короткий
# голосовой запрос («стим-аккаунт») против длинного факта («у пользователя
# стим-аккаунт art_vol_teror») даёт посимвольно всего 0.45 — ниже разумного
# порога, хотя оба слова запроса в факте буквально есть.
_FORGET_THRESHOLD = 0.5
# Верхняя граница объёма фактов, попадающих в промпт модели. Память —
# контекст, а не досье: двести длинных фактов не поместятся ни в один
# промпт, а пять самых свежих обычно покрывают текущий разговор. Число, а
# не доля: на фактах-однострочниках лимит по длине почти никогда не
# срабатывает, и модель видит ровно столько, сколько помещается.
_MAX_FACTS_CHARS = 1500


def _load_facts_raw() -> tuple[list, bool]:
    """Прочитать факты с диска. Возвращает (facts, ok).

    ok=False означает, что файл существует, но не смог быть прочитан или
    распознан как список фактов — порча YAML, неправильная кодировка,
    временно заблокированный файл. Это отличается от «файла нет» или
    «файл есть и это валидный пустой список» (оба — ok=True, facts=[]):
    только в случае ok=False remember()/forget() должны воздержаться от
    перезаписи файла, чтобы не стереть содержимое, которое не смогли
    прочитать (см. remember()).

    list_facts()/forget() дёргаются на КАЖДОМ ходу LLM-пути (см.
    app.build_prompt_block) — необработанное исключение здесь через общий
    catch в handle_command молча превращало бы «Не смог выполнить команду» в
    ответ на любую разговорную фразу, пока человек вручную не починит файл.
    Битый файл или отдельная кривая запись — не повод терять остальное.
    """
    if not _MEMORY_FILE.exists():
        return [], True
    try:
        data = yaml.safe_load(_MEMORY_FILE.read_text(encoding="utf-8"))
    except (yaml.YAMLError, OSError, UnicodeDecodeError):
        logger.warning(
            "memory.yaml повреждён (не удалось прочитать или распарсить), считаю память пустой",
            exc_info=True,
        )
        return [], False
    if data is None:
        return [], True
    if not isinstance(data, list):
        logger.warning("memory.yaml не список фактов (%r), считаю память пустой", type(data))
        return [], False
    facts = []
    for entry in data:
        if not isinstance(entry, dict) or "text" not in entry:
            logger.warning("Пропускаю запись memory.yaml неправильной формы: %r", entry)
            continue
        facts.append(entry)
    return facts, True


def _load_facts() -> list:
    """Факты с диска для чтения (list_facts()/forget() внутренней логики
    сравнения) — деградирует до [] на любой порче, без различения причины.
    Для мест, которым важно не перезаписывать битый файл, см.
    _load_facts_raw()."""
    facts, _ok = _load_facts_raw()
    return facts


def _save_facts(facts: list) -> None:
    _MEMORY_FILE.write_text(
        yaml.safe_dump(facts, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )


def _quarantine_broken_memory_file() -> None:
    """Отвести не читающийся memory.yaml в сторону перед тем, как
    remember() создаст на его месте свежий файл. Без этого повреждение
    файла (одна кривая запись, битая кодировка) превращало бы следующую же
    команду «запомни X» в необратимую потерю всего, что было в файле
    раньше — деградация до [] в _load_facts_raw() существует ради чтения
    (LLM-путь), а не как разрешение перезаписывать оригинал."""
    broken_path = _MEMORY_FILE.with_name(
        _MEMORY_FILE.name + ".broken-" + datetime.now().strftime("%Y%m%d-%H%M%S")
    )
    try:
        _MEMORY_FILE.rename(broken_path)
    except OSError:
        logger.warning(
            "Не смог отвести повреждённый memory.yaml в %s перед перезаписью — "
            "содержимое может быть потеряно",
            broken_path,
            exc_info=True,
        )
        return
    logger.warning(
        "memory.yaml не читается — сохранил повреждённую копию как %s перед тем, как начать новый файл",
        broken_path,
    )


def remember(text: str) -> None:
    facts, ok = _load_facts_raw()
    if not ok:
        _quarantine_broken_memory_file()
        facts = []
    facts.append({"text": text, "added": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
    _save_facts(facts)


def _word_overlap(query: str, fact_text: str) -> float:
    words = re.findall(r"[a-zа-я0-9_]+", query.lower().replace("ё", "е"))
    if not words:
        return 0.0
    fact_norm = fact_text.lower().replace("ё", "е")
    hits = sum(1 for word in words if word in fact_norm)
    return hits / len(words)


def _find_closest(query: str, facts: list) -> int | None:
    """Индекс факта, ближайшего к запросу, или None если ничего не дотянуло
    до _FORGET_THRESHOLD. Общий для forget() и update_fact() — иначе два
    порога разъехались бы, и «забудь X» находило бы не то, что «поправь X»."""
    best_index, best_score = None, 0.0
    for index, fact in enumerate(facts):
        score = _word_overlap(query, fact["text"])
        if score > best_score:
            best_index, best_score = index, score
    if best_index is None or best_score < _FORGET_THRESHOLD:
        return None
    return best_index


def forget(query: str) -> str | None:
    """Удалить ближайший по смыслу факт. См. модульный докстринг
    _FORGET_THRESHOLD про то, почему это доля слов, а не SequenceMatcher.

    Если файл не читается, forget() ничего не пишет — стирать тут нечего,
    ведём себя как «ничего похожего не найдено» (в отличие от remember(),
    который обязан что-то записать и поэтому сначала отводит битый файл
    в сторону, см. _quarantine_broken_memory_file)."""
    facts, ok = _load_facts_raw()
    if not ok:
        return None
    if not facts:
        return None
    index = _find_closest(query, facts)
    if index is None:
        return None
    removed = facts.pop(index)
    _save_facts(facts)
    return removed["text"]


def list_facts() -> list:
    return [fact["text"] for fact in _load_facts()]


def facts_detailed() -> list:
    """Факты вместе с датой добавления — для UI и отладки. list_facts()
    остаётся строками: его результат уходит в промпт и в озвучку, где даты
    только мешают."""
    return [dict(fact) for fact in _load_facts()]


def update_fact(query: str, text: str) -> str | None:
    """Заменить текст ближайшего по смыслу факта. Возвращает прежний текст
    или None, если ничего похожего не нашлось.

    Дополняет remember/forget: «поправь, у меня теперь другой ник» — это
    одна запись, а не «забудь» + «запомни» двумя командами, между которыми
    факт успевает потеряться, если вторая не доедет.

    Поиск — тот же _find_closest, что и у forget(), поэтому порог и его
    обоснование (см. _FORGET_THRESHOLD) общие."""
    facts, ok = _load_facts_raw()
    if not ok or not facts:
        return None
    index = _find_closest(query, facts)
    if index is None:
        return None
    previous = facts[index]["text"]
    facts[index] = {
        **facts[index],
        "text": text,
        "updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    _save_facts(facts)
    return previous


def clear_facts() -> int:
    """Стереть все факты. Возвращает, сколько стёрли.

    Отдельно от forget() намеренно: forget() ищет ОДИН факт по запросу и на
    коротком запросе может задеть не тот, а «забудь всё» — осознанное
    действие с понятным объёмом. Вызывающая сторона обязана спросить
    подтверждение (см. actions/memory_action.py)."""
    facts, ok = _load_facts_raw()
    if not ok:
        # Файл не читается — как и forget(), ничего не пишем: стирать
        # непрочитанное значит терять его безвозвратно.
        return 0
    if not facts:
        return 0
    _save_facts([])
    return len(facts)


def _fit_facts(facts: list, max_chars: int) -> list:
    """Факты, помещающиеся в max_chars, в исходном порядке.

    Отбираем С КОНЦА (свежие важнее давних: ник, поменянный вчера, нужнее
    того, что записан полгода назад), а возвращаем в исходном порядке —
    так модель видит их в том же виде, что и человек по «что ты помнишь».

    Перебираем весь список, а не обрываемся на первом непоместившемся:
    один длинный факт посреди коротких не должен отрезать всё, что старше
    него.
    """
    if max_chars <= 0:
        return []
    picked, used = [], 0
    for fact in reversed(facts):
        # +2 — «- » перед фактом и перевод строки после, иначе лимит
        # считался бы по тексту, а в промпт уезжало бы заметно больше.
        cost = len(fact) + 2
        if used + cost > max_chars:
            continue
        picked.append(fact)
        used += cost
    picked.reverse()
    return picked


def build_prompt_block(context: list, facts: list, max_facts_chars: int = _MAX_FACTS_CHARS) -> str:
    """Необязательный блок для brain._PROMPT. Пустая строка, если ни
    контекста, ни фактов нет — на месте {memory_block} останется пусто.

    Факты обрезаются по объёму (см. _fit_facts): их число не ограничено
    ничем, кроме желания человека их надиктовать, а промпт — ограничен.
    Разговор обрезать не нужно, его держит maxlen самого буфера."""
    parts = []
    if context:
        lines = "\n".join(
            f'Пользователь сказал: "{turn.user_text}" — ты ответил: "{turn.reply}"'
            for turn in context
        )
        parts.append(
            "Недавний разговор (если сейчас продолжение — например, короткое "
            "«да»/«давай» отвечает на твой последний вопрос):\n" + lines
        )
    fitted = _fit_facts(facts, max_facts_chars)
    if fitted:
        parts.append("Известно о пользователе:\n" + "\n".join(f"- {fact}" for fact in fitted))
    return ("\n\n".join(parts) + "\n") if parts else ""
