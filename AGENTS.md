## Agent skills

### Issue tracker

Задачи ведутся локально в `.scratch/` и зеркалируются в GitHub Issues репозитория `Babka9981/Job_System`. См. `docs/agents/issue-tracker.md`.

### Triage labels

Используются стандартные triage-метки Matt Pocock. См. `docs/agents/triage-labels.md`.

### Domain docs

Используется single-context структура. См. `docs/agents/domain.md`.

<!-- autopilot:start -->
# Персональный поиск работы

Личный Django-сервис для Ильи: вакансии, профиль, источники и подготовка откликов.
Исходные требования: `Job_Search_MVP_TZ.md`. Перед работой над интерфейсом читать
`design-system/README.md` и связанные спецификацию, токены и чек-лист.

Сборка ведётся навыком `autopilot`. Состояние — `.autopilot/state.js`,
прогресс — `.autopilot/dashboard.html`. Требования может отменять только пользователь.
Локальные таски `.scratch/` должны ссылаться на соответствующие таски сборки.
Текущее поручение ограничено подготовкой. Все тикеты not-started: выполнять их
только после отдельной команды пользователя. План — `docs/planning/README.md`.
Канонические тексты тикетов — `.scratch/job-search-mvp/issues/`; в `.autopilot/`
хранятся ссылки и состояние. Не запускать build автоматически при продолжении чата.
<!-- autopilot:end -->
