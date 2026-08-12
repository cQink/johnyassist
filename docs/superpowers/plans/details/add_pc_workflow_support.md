## Add PC workflow support: inline coding, editor prompt integration, window control and git helpers

> **СУЖЕНО и ЗАКРЫТО 2026-08-09** решением владельца. Из трёх шагов ниже сделан
> только третий (git), и это не срез угла:
> - шаг 1 (вставка кода в файл) уже частично закрыт: `type_text` в
>   `johnny/actions/keyboard_action.py` печатает через SendInput туда, где
>   фокус клавиатуры;
> - шаг 2 (прокси промптов во внешний ИИ) — дословно СЛЕДУЮЩИЙ пункт плана
>   «Add external AI prompt proxy support in editors», вся работа там;
> - управление окнами уже было целиком (`johnny/actions/windows.py`);
> - IDE на машине владельца не установлено ни одной, поэтому записи про
>   редактор в `apps.yaml` нет — обещать невыполнимое хуже, чем не иметь
>   команды.
>
> Сделано: `johnny/git_tools.py`, `johnny/actions/git_action.py`, настройка
> `git_workdir`, 46 тестов. Границы (не индексируем сами, push нет, без shell)
> — в докстринге `git_tools.py` и в `current_work_plan.md`.

Цель: Сделать ассистента полезным в повседневной работе на ПК и в IDE.

Критерии успеха:
- Ассистент может вставлять/редактировать код в открытом файле по команде.
- Есть интеграция с VS Code (commands / extension) или через HTTP/prompts.
- Поддержка базовых git-команд через интерфейс ассистента.

Шаги:
1. Реализовать команду для вставки кода в текущий файл (используя VS Code API или simulating keystrokes).
2. Сделать прокси-промпты в `johnny` для отправки контекста файла в внешние ИИ и применять изменения.
3. Добавить git-adapter для выполнения `git status`, `commit`, `branch` через безопасный shell wrapper.

Зависимости: VS Code extension (optional), python `pyautogui`/`pynput` для клавиатуры

Приватность/безопасность: подтвердить выполнение shell-команд пользователем

Оценка: 4-8 дней
