from pathlib import Path
import johnny.config as config
from johnny.config import load_config, CommandRule, Settings


def _write_configs(tmp_path: Path) -> Path:
    (tmp_path / "apps.yaml").write_text(
        'Дота: "steam://rungameid/570"\n', encoding="utf-8"
    )
    (tmp_path / "commands.yaml").write_text(
        '"открой канал * на твиче":\n'
        '  action: open_url\n'
        '  template: "twitch.tv/{0}"\n',
        encoding="utf-8",
    )
    (tmp_path / "settings.yaml").write_text(
        'wake_word: "джони"\n'
        'vosk_model_path: "models/vosk-model-small-ru-0.22"\n'
        'response_mode: voice\n'
        'whisper_model: medium\n'
        'whisper_device: cuda\n',
        encoding="utf-8",
    )
    return tmp_path


def test_load_config_reads_all_sections(tmp_path):
    cfg = load_config(_write_configs(tmp_path))
    # ключи apps приведены к нижнему регистру
    assert cfg.apps["дота"] == "steam://rungameid/570"
    assert cfg.channels == {}  # channels.yaml нет — пустой словарь
    assert cfg.commands[0] == CommandRule(
        pattern="открой канал * на твиче",
        action="open_url",
        template="twitch.tv/{0}",
    )
    assert cfg.settings == Settings(
        wake_word="джони",
        vosk_model_path="models/vosk-model-small-ru-0.22",
        response_mode="voice",
        whisper_model="medium",
        whisper_device="cuda",
    )


def test_load_config_reads_channels(tmp_path):
    _write_configs(tmp_path)
    (tmp_path / "channels.yaml").write_text(
        "9impulse:\n"
        "  twitch: 9impulse\n"
        "  aliases: [импульс, кирчик]\n",
        encoding="utf-8",
    )
    cfg = load_config(tmp_path)
    assert cfg.channels["9impulse"]["twitch"] == "9impulse"
    assert "кирчик" in cfg.channels["9impulse"]["aliases"]


def test_new_settings_have_defaults(tmp_path):
    cfg = load_config(_write_configs(tmp_path))
    assert cfg.settings.tts_provider == "fish"
    assert cfg.settings.fish_model_id == ""
    assert cfg.settings.groq_model == "openai/gpt-oss-120b"
    # Сильная модель по умолчанию выключена: она стоит денег, и включать её
    # обновлением проекта нельзя.
    assert cfg.settings.strong_brain == ""
    # Пустой адрес = официальный сервер. Значение по умолчанию тут ровно то,
    # что было до появления посредников.
    assert cfg.settings.strong_brain_base_url == ""


def test_settings_read_the_strong_brain_address(tmp_path):
    """Адрес сильной модели живёт в настройках, а не в секретах: он не секрет.

    Без него ключ посредника уйдёт на api.anthropic.com, где он неизвестен, и
    ответом будет 401 — то есть «нет доступа» вместо ответа.
    """
    _write_configs(tmp_path)
    (tmp_path / "settings.yaml").write_text(
        'wake_word: "джони"\n'
        'vosk_model_path: "models/vosk-model-small-ru-0.22"\n'
        'response_mode: voice\n'
        'whisper_model: medium\n'
        'whisper_device: cpu\n'
        'strong_brain: "opus"\n'
        'strong_brain_base_url: "https://agentrouter.org"\n',
        encoding="utf-8",
    )
    cfg = load_config(tmp_path)
    assert cfg.settings.strong_brain == "opus"
    assert cfg.settings.strong_brain_base_url == "https://agentrouter.org"


def test_settings_read_tts_and_brain_fields(tmp_path):
    _write_configs(tmp_path)
    (tmp_path / "settings.yaml").write_text(
        'wake_word: "джони"\n'
        'vosk_model_path: "models/vosk-model-small-ru-0.22"\n'
        'response_mode: voice\n'
        'whisper_model: medium\n'
        'whisper_device: cuda\n'
        'tts_provider: edge\n'
        'fish_model_id: "abc123"\n'
        'groq_model: "llama-3.1-8b-instant"\n',
        encoding="utf-8",
    )
    cfg = load_config(tmp_path)
    assert cfg.settings.tts_provider == "edge"
    assert cfg.settings.fish_model_id == "abc123"
    assert cfg.settings.groq_model == "llama-3.1-8b-instant"


def test_secrets_are_optional(tmp_path):
    cfg = load_config(_write_configs(tmp_path))
    assert cfg.secrets == {}   # файла нет — Джони работает без облака


def test_secrets_are_loaded_when_present(tmp_path):
    _write_configs(tmp_path)
    (tmp_path / "secrets.yaml").write_text(
        'groq_api_key: "g-test"\nfish_api_key: "f-test"\n', encoding="utf-8"
    )
    cfg = load_config(tmp_path)
    assert cfg.secrets["groq_api_key"] == "g-test"
    assert cfg.secrets["fish_api_key"] == "f-test"


def test_scenarios_loaded_as_commands(tmp_path):
    _write_configs(tmp_path)
    (tmp_path / "scenarios.yaml").write_text(
        "Режим стрима:\n  - запусти обс\n  - открой твич\n", encoding="utf-8"
    )
    cfg = load_config(tmp_path)
    assert cfg.scenarios["режим стрима"] == ["запусти обс", "открой твич"]
    rules = [rule for rule in cfg.commands if rule.action == "scenario"]
    assert len(rules) == 1
    assert rules[0].pattern == "режим стрима"
    assert rules[0].template == "режим стрима"


def test_missing_scenarios_file_is_fine(tmp_path):
    cfg = load_config(_write_configs(tmp_path))
    assert cfg.scenarios == {}
    assert [rule for rule in cfg.commands if rule.action == "scenario"] == []


def test_shipped_scenarios_all_resolve():
    """Каждый шаг реального scenarios.yaml обязан совпасть с реальной командой.

    Опечатка в шаге иначе всплыла бы только голосом: Джони промолчал бы, и
    понять почему было бы нечем.
    """
    import johnny.chain as chain

    cfg = load_config("config")
    assert cfg.scenarios, "в config/scenarios.yaml нет ни одного сценария"
    for name, steps in cfg.scenarios.items():
        assert chain.resolve(steps, cfg.commands) is not None, f"сценарий «{name}» не собрался"


def test_загружает_людей(tmp_path):
    (tmp_path / "apps.yaml").write_text("обс: obs.exe\n", encoding="utf-8")
    (tmp_path / "commands.yaml").write_text(
        '"пауза":\n  action: system\n  template: "play_pause"\n', encoding="utf-8"
    )
    (tmp_path / "settings.yaml").write_text("wake_word: джони\n", encoding="utf-8")
    (tmp_path / "people.yaml").write_text(
        'гоша:\n  discord: "Гречка"\n  username: "ne_godjaj"\n  aliases: ["гоша"]\n',
        encoding="utf-8",
    )

    config = load_config(tmp_path)

    assert config.people["гоша"]["discord"] == "Гречка"


def test_без_файла_людей_словарь_пустой(tmp_path):
    (tmp_path / "apps.yaml").write_text("обс: obs.exe\n", encoding="utf-8")
    (tmp_path / "commands.yaml").write_text(
        '"пауза":\n  action: system\n  template: "play_pause"\n', encoding="utf-8"
    )
    (tmp_path / "settings.yaml").write_text("wake_word: джони\n", encoding="utf-8")

    assert load_config(tmp_path).people == {}


def test_streaming_is_off_by_default():
    """Новое поведение включает человек: стриминг меняет то, КАК звучит Джони,
    и по умолчанию звучать он должен как вчера."""
    settings = config.Settings(
        wake_word="джони", vosk_model_path="p", response_mode="voice",
        whisper_model="medium", whisper_device="cpu",
    )
    assert settings.streaming is False
    assert settings.streaming_fillers == []


def test_streaming_settings_are_read_from_yaml(tmp_path):
    (tmp_path / "apps.yaml").write_text("{}", encoding="utf-8")
    (tmp_path / "commands.yaml").write_text("{}", encoding="utf-8")
    (tmp_path / "settings.yaml").write_text(
        "wake_word: джони\n"
        "vosk_model_path: p\n"
        "response_mode: voice\n"
        "whisper_model: medium\n"
        "whisper_device: cpu\n"
        "streaming: true\n"
        "streaming_first_chunk: 90\n"
        "streaming_fillers:\n"
        "  - Секунду\n"
        "  - Момент\n",
        encoding="utf-8",
    )
    loaded = config.load_config(tmp_path)
    assert loaded.settings.streaming is True
    assert loaded.settings.streaming_first_chunk == 90
    assert loaded.settings.streaming_fillers == ["Секунду", "Момент"]


def test_fillers_longer_than_the_cache_limit_are_dropped(tmp_path):
    """Филлер длиннее MAX_CACHED_CHARS не кешируется и синтезируется каждый
    раз — то есть сам становится задержкой, которую призван скрыть."""
    (tmp_path / "apps.yaml").write_text("{}", encoding="utf-8")
    (tmp_path / "commands.yaml").write_text("{}", encoding="utf-8")
    (tmp_path / "settings.yaml").write_text(
        "wake_word: джони\n"
        "vosk_model_path: p\n"
        "response_mode: voice\n"
        "whisper_model: medium\n"
        "whisper_device: cpu\n"
        "streaming_fillers:\n"
        "  - Секунду\n"
        f"  - {'очень длинная фраза ' * 5}\n",
        encoding="utf-8",
    )
    loaded = config.load_config(tmp_path)
    assert loaded.settings.streaming_fillers == ["Секунду"]


def test_single_string_filler_instead_of_list_is_treated_as_one_filler(tmp_path):
    """Если в YAML забыли дефис у списка («streaming_fillers: Секунду» вместо
    «streaming_fillers:\n  - Секунду»), yaml.safe_load вернёт строку, а не
    список. Без обработки строка разбивается на отдельные буквы и получается
    семь однобуквенных филлеров, которые Джони произносит вслух.

    Правка: одиночную строку трактуем как список из одного филлера."""
    (tmp_path / "apps.yaml").write_text("{}", encoding="utf-8")
    (tmp_path / "commands.yaml").write_text("{}", encoding="utf-8")
    (tmp_path / "settings.yaml").write_text(
        "wake_word: джони\n"
        "vosk_model_path: p\n"
        "response_mode: voice\n"
        "whisper_model: medium\n"
        "whisper_device: cpu\n"
        "streaming_fillers: Секунду\n",  # Забыли дефис: это строка, а не список
        encoding="utf-8",
    )
    loaded = config.load_config(tmp_path)
    # Должен быть ОДИН филлер, а не семь букв
    assert loaded.settings.streaming_fillers == ["Секунду"]
    assert len(loaded.settings.streaming_fillers) == 1


def test_non_list_non_string_filler_value_gives_empty_list(tmp_path):
    """Если в YAML ошибка и пришло что-то совсем неподходящее (например число),
    конфиг не должен упасть при старте. Пустой список филлеров — это законное
    состояние (филлеров нет), а падение из-за опечатки в необязательной
    настройке несоразмерно.

    Правка: не-списки и не-строки обрабатываются как пустой список."""
    (tmp_path / "apps.yaml").write_text("{}", encoding="utf-8")
    (tmp_path / "commands.yaml").write_text("{}", encoding="utf-8")
    (tmp_path / "settings.yaml").write_text(
        "wake_word: джони\n"
        "vosk_model_path: p\n"
        "response_mode: voice\n"
        "whisper_model: medium\n"
        "whisper_device: cpu\n"
        "streaming_fillers: 123\n",  # Явная ошибка: число вместо строки/списка
        encoding="utf-8",
    )
    loaded = config.load_config(tmp_path)
    # Не падаем, просто даём пустой список
    assert loaded.settings.streaming_fillers == []


def test_git_workdir_is_read_from_settings_yaml(tmp_path):
    """Значение git_workdir из settings.yaml должно доезжать до Settings.git_workdir.

    В прошлом коммите было добавлено чтение этого параметра (git_workdir=s.get(...)).
    Эта правка решает проблему: git-команды выполнялись бы в каталоге запуска
    Джони и рассказывали бы про ЧУЖОЙ репозиторий, а человек бы этого не заметил.
    Теперь настройка работает.
    """
    (tmp_path / "apps.yaml").write_text("{}", encoding="utf-8")
    (tmp_path / "commands.yaml").write_text("{}", encoding="utf-8")
    (tmp_path / "settings.yaml").write_text(
        "wake_word: джони\n"
        "vosk_model_path: p\n"
        "response_mode: voice\n"
        "whisper_model: medium\n"
        "whisper_device: cpu\n"
        'git_workdir: "D:/ssistent/3/johnyassist"\n',
        encoding="utf-8",
    )
    loaded = config.load_config(tmp_path)
    assert loaded.settings.git_workdir == "D:/ssistent/3/johnyassist"


def test_git_workdir_defaults_to_empty_string_when_missing(tmp_path):
    """Если git_workdir не указан в YAML, настройка должна быть пустой строкой.

    Пустая строка здесь — правильное значение по умолчанию: она выключает
    git-команды, и они не ошибаются молча, выполняясь в чужом репозитории.
    """
    (tmp_path / "apps.yaml").write_text("{}", encoding="utf-8")
    (tmp_path / "commands.yaml").write_text("{}", encoding="utf-8")
    (tmp_path / "settings.yaml").write_text(
        "wake_word: джони\n"
        "vosk_model_path: p\n"
        "response_mode: voice\n"
        "whisper_model: medium\n"
        "whisper_device: cpu\n",
        encoding="utf-8",
    )
    loaded = config.load_config(tmp_path)
    assert loaded.settings.git_workdir == ""


def test_игровая_модель_по_умолчанию_выключена():
    """Пусто = никогда не переключаться, как git_workdir и streaming_fillers.
    Переключение трогает видеопамять чужой машины — включать его молча нельзя."""
    settings = config.Settings(
        wake_word="джони", vosk_model_path="p", response_mode="voice",
        whisper_model="medium", whisper_device="cpu",
    )
    assert settings.whisper_model_gaming == ""


def test_игровая_модель_и_пороги_читаются_из_yaml(tmp_path):
    (tmp_path / "apps.yaml").write_text("{}", encoding="utf-8")
    (tmp_path / "commands.yaml").write_text("{}", encoding="utf-8")
    (tmp_path / "settings.yaml").write_text(
        "wake_word: джони\n"
        "vosk_model_path: p\n"
        "response_mode: voice\n"
        "whisper_model: medium\n"
        "whisper_device: cuda\n"
        "whisper_model_gaming: small\n"
        "gpu_guard_low_mb: 2500\n"
        "gpu_guard_high_mb: 4500\n",
        encoding="utf-8",
    )
    loaded = config.load_config(tmp_path)
    assert loaded.settings.whisper_model_gaming == "small"
    assert loaded.settings.gpu_guard_low_mb == 2500
    assert loaded.settings.gpu_guard_high_mb == 4500


def test_пороги_gpu_guard_по_умолчанию_когда_ключей_нет(tmp_path):
    """Если gpu_guard_low_mb и gpu_guard_high_mb не указаны в YAML, должны быть
    значения по умолчанию 2500 и 4500."""
    (tmp_path / "apps.yaml").write_text("{}", encoding="utf-8")
    (tmp_path / "commands.yaml").write_text("{}", encoding="utf-8")
    (tmp_path / "settings.yaml").write_text(
        "wake_word: джони\n"
        "vosk_model_path: p\n"
        "response_mode: voice\n"
        "whisper_model: medium\n"
        "whisper_device: cpu\n",
        encoding="utf-8",
    )
    loaded = config.load_config(tmp_path)
    assert loaded.settings.gpu_guard_low_mb == 2500
    assert loaded.settings.gpu_guard_high_mb == 4500


def test_пороги_gpu_guard_по_умолчанию_когда_значения_пусты(tmp_path):
    """Если в YAML пустые значения (gpu_guard_low_mb: без значения), YAML вернёт
    None. Без защиты int(None) бросит TypeError. Должны быть значения по умолчанию."""
    (tmp_path / "apps.yaml").write_text("{}", encoding="utf-8")
    (tmp_path / "commands.yaml").write_text("{}", encoding="utf-8")
    (tmp_path / "settings.yaml").write_text(
        "wake_word: джони\n"
        "vosk_model_path: p\n"
        "response_mode: voice\n"
        "whisper_model: medium\n"
        "whisper_device: cpu\n"
        "gpu_guard_low_mb:\n"
        "gpu_guard_high_mb:\n",
        encoding="utf-8",
    )
    loaded = config.load_config(tmp_path)
    assert loaded.settings.gpu_guard_low_mb == 2500
    assert loaded.settings.gpu_guard_high_mb == 4500


def test_gpu_guard_ноль_не_превращается_в_значение_по_умолчанию(tmp_path):
    """Находка 8 ревью: `int(s.get(...) or 2500)` считал бы 0 отсутствующим
    значением наравне с None, хотя это разные вещи — 0 значит «уходить на
    маленькую модель при любой занятой видеопамяти», а не «настройка не
    задана». Различать их обязано именно `is None`."""
    (tmp_path / "apps.yaml").write_text("{}", encoding="utf-8")
    (tmp_path / "commands.yaml").write_text("{}", encoding="utf-8")
    (tmp_path / "settings.yaml").write_text(
        "wake_word: джони\n"
        "vosk_model_path: p\n"
        "response_mode: voice\n"
        "whisper_model: medium\n"
        "whisper_device: cpu\n"
        "gpu_guard_low_mb: 0\n"
        "gpu_guard_high_mb: 0\n",
        encoding="utf-8",
    )
    loaded = config.load_config(tmp_path)
    assert loaded.settings.gpu_guard_low_mb == 0
    assert loaded.settings.gpu_guard_high_mb == 0
