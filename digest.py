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
разницу с двух часов на один. Поэтому:

  * «сегодня» берётся по местному времени человека (zoneinfo), а не по UTC —
    иначе после полуночи по UTC сводка рассказывала бы про вчера;
  * cron в GitHub Actions ставится ДВАЖДЫ, на оба варианта смещения, а лишний
    запуск отсекает `should_run` — у cron нет понятия летнего времени, и
    единственная запись уезжала бы на час зимой или летом.

ПРО МОЛЧАНИЕ. Когда сказать нечего, сводка не отправляется вовсе. Это
осознанное правило, а не экономия: сообщение, приходящее каждый день без
повода, через неделю перестают открывать — и тогда пропустят то самое, ради
чего всё делалось. Кому нужна погода каждое утро — `quiet_when_nothing: false`
в settings.yaml.
"""

import argparse
import datetime as dt
import logging
import os
import sys
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

sys.path.insert(0, str(Path(__file__).resolve().parent))

from johnny import events, notify, weather          # noqa: E402
from johnny.config import load_config               # noqa: E402

logger = logging.getLogger("digest")

ROOT = Path(__file__).resolve().parent
CONFIG_DIR = ROOT / "config"

MORNING = "morning"
EVENING = "evening"

# Насколько запуск может опоздать против назначенного времени и всё ещё
# считаться тем самым запуском. У GitHub Actions cron опаздывает на 5–15 минут
# штатно, поэтому окно щедрое; но оно меньше часа — иначе оба cron-запуска
# (зимний и летний, см. докстринг) сработали бы в один день.
_LATE_MINUTES = 55
_EARLY_MINUTES = 5

_DEFAULTS = {
    "latitude": 59.3293,          # Стокгольм
    "longitude": 18.0686,
    "timezone": "Europe/Stockholm",
    "morning": "07:30",
    "evening": "21:00",
    "quiet_when_nothing": True,
}


def настройки(config) -> dict:
    """Блок digest из settings.yaml поверх значений по умолчанию."""
    raw = getattr(config.settings, "digest", None) or {}
    merged = dict(_DEFAULTS)
    merged.update({k: v for k, v in raw.items() if v is not None and v != ""})
    return merged


def local_now(timezone: str) -> dt.datetime:
    """Текущее время человека. Сбой часового пояса — не повод не отправить сводку."""
    try:
        return dt.datetime.now(ZoneInfo(timezone))
    except (ZoneInfoNotFoundError, ValueError):
        logger.warning("Неизвестный часовой пояс %r — считаю по местному времени машины", timezone)
        return dt.datetime.now()


def should_run(now: dt.datetime, target: str) -> bool:
    """Пора ли отправлять сводку, назначенную на `target` («07:30»).

    Чистая функция: в неё передают время, а не читают часы внутри — иначе
    проверить поведение зимнего запуска можно было бы только зимой.
    """
    try:
        час, минута = (int(part) for part in target.split(":"))
        назначено = now.replace(hour=час, minute=минута, second=0, microsecond=0)
    except (ValueError, TypeError):
        logger.warning("Непонятное время сводки %r — отправляю без проверки", target)
        return True
    опоздание = (now - назначено).total_seconds() / 60
    return -_EARLY_MINUTES <= опоздание <= _LATE_MINUTES


def choose_kind(now: dt.datetime, options: dict) -> str | None:
    """Какая сводка сейчас уместна: утренняя, вечерняя или никакая."""
    for kind in (MORNING, EVENING):
        if should_run(now, str(options[kind])):
            return kind
    return None


def build(kind: str, day_events, forecast, day: dt.date, *, notable: bool) -> str:
    """Собрать текст сводки. Пустая строка = отправлять нечего.

    Чистая: ни сети, ни файлов, ни часов. Всё, что нужно, приходит аргументами,
    поэтому формулировки проверяются тестом, а не подбором дня с дождём.
    """
    строки = [
        f"Доброе утро. Сегодня {_дата(day)}." if kind == MORNING else f"Завтра, {_дата(day)}."
    ]
    if forecast:
        строки.append(f"Погода: {forecast}")
    for occurrence in day_events:
        строки.append(f"— {events.describe(occurrence)}")
    if not day_events and not notable:
        return ""
    return "\n".join(строки)


_МЕСЯЦЫ = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)
_ДНИ = ("понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье")


def _дата(day: dt.date) -> str:
    return f"{_ДНИ[day.weekday()]}, {day.day} {_МЕСЯЦЫ[day.month - 1]}"


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

    kind = args.kind or choose_kind(now, options)
    if kind is None:
        # Сюда попадает лишний из двух cron-запусков (зимний против летнего).
        logger.info("Сейчас %s — ни утро (%s), ни вечер (%s), нечего делать",
                    now.strftime("%H:%M"), options[MORNING], options[EVENING])
        return 0

    day = now.date() if kind == MORNING else now.date() + dt.timedelta(days=1)
    index = 0 if kind == MORNING else 1
    day_events = events.occurrences_on(events.load_events(), day)
    raw = weather.fetch(
        float(options["latitude"]), float(options["longitude"]), str(options["timezone"]), days=index + 1
    )
    текст = build(
        kind, day_events, weather.summary(raw, index), day, notable=weather.is_notable(raw, index)
    )
    if not текст and (args.force or not options["quiet_when_nothing"]):
        текст = build(kind, day_events, weather.summary(raw, index), day, notable=True)
    if not текст:
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
    logger.info("Сводка отправлена (%s, %s, событий %d)", kind, day, len(day_events))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
