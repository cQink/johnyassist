# Итерация «Больше команд» — дизайн

**Дата:** 2026-07-25
**Статус:** согласован

## Цель

Расширить набор голосовых команд: больше игр/программ, новые сайты, поиск
Google/Twitch, и смена аккаунта Steam голосом.

## Программы (`apps.yaml`)

Каждой программе — несколько имён (Whisper часто пишет латиницей). Пути к
exe **в кавычках** (пробелы) + запуск из правильной рабочей папки.

| Голос | Цель |
|-------|------|
| дота / dota | `steam://rungameid/570` |
| апекс / apex | `steam://rungameid/1172470` |
| кс / cs / кс2 | `steam://rungameid/730` (CS2) |
| раст / rust | `steam://rungameid/252490` |
| стим / steam | `"C:/Program Files (x86)/Steam/steam.exe"` |
| хром / браузер / chrome | `"C:/Program Files/Google/Chrome/Application/chrome.exe"` |
| обс / obs | `"C:/Program Files/obs-studio/bin/64bit/obs64.exe"` |
| дискорд / discord | `"C:/Users/Admin/AppData/Local/Discord/Update.exe" --processStart Discord.exe` |
| повершелл / powershell | `"C:/WINDOWS/System32/WindowsPowerShell/v1.0/powershell.exe"` |

Запуск через существующий шаблон «запусти *».

## Новые команды (`commands.yaml`)

- Сайты: «открой спотифай» → open.spotify.com · «открой тикток» → tiktok.com ·
  «открой почту» → mail.google.com (Gmail)
- Google: «загугли *» / «найди в гугле *» → `google.com/search?q={0}`
- Twitch поиск: «найди на твиче *» → `twitch.tv/search?term={0}`
- YouTube: «включи * на ютубе» → поиск (`youtube.com/results?search_query={0}`)

## Смена аккаунта Steam (новое действие `steam_login`)

Запускает `steam.exe -login <аккаунт>`. Фразы → аккаунты:
- «зайди на основу» / «основа» / «основной» → `art_vol_teror`
- «зайди на йети» / «йети» → `uhctuhkt1`
- «зайди на смурф» / «смурф» → `flynes_`

Путь к `steam.exe` берётся из `apps.yaml` (ключ «стим»).

## Правки в коде

- `actions.py`:
  - `_start(target)` — разбирать путь через `shlex.split(posix=False)` (кавычки +
    аргументы), запускать с `cwd = папка exe` (чинит OBS, у которого свои locale-
    файлы рядом).
  - Новая функция `steam_login(account, apps)` + ветка в `execute()`.
- Тесты: разбор пути с пробелами/аргументами; `steam_login` (успех и «Steam не
  найден»).

## Осознанно вне итерации

- Умное имя канала → правильный URL (проблема «Danger Лёха»).
- Мультимониторность.

## На проверку пользователем

Смена аккаунта Steam через `-login` на **запущенном** Steam может лишь
переключить фокус. Если так — добавим `-shutdown` → пауза → `-login`.
