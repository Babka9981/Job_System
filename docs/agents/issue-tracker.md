# Issue tracker: Local Markdown + GitHub mirror

Основной источник задач и спецификаций — Markdown-файлы в `.scratch/`.
Каждая задача дублируется в GitHub Issues:
https://github.com/Babka9981/Job_System

## Local conventions

- Одна директория на функцию: `.scratch/<feature-slug>/`
- Спецификация: `.scratch/<feature-slug>/spec.md`
- Задачи: `.scratch/<feature-slug>/issues/<NN>-<slug>.md`
- Статус указывается строкой `Status:` в начале файла
- Обсуждение добавляется в секцию `## Comments`
- Связанный GitHub Issue указывается строкой `GitHub-Issue: #<number>`

## Synchronization

1. Сначала создать или обновить локальный Markdown-файл.
2. Затем создать или обновить соответствующий GitHub Issue.
3. Для GitHub-команд использовать:
   `gh -R Babka9981/Job_System`
4. Если GitHub временно недоступен, локальный файл остаётся источником истины; синхронизацию выполнить позже.
5. Закрытие, статус, метки и существенные комментарии должны быть отражены с обеих сторон.

## Publishing

Когда навык просит опубликовать задачу:

1. Создать локальный файл.
2. Создать GitHub Issue.
3. Записать номер GitHub Issue в локальный файл.

## Fetching

Сначала прочитать локальный файл, затем проверить актуальное состояние его GitHub Issue.

## Pull requests as a triage surface

PRs as a request surface: no.
