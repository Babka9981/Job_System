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
Пользователь дал отдельную команду начать реализацию 20.09.2026. Выполнять тикеты
по зависимостям из плана `docs/planning/README.md`.
Канонические тексты тикетов — `.scratch/job-search-mvp/issues/`; в `.autopilot/`
хранятся ссылки и состояние.

Проверенные команды bootstrap: `.\.venv\Scripts\python.exe manage.py test`,
`node --test jobs/static/ui/tests/*.test.cjs` и
`.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run`.
Django сам не загружает `.env`: production-переменные задаются process manager либо
в окружении процесса; воспроизводимый `check --deploy` описан в `README.md`.

Dedup вакансий: URL сам по себе никогда не является cross-source evidence. Внутри
источника identity — `source+external_id`, fallback `source+exact URL`; между
источниками merge разрешён только по непустому content hash или структурированному
adapter-confirmed permalink. Permission/evidence флаги принимают только literal `True`.
<!-- autopilot:end -->
