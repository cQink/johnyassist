import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field

from . import brain_anthropic, brain_claude, brain_groq, brain_openai, chain
from .http_client import warn_once
from .router import RoutedAction, is_unsafe_action, route

_PROMPT = """Ты — голосовой ассистент Джони на компьютере с Windows.
Текст ниже пришёл от распознавания речи и МОГ БЫТЬ ИСКОВЕРКАН.
{memory_block}
Пользователь сказал: "{text}"

Вот команды, которые Джони умеет ({count} шт.):
{commands}

1) Если это исковерканная КОМАНДА из списка — ответь ТОЛЬКО JSON с точной
фразой из списка (звёздочку замени тем, что сказал пользователь):
{{"command": "громкость 5"}}

2) Если это НЕСКОЛЬКО команд из списка в одной фразе — ответь ТОЛЬКО JSON со
списком точных фраз из списка, по одной на действие (максимум 5):
{{"steps": ["найди на ютубе кино", "включи первое видео"]}}

3) Если это команда управления компьютером, которой в списке НЕТ (открыть
произвольный сайт, запустить программу) — ответь ТОЛЬКО JSON:
{{"action": "<open_url|launch_app|system>", "argument": "<url | имя программы | volume_up/volume_down/mute/lock>", "reply": "<короткая фраза для озвучки>"}}

4) Во ВСЕХ остальных случаях (вопрос, просьба рассказать/пошутить/поговорить,
или ты не уверен, что это вообще была команда) — ПРОСТО ОТВЕТЬ НА ВОПРОС живой
человеческой речью по-русски, 1–3 коротких предложения, БЕЗ JSON (твой ответ
будет прочитан вслух). «Как дела?» — расскажи, как дела. Не объясняй, что это
не команда, и не перечисляй, что ты умеешь, если об этом не спросили.

ВАЖНО для поля "reply" в пункте 3 и для пункта 4: обращайся НАПРЯМУЮ к
пользователю, на «вы», как в живом разговоре («открываю браузер», «включаю
музыку»), а не «похоже, пользователь хотел...» или «пользователь попросил...».
Ты разговариваешь С НИМ, а не описываешь его действия и не комментируешь этот
промпт со стороны."""


_PUNCTUATE_PROMPT = """Ниже текст, надиктованный человеком голосом и распознанный
Whisper по кускам без знаков препинания (куски резались по паузам и не видят
друг друга, поэтому пунктуации внутри нет).

Текст: "{text}"

Расставь знаки препинания (точки, запятые, вопросительные и восклицательные
знаки) и заглавные буквы в начале предложений. НЕ меняй, не добавляй и не
убирай НИ ОДНОГО слова — только пунктуация и регистр первых букв. Ответь
ТОЛЬКО получившимся текстом, без пояснений и без кавычек вокруг него."""


def punctuate(text: str, providers: list[tuple[str, Callable[[str], str]]]) -> str:
    """Расставляет пунктуацию в надиктованном тексте («Джони, введи ...»)
    через ту же цепочку провайдеров, что и модель-корректор (Groq первым,
    claude -p запасным, см. make_providers).

    Whisper режет длинную диктовку на куски по паузам и расшифровывает их
    независимо (condition_on_previous_text=False — намеренно, иначе короткие
    команды галлюцинируют, см. recognizer.py) — из-за этого пунктуации между
    кусками взяться неоткуда. Постобработка текстом — тот же приём, что и у
    голосовой диктовки ChatGPT/десктопных ассистентов: сырой Whisper даёт
    невыровненную пунктуацию, поэтому текст дополнительно чистит LLM.

    Сбой сети/пустой ответ у ВСЕХ провайдеров — не должны стоить пользователю
    надиктованного текста: возвращаем исходную строку как есть, а не пустоту.
    """
    if not text.strip():
        return text
    prompt = _PUNCTUATE_PROMPT.format(text=text)
    for _, provider in providers:
        raw = provider(prompt)
        if raw and raw.strip():
            return raw.strip().strip('"').strip("«»")
    return text


def _extract_json(raw: str) -> dict | None:
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


@dataclass
class BrainResult:
    routed: RoutedAction | None
    reply: str | None
    # Кто ответил — для истории и лога. В сравнении не участвует, иначе
    # тесты пришлось бы завязывать на имя провайдера.
    provider: str = field(default="", compare=False)
    # Цепочка шагов, если модель разложила фразу на несколько команд.
    steps: list[RoutedAction] | None = None


# Слот «сильной модели»: имя из settings.strong_brain -> модуль и ключ в
# секретах. Слот один и сменный по замыслу: опрашивать обе модели — это двойная
# цена за один ответ, а не двойная надёжность. Сюда же встанет локальная модель,
# когда появится, — строкой в этой таблице, без правок цепочки ниже.
_STRONG_BRAINS = {
    "opus": (brain_anthropic, "anthropic_api_key"),
    "gpt": (brain_openai, "openai_api_key"),
}


def _strong_provider(config, secrets) -> tuple[str, Callable[[str], str]] | None:
    """Провайдер сильной модели или None, если выключена/нет ключа.

    Молчаливое None при отсутствии ключа — намеренно: `strong_brain` в настройках
    значит «хочу», ключ значит «могу», и разводить их надо так же, как у
    коннекторов. Иначе имя модели в настройках включало бы платные запросы само.
    """
    chosen = (getattr(config.settings, "strong_brain", "") or "").strip().lower()
    if not chosen:
        return None
    entry = _STRONG_BRAINS.get(chosen)
    if entry is None:
        warn_once("strong_brain", f"Неизвестная strong_brain: {chosen} — сильная модель выключена")
        return None
    module, secret_name = entry
    api_key = (secrets or {}).get(secret_name)
    if not api_key:
        return None
    model = (getattr(config.settings, "strong_brain_model", "") or "").strip() or module.MODEL
    # Адрес — третьим аргументом, а не через настройку внутри модуля: у ключа
    # владельца посредник (agentrouter.org), и на официальном адресе такой ключ
    # неизвестен. Пусто = официальный адрес, то есть прежнее поведение.
    base_url = (getattr(config.settings, "strong_brain_base_url", "") or "").strip()
    return module.NAME, module.make_provider(api_key, model, base_url)


def make_providers(config) -> list[tuple[str, Callable[[str], str]]]:
    """Порядок опроса моделей: Groq (0.2–0.5 с) впереди, claude -p (3–5 с)
    последним. Groq включается только при наличии ключа в секретах.

    Сильная модель по API-ключу (Opus 5 или GPT-5.6 Sol, см. `strong_brain`)
    встаёт МЕЖДУ ними, и это ключевое решение всей цепочки. Первый непустой
    ответ побеждает, поэтому позиция здесь равна политике расходов: за Groq
    сильная модель включается только там, где Groq не справился, — то есть при
    отсутствии ключа, в его cooldown или на его ошибке. Это и есть «доп мозг»:
    он не отвечает «который час» по цене $25 за миллион токенов.

    Кому нужно обратное — `strong_brain_first: true` ставит её во главу цепочки
    и делает основным мозгом. Отдельным переключателем, а не переписыванием
    порядка руками: разница между «доп» и «основной» здесь исключительно в цене
    и задержке, и человек должен включать её осознанно.
    """
    providers = []
    secrets = config.secrets or {}
    api_key = secrets.get("groq_api_key")
    if api_key:
        providers.append(("groq", brain_groq.make_provider(api_key, config.settings.groq_model)))
    strong = _strong_provider(config, secrets)
    if strong is not None:
        if getattr(config.settings, "strong_brain_first", False):
            providers.insert(0, strong)
        else:
            providers.append(strong)
    providers.append(("claude", brain_claude.run))
    return providers


def interpret(
    text: str, commands=None, providers=None, memory_block: str = ""
) -> BrainResult | None:
    """Спросить модель. providers — список (имя, функция prompt->текст),
    перебираются по порядку до первого непустого ответа. memory_block —
    необязательный блок короткой памяти разговора + долгосрочных фактов
    (см. johnny.memory.build_prompt_block), подставляется в промпт как есть."""
    commands = commands or []
    if providers is None:
        providers = [("claude", brain_claude.run)]
    # providers=[] (в отличие от None) означает «нет ни одного провайдера» и
    # должен остаться пустым: `providers or [...]` не отличал бы пустой
    # список от None и молча уходил бы на живой claude -p.
    # Разрушительные команды (выключение/перезагрузка/сон) не показываем
    # модели вовсе — их нельзя предлагать угадывать даже как «исправление».
    safe_commands = [rule for rule in commands if not is_unsafe_action(rule.action, rule.template)]
    listing = "\n".join(f"- {rule.pattern}" for rule in safe_commands)
    prompt = _PROMPT.format(
        text=text, count=len(safe_commands), commands=listing, memory_block=memory_block
    )

    raw, name = "", ""
    for name, provider in providers:
        raw = provider(prompt)
        if raw and raw.strip():
            break
    if not raw or not raw.strip():
        return None  # пусто у всех = моделей нет на связи

    data = _extract_json(raw)
    if data is not None:
        # Режим коррекции: модель узнала ослышку. Выполняет обычный route() —
        # так модель не может изобрести действие или собрать кривой аргумент.
        corrected = data.get("command")
        if corrected:
            routed = route(corrected, commands)
            if routed is not None and is_unsafe_action(routed.action, routed.argument):
                # «Исправление» указывает на разрушительное действие — выполнять
                # нельзя, даже если модель сама это предложила.
                routed = None
            return BrainResult(routed=routed, reply=None, provider=name)
        steps = data.get("steps")
        if steps:
            # Модель предлагает только ФРАЗЫ: список собирает chain.resolve
            # через route_exact, поэтому изобрести действие или протащить
            # опасную команду она не может.
            return BrainResult(
                routed=None,
                reply=None,
                provider=name,
                steps=chain.resolve([str(step) for step in steps], commands),
            )
        action = data.get("action")
        reply = data.get("reply") or None
        if action in ("open_url", "launch_app", "system"):
            argument = data.get("argument", "")
            if is_unsafe_action(action, argument):
                return BrainResult(routed=None, reply=None, provider=name)
            routed = RoutedAction(action=action, argument=argument, via=name)
            return BrainResult(routed=routed, reply=reply, provider=name)
        if action == "answer":
            return BrainResult(routed=None, reply=reply, provider=name)
        # Валидный JSON, но ни одной из ожидаемых форм ("command"/action) —
        # озвучивать сырой JSON нельзя, app.py скажет «Не понял команду».
        return BrainResult(routed=None, reply=None, provider=name)

    # Не JSON — значит это обычный разговорный ответ, озвучиваем его как есть.
    return BrainResult(routed=None, reply=raw.strip(), provider=name)
