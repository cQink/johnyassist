## Add project context analysis and smart workspace automation

Цель: Анализировать проект и предлагать полезные шаги и автоматизацию.

Критерии успеха:
- Ассистент показывает структуру проекта, зависимости и TODO-список.
- Есть команды автоматизации (run tests, open docs, scaffold tasks).

Шаги:
1. Реализовать сканер проекта (парсинг `package.json`, `requirements.txt`, `pyproject.toml`).
2. Индексировать файлы для поиска и контекста.
3. Создать набор автоматизаций (test-run, build, lint).

Зависимости: парсеры для language-specific manifests

Приватность/безопасность: read-only скан по умолчанию

Оценка: 3-6 дней
