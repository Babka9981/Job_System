## Agent skills

### Issue tracker

Задачи ведутся локально в `.scratch/` и зеркалируются в GitHub Issues репозитория `Babka9981/Job_System`. См. `docs/agents/issue-tracker.md`.

### Triage labels

Используются стандартные triage-метки Matt Pocock. См. `docs/agents/triage-labels.md`.

### Domain docs

Используется single-context структура. См. `docs/agents/domain.md`.

<!-- autopilot:start -->
# Персональный поиск работы

Личный owner-only сервис для Ильи: собирает вакансии, извлекает и подтверждает профиль
из CV, оценивает соответствие, исследует компании, готовит два вида отклика и отправляет
Telegram-дайджест. Стек: Python 3.13.15, Django 5.2.17 LTS, SQLite, server-rendered HTML;
Node 22 используется только для UI/browser acceptance. Исходные требования —
`Job_Search_MVP_TZ.md`; перед UI-изменениями читать `design-system/README.md`.

## Команды

```powershell
# установка
py -V:3.13.15 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
npm ci

# локальная БД и сервер; Django читает environment процесса, а не .env
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py runserver

# проверенные контуры
.\.venv\Scripts\python.exe manage.py test
npm run test:ui
npm run test:browser
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run

# один Django-модуль / один Node-файл
.\.venv\Scripts\python.exe manage.py test jobs.tests.test_matching
node --test jobs/static/ui/tests/drawer.test.cjs

# production image; сначала создать deployment/.env через wizard
Set-Location deployment
bash ./setup-wizard.sh
$env:JOB_IMAGE_TAG = 'local-check'
docker compose build web
```

Последняя полная проверка: Django 355 passed, 1 skipped; Node 10 tests; browser matrix
50 scans. Воспроизводимые `check --deploy`, acceptance и restore-команды описаны в
`README.md`, `docs/acceptance/pilot-acceptance.md` и `docs/operations/README.md`.

## Структура

```text
config/                  Django settings, root URL и WSGI
jobs/models/             общая ORM-схема, migrations и storage
jobs/core/               owner access, login и общий shell
jobs/profile/            CV, факты, criteria/preferences и подтверждение версии
jobs/vacancies/          карточки, optimistic updates и dedup/upsert
jobs/sources/            registry, collector, public/keyed/RVC/Telegram adapters
jobs/matching/           детерминированные hard rules и платная LLM-оценка
jobs/intelligence/       budget, OpenAI gateway, безопасный fetch и Tavily/Brave research
jobs/drafts/             cover letter/recruiter message и пользовательские правки
jobs/monitoring/         расписание, global lease, recovery и цикл мониторинга
jobs/notifications/      Telegram Bot outbox, retry и uncertain reconciliation
jobs/operations/         health, cleanup и allowlisted snapshot/verification
jobs/templates/, jobs/static/ server-rendered UI и единственные runtime UI assets
tests/acceptance/        сквозной fixture journey, Chrome/Axe и restore fault injection
deployment/              Docker/Caddy/systemd, wizard, backup и restore
```

## Ключевые файлы

- `config/settings.py` — все Django/runtime границы; `.env` автоматически не загружается.
- `jobs/models/models.py` — единая схема данных; migration order живёт в `jobs/models/migrations/`.
- `jobs/sources/core/contracts.py` и `jobs/sources/core/collector.py` — `Batch/Coverage`, cursors, fencing и ingestion orchestration.
- `jobs/vacancies/services.py` — sanitization, identity/dedup и versioned user-state writes.
- `jobs/matching/services.py` — `evaluate(...)`, permission/hash gate, cache и single-flight lease.
- `jobs/intelligence/gateway.py`, `budget.py`, `research/service.py` — structured LLM, reservations и evidence-backed research.
- `jobs/drafts/services.py` — генерация по ID подтверждённых фактов и server-side composition.
- `jobs/monitoring/service.py` и `jobs/notifications/service.py` — cycle checkpoint и доставка дайджеста.
- `jobs/operations/snapshot.py` — единственная allowlist-граница backup/restore.
- `deployment/compose.yaml` — project `job-system`, loopback bind `127.0.0.1:18111`, non-root read-only container.
- `docs/operations/README.md` — production runbook; VPS deploy остаётся отдельным owner-approved gate.

## Архитектура и поток

1. Adapter возвращает `Batch(records,next_cursor,coverage)`; collector держит fenced DB lease, атомарно вызывает `upsert_record` и сохраняет opaque cursor только после commit страницы.
2. `upsert_record` очищает публичный текст, сохраняет точный SHA-256 и связывает provenance; UI меняет пользовательское состояние через optimistic `Vacancy.version`.
3. `evaluate` сначала применяет доказуемые salary/geo/function rules, затем при необходимости проходит exact permission/hash gate, budget reservation и holder-scoped lease.
4. Research выбирает Tavily/Brave, резервирует стоимость каждого HTTP-вызова, fetch-ит только публичные адреса и сохраняет проверяемые passages, conflicts, hypotheses и provider provenance.
5. Draft generation передаёт LLM только IDs разрешённых фактов; итоговый текст собирается сервером из точного CV/JD/research evidence и сохраняется с CAS-версией.
6. `run_cycle` под global lease выполняет collection → matching → durable checkpoint → `NotificationOutbox`; Telegram Bot delivery отделена от MTProto reader.
7. SQLite и private/CV state постоянны; temporary source content, sessions, caches и secrets физически исключены из snapshot. Backup шифруется `age`, private identity хранится вне VPS.

## Соглашения кода

- Owner scoping обязателен на ORM/service/HTTP границах; регистрация закрыта, app pages доступны только `JOB_OWNER_USERNAME`.
- Unknown остаётся unknown: деньги — `Decimal`, даты в DB — UTC, timezone применяется на scheduling/UI границе.
- Same-source identity: `source+external_id`, fallback `source+exact URL`; один URL не является cross-source evidence.
- Cross-source merge разрешён только по непустому content hash или literal `adapter_confirmed_permalink=True` при совместимой identity.
- LLM видит description только при literal `description_permission=True` и точном совпадении `SourceRecord.raw_hash`; разрешение не переносится после изменения контента.
- Недоверенные CV/JD/web/Telegram данные не имеют tool authority; upstream errors наружу выходят только как безопасный code/message без response body, cause и credentials.
- Платные вызовы сначала резервируют дневной бюджет; uncertain outcome остаётся pending, а неизвестная цена fail-closed отключает вызов.
- Конкурентные платные/monitoring операции используют DB `Lease`; занятый/locked lease означает pending/busy, release всегда holder-scoped.
- Общие компоненты UI лежат в `jobs/templates/shared/`; цвета, размеры и состояния берутся только из `jobs/static/ui/tokens.css` и design system.
- Feature tests заменяют внешние провайдеры fixtures; live API, Telegram и VPS не считаются проверенными локальной acceptance.

## Окружение

Имена и назначение перечислены в `.env.example`; значения и локальные `.env` не коммитятся.

- Django/storage: `DJANGO_SECRET_KEY`, `DJANGO_DEBUG`, `DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS`, `DJANGO_SECURE_SSL_REDIRECT`, `DJANGO_SECURE_HSTS_SECONDS`, `JOB_DATABASE_PATH`, `JOB_STATIC_ROOT`, `JOB_PRIVATE_ROOT`, `JOB_TEMPORARY_ROOT`.
- Owner/runtime: `JOB_OWNER_USERNAME`, `JOB_OWNER_PASSWORD`, `JOB_TIME_ZONE`, `JOB_SITE_URL`, `JOB_DOMAIN`, `JOB_VPS_IP`.
- Monitoring/cache: `JOB_MONITORING_SCHEDULE`, `JOB_MONITORING_ENABLED`, `JOB_MONITORING_LEASE_SECONDS`, `JOB_NOTIFICATION_MAX_ATTEMPTS`, `JOB_MATCH_CACHE_ROOT`, `JOB_MATCH_CACHE_TIMEOUT_SECONDS`.
- Telegram reader/bot: `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `TELEGRAM_SESSION_PATH`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_OWNER_CHAT_ID`.
- Keyed sources: `WEB3_CAREER_API_TOKEN`, `WEB3_CAREER_FULL_DESCRIPTION_CONFIRMED`, `CRYPTOJOBS_LIST_API_KEY`, `CRYPTOJOBS_LIST_FULL_DESCRIPTION_CONFIRMED`, `REMOTE_ROCKETSHIP_API_KEY`, `REMOTE_ROCKETSHIP_ENABLED`, `REMOTE_ROCKETSHIP_ACTIVE_PLAN_CONFIRMED`.
- AI/search/cost: `OPENAI_API_KEY`, `OPENAI_MODEL`, `OPENAI_PRICES_JSON`, `TAVILY_API_KEY`, `TAVILY_COST_PER_CREDIT_USD`, `BRAVE_SEARCH_API_KEY`, `BRAVE_LLM_CONTEXT_COST_USD`.
- Backup: `BACKUP_AGE_RECIPIENT`; private `age` identity никогда не размещается на VPS.

## Тесты

- Django unit/integration: `jobs/core/tests/`, `jobs/models/tests/`, `jobs/operations/tests/`, `jobs/tests/`; один модуль запускается dotted path после `manage.py test`.
- Сквозной fixture pilot: `tests/acceptance/test_pilot_acceptance.py`; внешняя сеть и платные вызовы заменены deterministic fixtures.
- UI logic: `jobs/static/ui/tests/*.test.cjs`; browser harness и authenticated seed — `tests/acceptance/`.
- Browser acceptance требует локальный Chrome и проверяет 5 страниц × 5 viewport × 2 themes, Axe, console, overflow, drawer и draft save/copy.
- Deployment contracts находятся в `jobs/operations/tests/test_operations.py`; Linux restore faults — `tests/acceptance/restore-faults.sh`.

## Подводные камни

- Bare Django не читает `.env`; переменные задаются процессу, а `deployment/.env` читает только Compose/wizard.
- `TELEGRAM_API_ID` и `TELEGRAM_API_HASH` пока можно оставить пустыми: reader будет не настроен; Bot API — независимый компонент.
- Registry содержит 50 источников, но это catalog coverage, не утверждение о 50 live integrations; restricted/paid sources честно остаются `needs_access`/disabled.
- Matching cache долговечен и приватен; его ключ включает точный текст вакансии, criteria и profile version, а `force` не возвращает stale cache.
- `temporary/`, `.env`, Telegram sessions, WAL/SHM и caches не входят в backup; snapshot принимает только format 1 и точный allowlist.
- Production пока не развёрнут: Caddy/HTTPS, live APIs, restart и restore drill требуют отдельного разрешения; существующие Eggent, SkyPay и blog services на VPS неприкосновенны.

## Как здесь работает Autopilot

Сборка ведётся навыком `autopilot`. Состояние — `.autopilot/state.js`,
прогресс — `.autopilot/dashboard.html`. Требования может отменять только пользователь.
Локальные таски `.scratch/` должны ссылаться на соответствующие таски сборки.
Пользователь дал отдельную команду начать реализацию 20.09.2026. Выполнять тикеты
по зависимостям из плана `docs/planning/README.md`.
Канонические тексты тикетов — `.scratch/job-search-mvp/issues/`; в `.autopilot/`
хранятся ссылки и состояние.
<!-- autopilot:end -->
