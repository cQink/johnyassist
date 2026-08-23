"""Голосом в календарь: «напомни в четверг про врача» и «что у меня сегодня».

Этот файл — только про голос. Он превращает фразу в запись календаря и
зачитывает записанное, но НЕ знает ни про телеграм, ни про сводку, ни про
погоду: сводку собирает и отправляет другой код, и связывает их только формат
файла (config/calendar.yaml). Сломать одно, починив другое, поэтому трудно.

Джони всегда проговаривает распознанную дату целиком — «записал на четверг,
27 августа». Не из вежливости: разбор фразы может ошибиться, и это
единственный момент, когда человек ещё способен поправить. Напоминание, тихо
уехавшее не на тот день, обнаруживается тем, что человек не пришёл к врачу.
"""

import datetime as dt
import logging
import re

from .. import dates, events
from ..git_tools import plural
from .registry import ActionResult, registry

logger = logging.getLogger(__name__)

# Сколько событий зачитываем вслух за раз. Больше на слух не удерживается, а
# читает их Джони голосом — та же причина, что у _LOG_COUNT в git_tools.
_MAX_SPOKEN = 6


def _ask_model(ctx: dict):
    """Функция «промпт → ответ» для запасного разбора даты, или None.

    Собирается лениво и только из того, что уже есть в конфиге: свой парсер
    справляется с обычными фразами, и дёргать сеть на каждое «напомни завтра»
    незачем (см. dates.parse_with_fallback — модель зовётся, только если свой
    разбор вернул None).
    """
    config = ctx.get("config")
    secrets = getattr(config, "secrets", None) or {}
    api_key = secrets.get("groq_api_key")
    if not api_key:
        return None
    from .. import brain_groq

    return brain_groq.make_provider(api_key, config.settings.groq_model)


@registry.register("remind")
def action_remind(argument: str, ctx: dict) -> ActionResult:
    """«напомни в четверг про врача» → запись в календарь."""
    today = dt.date.today()
    reminder = dates.parse_with_fallback(argument, today, ask=_ask_model(ctx))
    if reminder is None:
        # Отказ, а не запись на сегодня: напоминание не в тот день хуже, чем
        # его отсутствие, — человек будет на него рассчитывать.
        return ActionResult(False, "Не понял, на когда напомнить. Скажите день: завтра, в четверг, 15 сентября")
    event = events.Event(
        date=reminder.date,
        text=reminder.text,
        repeat=reminder.repeat,
        warn=reminder.warn,
        time=reminder.time,
    )
    try:
        events.append_event(event)
    except OSError as exc:
        logger.warning("Не смог записать в календарь: %s", exc)
        return ActionResult(False, "Не смог записать в календарь")
    logger.info("В календарь: %s %s (повтор %s, за %d дн.)", event.date, event.text, event.repeat, event.warn)
    return ActionResult(True, _подтверждение(event, today))


def _подтверждение(event: events.Event, today: dt.date) -> str:
    """«Записал на четверг, 27 августа: врач».

    Формулировок четыре, по одной на вид повтора, и это не украшательство:
    «записал на каждую неделю, завтра» человек разбирает вслух дольше, чем
    «записал: каждый понедельник», а слышит он это один раз и на ходу.
    Предлог у разового случая вырезается («в четверг» → «на четверг») —
    иначе выходит «записал на в четверг».
    """
    # Двоеточие в «Записал:» уже занято у повторов, поэтому текст к ним
    # цепляется тире: «Записал: каждый год 15 сентября: день рождения» читается
    # как список, а не как фраза.
    разделитель = ": "
    if event.repeat == events.YEARLY:
        начало, разделитель = f"Записал: каждый год {events.day_month(event.date)}", " — "
    elif event.repeat == events.MONTHLY:
        начало, разделитель = f"Записал: каждое {event.date.day} число", " — "
    elif event.repeat == events.WEEKLY:
        начало, разделитель = f"Записал: {events.every_weekday(event.date)}", " — "
    else:
        когда = re.sub(r"^во?\s+", "", events.human_date(event.date, today))
        начало = f"Записал на {когда}"
    if event.time:
        начало += f", в {event.time}"
    хвост = ""
    if event.warn:
        хвост = f". Предупрежу за {event.warn} {plural(event.warn, 'день', 'дня', 'дней')}"
    return f"{начало}{разделитель}{event.text}{хвост}"


@registry.register("calendar")
def action_calendar(argument: str, ctx: dict) -> ActionResult:
    """«что у меня сегодня» / «завтра» / «на неделе» — зачитать календарь.

    Аргумент приходит из commands.yaml и может быть только одним из трёх слов:
    так фраза не превращается в свободный запрос, который пришлось бы
    разбирать ещё раз.
    """
    today = dt.date.today()
    if argument == "tomorrow":
        return _на_день(today + dt.timedelta(days=1), "Завтра", "На завтра ничего не записано")
    if argument == "week":
        return _на_неделю(today)
    return _на_день(today, "Сегодня", "На сегодня ничего не записано")


def _на_день(day: dt.date, начало: str, пусто: str) -> ActionResult:
    найдено = events.occurrences_on(events.load_events(), day)
    if not найдено:
        return ActionResult(True, пусто)
    строки = [events.describe(o) for o in найдено[:_MAX_SPOKEN]]
    ответ = f"{начало}: " + "; ".join(строки)
    if len(найдено) > _MAX_SPOKEN:
        ответ += f"; и ещё {len(найдено) - _MAX_SPOKEN}"
    return ActionResult(True, ответ)


def _на_неделю(today: dt.date) -> ActionResult:
    """Ближайшие семь дней. Каждое событие называем один раз, в свой день.

    Обратный отсчёт (warn) здесь не нужен: неделя показана целиком, и
    «предупредить за три дня» превратилось бы в одно и то же событие,
    названное четыре раза подряд.
    """
    все = events.load_events()
    строки: list[str] = []
    for сдвиг in range(7):
        day = today + dt.timedelta(days=сдвиг)
        for occ in events.occurrences_on(все, day):
            if occ.days_left:
                continue
            когда = events.human_date(day, today)
            время = f", в {occ.event.time}" if occ.event.time else ""
            строки.append(f"{когда}{время} — {occ.event.text}")
    if not строки:
        return ActionResult(True, "На неделю ничего не записано")
    ответ = "На неделе: " + "; ".join(строки[:_MAX_SPOKEN])
    if len(строки) > _MAX_SPOKEN:
        ответ += f"; и ещё {len(строки) - _MAX_SPOKEN}"
    return ActionResult(True, ответ)
