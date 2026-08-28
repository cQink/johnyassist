"""Утренняя и вечерняя сводка в телеграм: календарь плюс погода.

Запускается по расписанию и живёт в корне проекта, а не в пакете johnny,
потому что это не часть голосового ассистента, а отдельная маленькая программа
с тем же календарём. Микрофона, видеокарты и Whisper ей не нужно вовсе — и
именно поэтому её потом можно будет унести в облако целиком, чтобы сводка
приходила при выключенном компьютере.

    python digest.py            — понять по часам, утро сейчас или вечер
    python digest.py morning    — собрать утреннюю принудительно
    python digest.py evening --dry-run   — показать текст, никуда не отправляя

ПРО ЧАСОВОЙ ПОЯС, потенциально самую обидную ошибку во всей затее. Облако
работает в UTC, человек живёт в Стокгольме, и переход на летнее время сдвигает
разницу с двух часов на один. Поэтому «сегодня» и «пора ли» считаются по
МЕСТНОМУ времени человека (zoneinfo), а не по UTC: иначе после полуночи по UTC
сводка рассказывала бы про вчера. Cron про летнее время не знает вовсе — знает
только этот файл.

ПРО НАДЁЖНОСТЬ, и это здесь главное. Расписание в GitHub Actions
НЕГАРАНТИРОВАННОЕ: события задерживаются, а иногда не создаются совсем.
27.08.2026 утренней сводки не было вовсе — GitHub не создал ни одного запуска
ни в 05:30, ни в 06:30 UTC, при активном workflow и свежем репозитории.

Поэтому конструкция не полагается на единственный будильник:

  * cron стоит КАЖДЫЙ ЧАС внутри окна (см. .github/workflows/digest.yml);
  * окно широкое — утро 07:30–12:00, вечер 21:00–00:00;
  * от повторов защищает не узость окна, а отметка в data/last-sent.json:
    «эту сводку за такое-то число уже отправляли».

Пропуск одного запуска стоит часа задержки вместо потерянного дня.

ПРО МОЛЧАНИЕ. Когда сказать нечего, сводка может не отправляться вовсе —
`quiet_when_nothing`. Правило осознанное: сообщение, приходящее каждый день
без повода, через неделю перестают открывать. Но сейчас оно ВЫКЛЮЧЕНО по
просьбе владельца: пока он проверяет работу, молчание неотличимо от поломки.
"""

import argparse
import datetime as dt
import json
import logging
import os
import sys
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

sys.path.insert(0, str(Path(__file__).resolve().parent))

from johnny import events, notify, school, translate, weather  # noqa: E402
from johnny.config import load_config               # noqa: E402

logger = logging.getLogger("digest")

ROOT = Path(__file__).resolve().parent
CONFIG_DIR = ROOT / "config"

MORNING = "morning"
EVENING = "evening"

# Слепок расписания: с ним сравнивается сегодняшняя выгрузка, чтобы заметить
# отмену урока. Лежит в самом репозитории, а не в кеше запуска: облачный
# запуск начинается с чистой машины, и любой кеш там пуст. Заодно история
# правок расписания оказывается в git — видно, что и когда переносили.
SNAPSHOT = ROOT / "data" / "school-snapshot.json"

# Отметка «эту сводку за такое-то число уже отправляли». Лежит рядом со
# слепком расписания и по той же причине: другой памяти между запусками в
# облаке нет.
STATE = ROOT / "data" / "last-sent.json"

# ДОГОНЯЮЩЕЕ ОКНО, и это ядро всей затеи с надёжностью.
#
# 27.08.2026 утренняя сводка не пришла вовсе: GitHub не создал НИ ОДНОГО
# запуска ни в 05:30, ни в 06:30 UTC. Workflow при этом активен, репозиторий
# свежий, прошлые запуски зелёные — событие просто потерялось. Расписание в
# Actions негарантированное: GitHub обещает «возможные задержки при высокой
# нагрузке», а на деле после аварии часть событий не создаётся совсем.
#
# Поэтому будильник теперь не один. Cron стоит КАЖДЫЙ ЧАС внутри окна, а
# отметка в STATE не даёт отправить сводку дважды за день. Пропуск одного
# запуска стоит часа задержки вместо целого пропущенного дня.
#
# Размер окна. 28.08.2026 замерено: за сутки GitHub доставил ОДИН запуск из
# десяти, и тот с опозданием на пять с половиной часов. При окне в 4.5 часа
# опоздавший запуск попадал уже в закрытое окно и молча ничего не делал —
# то есть узкое окно само по себе съедало день.
#
# Поэтому утреннее окно тянется до 15:30: уроки, меню и «через сколько дней
# контрольная» на сегодня остаются нужными до конца учебного дня, а поздний
# приход помечается late — см. build(), «Доброе утро» в час дня не пишем.
# Вечернее упирается в полночь: за неё оно не заходит намеренно, иначе «уже
# отправляли сегодня» сравнивалось бы с другой датой, чем та, о которой сводка.
_WINDOW_HOURS = {MORNING: 8.0, EVENING: 3.0}

# С какого опоздания сводка считается догоняющей. Полтора часа: обычная
# задержка cron у GitHub — минуты, так что нормальный запуск сюда не попадёт.
_LATE_AFTER_HOURS = 1.5

# На сколько окно открывается РАНЬШЕ назначенного времени. Пять минут — на
# случай, если часы раннера чуть спешат относительно назначенного; больше не
# нужно, окно и так длинное с другого конца.
_EARLY_HOURS = 5 / 60

# Сколько строк об изменениях расписания показывать. Больше — уже простыня,
# в которой теряется всё остальное; сколько осталось, всё равно сказано.
_MAX_CHANGES = 5

_DEFAULTS = {
    "latitude": 59.3293,          # Стокгольм
    "longitude": 18.0686,
    "timezone": "Europe/Stockholm",
    "morning": "07:30",
    "evening": "21:00",
    "quiet_when_nothing": True,
    # За сколько дней начинать напоминать про контрольные и сдачи. У школьных
    # заданий нет своего поля warn, в отличие от событий calendar.yaml, —
    # поэтому запас общий и настраивается здесь.
    "task_warn_days": 7,
    "school": True,
    # Перевод меню столовой на русский моделью. Названия предметов НЕ трогает —
    # для них есть subject_names ниже, и это осознанно разные механизмы.
    "translate_menu": True,
    # Переименование предметов: правило укорачивания даёт формально верное имя,
    # но «Svenska som andra språk» человек называет просто «Svenska».
    "subject_names": {},
}


def настройки(config) -> dict:
    """Блок digest из settings.yaml поверх значений по умолчанию."""
    raw = getattr(config.settings, "digest", None) or {}
    merged = dict(_DEFAULTS)
    merged.update({k: v for k, v in raw.items() if v is not None and v != ""})
    return merged


def local_now(timezone: str) -> dt.datetime | None:
    """Текущее время человека. None — часовой пояс неизвестен.

    Именно None, а не «посчитаю по времени машины». Раньше здесь был как раз
    такой запасной путь, и он оказался ловушкой: машина в облаке живёт по UTC,
    то есть на два часа мимо. `should_run` не попал бы в окно НИ РАЗУ, сводка
    не приходила бы никогда — и ничто бы об этом не сообщило. Молчание тут
    неотличимо от «сегодня нечего сказать».

    Причина, по которой это вообще случается: zoneinfo берёт зоны из системы,
    а на Windows их нет вовсе — нужен пакет tzdata (он есть в requirements.txt
    и в workflow). Поймано проверкой из чистого клона 2026-08-26.
    """
    try:
        return dt.datetime.now(ZoneInfo(timezone))
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        return None


def should_run(now: dt.datetime, target: str, window_hours: float = 4.5) -> bool:
    """Наступило ли окно сводки, назначенной на `target` («07:30»).

    Чистая функция: в неё передают время, а не читают часы внутри — иначе
    проверить поведение зимнего запуска можно было бы только зимой.

    Окно открывается за пять минут до назначенного времени (часы машины могут
    чуть спешить) и держится window_hours. Держится долго намеренно: см.
    _WINDOW_HOURS — от повторной отправки защищает не узость окна, а отметка
    в STATE.
    """
    try:
        час, минута = (int(part) for part in target.split(":"))
        назначено = now.replace(hour=час, minute=минута, second=0, microsecond=0)
    except (ValueError, TypeError):
        logger.warning("Непонятное время сводки %r — отправляю без проверки", target)
        return True
    прошло = (now - назначено).total_seconds() / 3600
    return -_EARLY_HOURS <= прошло <= window_hours


def is_late(now: dt.datetime, target: str) -> bool:
    """Сводка догоняющая — назначена была больше часа с лишним назад.

    Непонятное время считаем «не опоздали»: врать «Доброе утро» неприятно, но
    молча менять приветствие из-за неразобранной настройки — страннее.
    """
    try:
        час, минута = (int(part) for part in target.split(":"))
    except (ValueError, TypeError):
        return False
    назначено = now.replace(hour=час, minute=минута, second=0, microsecond=0)
    return (now - назначено).total_seconds() / 3600 > _LATE_AFTER_HOURS


def load_state(path) -> dict:
    """Что и когда уже отправляли. Нет файла или он битый — пустой словарь.

    Пустой словарь значит «сегодня ещё ничего не отправляли», то есть в худшем
    случае придёт лишняя сводка. Это несравнимо лучше обратной ошибки, когда
    из-за нечитаемого файла сводка не приходит вовсе и никто не знает почему.
    """
    from pathlib import Path

    path = Path(path)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Отметка об отправке не прочиталась (%s) — считаю, что не отправляли", exc)
        return {}
    return data if isinstance(data, dict) else {}


def save_state(path, state: dict) -> None:
    from pathlib import Path

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")


def choose_kind(now: dt.datetime, options: dict, state: dict | None = None) -> str | None:
    """Какая сводка сейчас уместна: утренняя, вечерняя или никакая.

    Учитывает отметку об уже отправленном: cron стоит каждый час внутри окна,
    и без этой проверки человек получал бы одну и ту же сводку пять раз подряд.
    """
    state = state or {}
    сегодня = now.date().isoformat()
    for kind in (MORNING, EVENING):
        if not should_run(now, str(options[kind]), _WINDOW_HOURS[kind]):
            continue
        if state.get(kind) == сегодня:
            logger.info("Сводка %s за %s уже отправлена — пропускаю запуск", kind, сегодня)
            continue
        return kind
    return None


class Школа:
    """Готовые к печати куски школьной части. Пустые, если фид не пришёл.

    Собирается отдельно от build(), чтобы build оставалась чистой: тексты
    сводки проверяются тестом, а не подбором подходящего учебного дня.
    """

    __slots__ = ("lessons", "menu", "tasks", "changes")

    def __init__(self, lessons="", menu="", tasks=None, changes=None):
        self.lessons, self.menu = lessons, menu
        self.tasks, self.changes = tasks or [], changes or []

    def __bool__(self):
        return bool(self.lessons or self.menu or self.tasks or self.changes)


def собрать_школу(школьные, day: dt.date, changes, warn_days: int, menu="") -> Школа:
    """Уроки, меню, ближайшие контрольные и изменения — на день `day`.

    ВСЁ СЧИТАЕТСЯ ОТ `day`, а не от сегодняшнего числа, и в этом смысл: вечерняя
    сводка целиком про завтра, о чём прямо сказано в её первой строке. Пока
    отсчёт шёл от сегодня, вечером в день контрольной она всё ещё висела в
    списке — уже написанная. Теперь окно начинается с того дня, про который
    сводка, и написанное вчера в неё не попадает.

    Аргумент назван «школьные», а не events, сознательно: имя events занято
    модулем календаря, и параметр с тем же именем перекрыл бы его внутри
    функции — вместе с events.in_days, который здесь и нужен.
    """
    задания = []
    for task in school.tasks_between(школьные, day, day + dt.timedelta(days=warn_days)):
        осталось = (task.date() - day).days
        хвост = f" ({events.in_days(осталось)})" if осталось else ""
        время = f"{task.time()} " if task.time() else ""
        задания.append(f"{время}{task.title}{хвост}".strip())
    return Школа(
        lessons=school.describe_lessons(school.lessons_on(школьные, day)),
        menu=menu or school.menu_on(школьные, day),
        tasks=задания,
        changes=changes.lines() if changes else [],
    )


def build(kind: str, day_events, forecast, day: dt.date, *, notable: bool, школа=None,
          late: bool = False) -> str:
    """Собрать текст сводки. Пустая строка = отправлять нечего.

    Чистая: ни сети, ни файлов, ни часов. Всё, что нужно, приходит аргументами,
    поэтому формулировки проверяются тестом, а не подбором дня с дождём.

    late — сводка догоняющая, приехала сильно позже назначенного времени
    (GitHub может задержать запуск на часы, см. _WINDOW_HOURS). Тогда «Доброе
    утро» не пишем: в час дня оно выглядит поломкой и подрывает доверие ко
    всему остальному тексту, хотя уроки и меню на сегодня всё ещё нужны.
    """
    школа = школа or Школа()
    if kind != MORNING:
        первая = f"Завтра, {_дата(day)}."
    elif late:
        первая = f"Сегодня {_дата(day)}."
    else:
        первая = f"Доброе утро. Сегодня {_дата(day)}."
    строки = [первая]
    if forecast:
        строки.append(f"Погода: {forecast}")
    if школа.lessons:
        строки.append(f"Уроки: {школа.lessons}")
    # Изменения стоят сразу за уроками, а не в общем списке дел: это
    # единственная строка, ради которой сводку стоит открыть немедленно.
    #
    # Потолок нужен на случай настоящей перетасовки расписания: полтора десятка
    # строк «отменили…» — это уже не новость, а простыня, и в ней потеряется
    # всё остальное. Число оставшихся всё равно названо, так что скрыть от
    # человека ничего не получится.
    for изменение in школа.changes[:_MAX_CHANGES]:
        строки.append(f"! Расписание: {изменение}")
    if len(школа.changes) > _MAX_CHANGES:
        строки.append(f"! Расписание: и ещё {len(школа.changes) - _MAX_CHANGES} изменений")
    if школа.menu:
        строки.append(f"Обед: {школа.menu}")
    for occurrence in day_events:
        строки.append(f"— {events.describe(occurrence)}")
    for задание in школа.tasks:
        строки.append(f"— {задание}")
    if not day_events and not notable and not школа:
        return ""
    return "\n".join(строки)


_МЕСЯЦЫ = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)
_ДНИ = ("понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье")


def _дата(day: dt.date) -> str:
    return f"{_ДНИ[day.weekday()]}, {day.day} {_МЕСЯЦЫ[day.month - 1]}"


def адрес_школы(config) -> str:
    """Ссылка на школьный фид: сначала окружение, потом secrets.yaml.

    Живёт среди секретов, а не настроек, потому что это пароль: SchoolSoft
    предупреждает, что содержимое календаря видит любой, у кого есть ссылка.
    """
    из_окружения = os.environ.get("SCHOOLSOFT_ICAL_URL", "").strip()
    из_файла = str((getattr(config, "secrets", None) or {}).get("schoolsoft_ical_url") or "").strip()
    return из_окружения or из_файла


def секреты(config) -> tuple[str, str]:
    """Токен и адресат: сначала переменные окружения, потом secrets.yaml.

    Порядок именно такой ради облака: в GitHub Actions секреты приезжают
    переменными окружения, а файла secrets.yaml там нет и быть не должно —
    он в .gitignore, и это правильно.
    """
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    из_файла = notify.from_secrets(getattr(config, "secrets", None) or {})
    return token or из_файла[0], chat_id or из_файла[1]


def собрать_расписание(config, options, day: dt.date, today: dt.date, dry_run: bool) -> Школа:
    """Скачать школьный фид, сравнить с прошлым слепком, сохранить новый.

    Слепок сохраняется ТОЛЬКО при настоящем запуске: при --dry-run человек
    смотрит, что получится, и не должен этим просмотром съесть отмену урока —
    иначе следующий, настоящий запуск сравнит уже с новым слепком и промолчит.
    """
    if not options.get("school", True):
        return Школа()
    url = адрес_школы(config)
    if not url:
        logger.info("Ссылки на школьный календарь нет — расписание пропускаю")
        return Школа()
    text = school.fetch(url)
    if text is None:
        return Школа()
    школьные = school.parse_feed(text, options.get("subject_names") or {})
    прошлый = school.load_snapshot(SNAPSHOT)
    новый = school.snapshot(школьные, today)
    изменения = school.diff(прошлый, новый)
    if изменения:
        logger.info("Расписание изменилось: %s", "; ".join(изменения.lines()))
    if not dry_run:
        school.save_snapshot(SNAPSHOT, новый)
    меню = school.menu_on(школьные, day)
    if меню and options.get("translate_menu", True):
        меню = translate.dish_to_russian(меню, _переводчик(config))
    return собрать_школу(
        школьные, day, изменения, int(options.get("task_warn_days", 7)), menu=меню
    )


def _переводчик(config):
    """Функция перевода или None: ключа Groq может не быть, и это нормально.

    Без ключа меню просто останется шведским — сводка от этого не пострадает.
    """
    ключ = os.environ.get("GROQ_API_KEY", "").strip() or str(
        (getattr(config, "secrets", None) or {}).get("groq_api_key") or ""
    ).strip()
    модель = getattr(getattr(config, "settings", None), "groq_model", "") or "openai/gpt-oss-120b"
    return translate.make_asker(ключ, модель)


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Сводка в телеграм")
    parser.add_argument("kind", nargs="?", choices=[MORNING, EVENING], help="какую сводку собрать")
    parser.add_argument("--dry-run", action="store_true", help="показать текст, не отправляя")
    parser.add_argument("--force", action="store_true", help="отправить, даже если сказать нечего")
    args = parser.parse_args(argv)

    config = load_config(CONFIG_DIR)
    options = настройки(config)
    now = local_now(str(options["timezone"]))
    if now is None:
        logger.error(
            "Часовой пояс %r неизвестен: нет пакета tzdata. Время сводок посчитать не по чему.",
            options["timezone"],
        )
        if args.kind is None:
            # Запуск по расписанию: решать «утро сейчас или вечер» не по чему,
            # а угадать значит слать не вовремя. Падаем с ошибкой — красный
            # запуск в GitHub видно, а молчащую сводку не видно никак.
            return 1
        # Сводку назвали явно (руками, кнопкой Run workflow) — решать нечего,
        # считаем по времени машины и продолжаем.
        now = dt.datetime.now()

    состояние = load_state(STATE)
    # Явно названная сводка (руками, кнопкой Run workflow) отметку игнорирует:
    # человек попросил именно её и именно сейчас, спорить с ним незачем.
    kind = args.kind or choose_kind(now, options, состояние)
    if kind is None:
        logger.info("Сейчас %s — ни утро (%s), ни вечер (%s), нечего делать",
                    now.strftime("%H:%M"), options[MORNING], options[EVENING])
        return 0

    day = now.date() if kind == MORNING else now.date() + dt.timedelta(days=1)
    index = 0 if kind == MORNING else 1
    day_events = events.occurrences_on(events.load_events(), day)
    raw = weather.fetch(
        float(options["latitude"]), float(options["longitude"]), str(options["timezone"]), days=index + 1
    )
    школа = собрать_расписание(config, options, day, now.date(), args.dry_run)
    опоздали = kind == MORNING and is_late(now, str(options[kind]))
    текст = build(
        kind, day_events, weather.summary(raw, index), day,
        notable=weather.is_notable(raw, index), школа=школа, late=опоздали,
    )
    if not текст and (args.force or not options["quiet_when_nothing"]):
        текст = build(
            kind, day_events, weather.summary(raw, index), day, notable=True, школа=школа,
            late=опоздали,
        )
    if not текст:
        # Отметку НЕ ставим: сказать было нечего — значит сводка за сегодня не
        # отправлена, и если через час появится повод (изменилось расписание,
        # завели напоминание), следующий запуск обязан её прислать.
        logger.info("Сказать нечего — молчу (%s, %s)", kind, day)
        return 0

    if args.dry_run:
        print(текст)
        return 0

    token, chat_id = секреты(config)
    try:
        notify.send(текст, token=token, chat_id=chat_id)
    except notify.NotifyError as exc:
        # Здесь именно падаем, в отличие от try_send у Джони: упавший запуск
        # видно в GitHub, а проглоченная ошибка означала бы, что сводка молча
        # не приходит неделями и никто об этом не знает.
        logger.error("Сводка не отправлена: %s", exc)
        return 1
    # Отметка ставится ТОЛЬКО после успешной отправки — иначе неудачный запуск
    # закрыл бы окно, и следующий, уже успешный, промолчал бы.
    состояние[kind] = now.date().isoformat()
    save_state(STATE, состояние)
    logger.info("Сводка отправлена (%s, %s, событий %d)", kind, day, len(day_events))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
