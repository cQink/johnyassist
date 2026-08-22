from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class CommandRule:
    pattern: str
    action: str
    template: str


@dataclass
class Settings:
    wake_word: str
    vosk_model_path: str
    response_mode: str
    whisper_model: str
    whisper_device: str
    # Модель, на которую уходить, пока запущена игра. Пусто = не переключаться
    # никогда, и это правильное значение по умолчанию: подмена трогает
    # видеопамять, а сколько её и на что она нужна — знает только владелец
    # машины. Замер на RTX 3070 (2026-08-22): medium 2138 МБ, small 648 МБ.
    whisper_model_gaming: str = ""
    # Пороги свободной видеопамяти для запасного признака (игры мимо Steam).
    # Зазор между ними обязан быть шире 1490 МБ — столько освобождает сама
    # подмена, и на узком зазоре Джони закольцуется. Подробнее — game_watch.decide.
    gpu_guard_low_mb: int = 2500
    gpu_guard_high_mb: int = 4500
    tts_voice: str = "ru-RU-DmitryNeural"
    tts_provider: str = "fish"          # local (NeMo/XTTS) | fish (Джарвис) | edge (Дмитрий)
    fish_model_id: str = ""             # id клонированного голоса на fish.audio
    # Локальный TTS-сервис: пустой tts_local_url — выключен, говорим fish/edge.
    tts_local_url: str = ""             # http://localhost:8765 (или LAN/VPS)
    tts_voice_id: str = ""              # id голоса в сервисе (по умолчанию сервисный)
    tts_style: str = ""                 # calm | confident | friendly (что умеет сервис)
    groq_model: str = "llama-3.3-70b-versatile"
    # Сильная модель по API-ключу («доп мозг»): "" | opus | gpt. Пустая строка
    # по умолчанию — она стоит денег, и включать её должен человек. Ключ к ней
    # живёт в secrets.yaml отдельно: имя модели значит «хочу», ключ — «могу».
    strong_brain: str = ""
    strong_brain_model: str = ""     # пусто = модель по умолчанию своего слота
    # Адрес API сильной модели. Пусто = официальный (api.anthropic.com /
    # api.openai.com). Нужен посредникам вроде agentrouter.org: ключ у них свой,
    # и на официальном адресе он неизвестен — придёт 401, а не «нет денег».
    # Адрес не секрет и живёт здесь, а не в secrets.yaml, — как endpoint у Azure.
    strong_brain_base_url: str = ""
    # true = сильная модель отвечает первой (основной мозг), false = только там,
    # где не справился Groq (доп мозг). Разница здесь — цена и задержка.
    strong_brain_first: bool = False
    tts_volume: float = 1.0             # 0.0–1.0: громкость голоса и звуков Джони
    # Репозиторий для голосовых git-команд («что с гитом», «закоммить …»).
    # Пусто = выключено, и это правильное значение по умолчанию: без явного
    # пути git запускался бы в папке, из которой стартовал Джони, и рассказывал
    # бы про ЧУЖОЙ репозиторий — а человек бы этого не заметил.
    git_workdir: str = ""
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
    # Внешние тулзы (Azure Vision, Face++, social-analyzer, переводчик):
    # согласие, квоты, адрес ресурса, глубина обхода. Пустой блок = всё
    # выключено, и это правильное значение по умолчанию — коннекторы тратят
    # деньги и трогают персональные данные.
    connectors: dict = field(default_factory=dict)


@dataclass
class Config:
    apps: dict[str, str]
    commands: list[CommandRule]
    settings: Settings
    channels: dict = field(default_factory=dict)
    # Люди для Discord: алиас → отображаемое имя и юзернейм. Файла может не быть.
    people: dict = field(default_factory=dict)
    # Сценарии: фраза → список обычных команд. Файла может не быть.
    scenarios: dict = field(default_factory=dict)
    # Ключи API. Файла secrets.yaml может не быть — тогда Джони работает
    # на edge-Дмитрии и claude -p, как до появления облачных провайдеров.
    secrets: dict = field(default_factory=dict)


def _read_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _short_fillers(raw) -> list[str]:
    """Филлеры, которые влезают в кеш TTS. Длинные отбрасываем молча.

    Порог здесь не для красоты: кешируются реплики короче
    tts_cache.MAX_CACHED_CHARS, а некешируемый филлер синтезируется при каждом
    ответе и сам становится той задержкой, ради устранения которой он и нужен.
    Значение продублировано числом намеренно: тянуть в конфиг импорт tts_cache
    (а с ним sounds и requests) ради одной константы дороже, чем этот комментарий.
    """
    limit = 40
    # Обработка случая, когда пришла одиночная строка вместо списка: если в YAML
    # забыли дефис у списка (написали "streaming_fillers: Секунду" вместо
    # "streaming_fillers:\n  - Секунду"), yaml.safe_load вернёт строку, а не
    # список. Трактуем одиночную строку как список из одного элемента; остальные
    # не-списки (число, словарь) даём как пустой список без исключения.
    if isinstance(raw, str):
        raw = [raw]
    elif not isinstance(raw, (list, tuple)):
        raw = []
    return [str(phrase).strip() for phrase in raw if 0 < len(str(phrase).strip()) <= limit]


def load_config(config_dir) -> Config:
    config_dir = Path(config_dir)

    apps_raw = _read_yaml(config_dir / "apps.yaml")
    apps = {str(name).lower(): str(target) for name, target in apps_raw.items()}

    commands_raw = _read_yaml(config_dir / "commands.yaml")
    commands = [
        CommandRule(pattern=pattern, action=body["action"], template=body["template"])
        for pattern, body in commands_raw.items()
    ]

    s = _read_yaml(config_dir / "settings.yaml")
    settings = Settings(
        wake_word=s.get("wake_word", "джони"),
        vosk_model_path=s.get("vosk_model_path", "models/vosk-model-small-ru-0.22"),
        response_mode=s.get("response_mode", "voice"),
        whisper_model=s.get("whisper_model", "medium"),
        whisper_device=s.get("whisper_device", "cuda"),
        whisper_model_gaming=str(s.get("whisper_model_gaming", "") or ""),
        gpu_guard_low_mb=int(s.get("gpu_guard_low_mb", 2500)),
        gpu_guard_high_mb=int(s.get("gpu_guard_high_mb", 4500)),
        tts_voice=s.get("tts_voice", "ru-RU-DmitryNeural"),
        tts_provider=s.get("tts_provider", "fish"),
        fish_model_id=s.get("fish_model_id", ""),
        tts_local_url=s.get("tts_local_url", ""),
        tts_voice_id=s.get("tts_voice_id", ""),
        tts_style=s.get("tts_style", ""),
        groq_model=s.get("groq_model", "llama-3.3-70b-versatile"),
        strong_brain=s.get("strong_brain", ""),
        strong_brain_model=s.get("strong_brain_model", ""),
        strong_brain_base_url=s.get("strong_brain_base_url", ""),
        strong_brain_first=bool(s.get("strong_brain_first", False)),
        tts_volume=float(s.get("tts_volume", 1.0)),
        git_workdir=s.get("git_workdir", ""),
        streaming=bool(s.get("streaming", False)),
        streaming_first_chunk=int(s.get("streaming_first_chunk", 120)),
        streaming_fillers=_short_fillers(s.get("streaming_fillers") or []),
        connectors=s.get("connectors") or {},
    )

    channels_path = config_dir / "channels.yaml"
    channels = _read_yaml(channels_path) if channels_path.exists() else {}

    people_path = config_dir / "people.yaml"
    people = _read_yaml(people_path) if people_path.exists() else {}

    secrets_path = config_dir / "secrets.yaml"
    secrets = _read_yaml(secrets_path) if secrets_path.exists() else {}

    scenarios_path = config_dir / "scenarios.yaml"
    scenarios_raw = _read_yaml(scenarios_path) if scenarios_path.exists() else {}
    scenarios = {
        str(phrase).lower(): [str(step) for step in steps]
        for phrase, steps in scenarios_raw.items()
    }
    # Сценарий — обычное правило роутера, поэтому ему бесплатно достаётся вся
    # лестница совпадений, включая нечёткую: «режым стрима» тоже поймается.
    commands += [
        CommandRule(pattern=phrase, action="scenario", template=phrase) for phrase in scenarios
    ]

    return Config(
        apps=apps,
        commands=commands,
        settings=settings,
        channels=channels,
        people=people,
        scenarios=scenarios,
        secrets=secrets,
    )
