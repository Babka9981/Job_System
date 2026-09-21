# Контракты для начала разработки

Статус: проект, реализации нет. После команды пользователя T01 закрепляет эти границы
в коде; изменения контракта согласуются до запуска зависимых тикетов.

## Границы, решённые в спецификации


| Модуль | Владеет | Публичная граница | Скрывает |
|---|---|---|---|
| core | owner/auth, schema и migration ordering, shared UI | Django auth + ORM сущности | storage/settings/session |
| profile | CV, факты, criteria, version | extract_profile(resume), confirm_profile(draft, version) | parsers/OpenAI prompts |
| vacancies | карточка, источники-ссылки, user state | upsert_record(record), change_status(id, version, status, note) | merge/dedup |
| sources | adapter contract, registry, cursors, TTL | collect(source, cursor) -> Batch(records,next_cursor,coverage) | HTTP/MCP/Telegram |
| matching | structured match, salary/geo decisions | evaluate(vacancy, criteria) -> Assessment | rules/prompts |
| intelligence | OpenAI client, budget, public fetch/search, research/drafts | research(company,domain,role,refresh), generate(vacancy,profile,kind) | network/cache/prompts |
| monitoring | schedule, lease, runs, digest outbox | run_cycle(), deliver_digest(run) | timer/retries/bot |
| operations | compose/HTTPS/backup/cleanup | CLI documented commands | platform details |

Shared model contract T01: Owner via Django User; Profile(version,confirmed_version,
about,criteria,preferences); Resume(private_path,text,extraction_state); ProfileFact
(text,kind,source/page,profile_version,confirmed); Source(kind,adapter,config without secrets,
status,enabled,last_success,cursor,coverage,llm_permission); Vacancy(normalized fields,
user_status,availability,version); SourceRecord(source,external_id,URL,hash,permission,
expiry); Research(company,domain,role coverage,status,facts,sources,expires_at);
Draft(kind,text,profile_version,research,status,provenance); Run/Lease;
UsageReservation/Ledger; NotificationOutbox. Dates UTC in DB, user timezone in UI.
Temporary source content physically segregated for backup exclusion.

Tests at public service boundaries and Django HTTP, providers replaced by fixtures.
Core alone owns initial schema and migration registry; later model changes serialised
through owner before waves. UI partials shared, feature pages owned by feature tickets.
Dependency absent => report BLOCKED, do not invent success.


## Уточнение ownership

T03 владеет общими OpenAI gateway и Budget API, T07 переиспользует их;
T08 владеет search/fetch/research, T09 — генерацией. T04 владеет registry/collector,
T05 — keyed/RVC adapters, T06 — Telegram reader. T10 вызывает collector и notification bot.
Общие urls/template navigation/модели и migration numbering меняет владелец core
последовательно, а feature-specific handlers/tests/partials — соответствующий тикет.

## Формат записи источника

NormalizedRecord: source_slug, external_id, canonical_url, apply_url, title, company,
company_domain?, description?, description_permission, published_at?, expires_at?,
language?, role?, industry?, work_arrangement?, country_restrictions[], timezone_restrictions[],
salary{min?,max?,currency?,period?,basis?,component?,fixed{min?,max?,currency?,period?,basis?}?,bonus{value?,description?}?,equity_tokens{value?,description?}?}, contact?, raw_hash, attribution.
Каждая составляющая хранится отдельно; total не подменяет fixed. Неуказанные значения
остаются unknown. Исходное описание состава оплаты сохраняется для проверки.
Вопросительный знак означает unknown, не приглашение вывести значение из догадки.
Batch: records[], next_cursor?, coverage{window_start?,truncated,reason?}, provider_updated_at?.
Cursor — opaque, сохраняется после commit обработанной страницы.

## Handoff волны 2

- T02 implemented: Vacancy дополнена `closing_note`, nullable `last_checked_at` и `priority`:
  `remote | relocation | other`; приоритет независим от будущей оценки matching,
  сортировка `remote > relocation > other`.
- Core URL dispatcher делегирует вакансии в `jobs.vacancies.urls`, профиль —
  в `jobs.profile.urls`, сохраняя shell route names.
- PDF extraction использует `pypdf==6.19.0`; OpenAI gateway остаётся в зоне profile.

## T02 revision 1 — подтверждённый dedup

- Same-source identity: `source + external_id`; fallback без ID: `source + exact URL`.
- Cross-source URL никогда не является evidence сам по себе.
- Cross-source merge: только одинаковый непустой normalized content hash либо
  `adapter_confirmed_permalink=true` от конкретного source adapter.
- Manual URL не auto-merge; ambiguous всегда создаёт отдельную Vacancy.
- `SourceRecord.adapter_confirmed_permalink` и `description_permission` принимают
  только literal `True`; при смене URL/контента прежнее разрешение не переносится.

## Долгоживущие состояния

User status: new/saved/applied/interview/closed; hidden отдельно.
Availability: unknown/active/removed. Match: pending/fit/clarify/reject/error.
Research: pending/running/complete/partial/unavailable/needs-domain.
Draft: editing/generated/limited/error. Delivery: pending/sent/failed/uncertain.
Статусы «ошибка» и «0 вакансий» никогда не объединяются.

## Инварианты реализации

UTC хранение и timezone display. Decimal для денег. Отдельно source content и user notes.
Content permission проверяется до LLM; CV/Search/Jobs недоверенные данные без tool authority.
Ошибки upstream содержат код/безопасное описание, не credentials или полный ответ.
Использовать existing design-system как единственный набор токенов.

## Команды

Команды приложения пока отсутствуют. T01 должен создать и проверить реальные команды
установки, миграций, запуска и тестов; не считать будущую строку manage.py работающей.
Недостающая dependency сообщается как BLOCKED; установка по решению оркестратора.

## Реализовано в T07

- evaluate(vacancy, criteria) возвращает Assessment со статусом fit, clarify,
  reject или pending; hard reject применяется только к доказанным salary/geo
  ограничениям и явно нерелевантной функции без целевых обязанностей.
- LLM получает описание только при literal True permission и точном совпадении
  SHA-256 текущего Vacancy.description с SourceRecord.raw_hash.
- Salary сравнивает только совместимые fixed, currency, period и basis; unknown,
  hourly без часов, total comp без fixed и инвертированная вилка дают clarify.
- Worldwide не доказывает возможность оформления и всегда создаёт отдельный
  вопрос о праве найма; country и timezone ограничения не смешиваются.
- Результат неизменённого текста и profile version хранится в durable private
  cache без повторной оплаты; force обходит cache только после своего lease.
- Single-flight использует DB Lease: unique create и атомарный conditional
  takeover истёкшей записи. SQLite lock возвращает pending, release holder-scoped;
  конкуренты не становятся двумя платными лидерами.

## Реализовано в T04

- Registry идемпотентно создаёт ровно 50 предзаполненных источников: 9 сайтов и
  41 Telegram-источник; секреты в Source.config не сохраняются.
- Публичные adapters Remote OK, Himalayas и Jobicy возвращают NormalizedRecord;
  сеть ограничена timeout/retry/backoff, а сбой одного источника изолирован.
- Collector использует DB Lease с heartbeat и fencing, сохраняет cursor только
  после commit страницы и безопасно возобновляет interrupted run.
- Известный published_at старше 7 дней отбрасывается на каждой странице и в
  каждом цикле; неизвестная дата сохраняется. Неверная конфигурация fail-closed.
- SourceRecord.raw_hash — SHA-256 точного очищенного текста, сохранённого в
  Vacancy.description; description_permission и source binding задаются
  adapter-ом и не принимаются из недоверенной записи.
- Coverage хранит и одновременно показывает window_start, truncated и reason;
  экран /sources/ не падает на некорректном interval.

## Реализовано в T01

- HTTP: `/`, `/profile/`, `/sources/`, `/accounts/login/`, POST `/accounts/logout/`.
- CLI: `manage.py create_owner [--username USERNAME] [--no-input]`.
- ORM: `Profile`, `Resume`, `ProfileFact`, `Source`, `Vacancy`, `SourceRecord`,
  `TemporarySourceContent`, `Research`, `Draft`, `Run`, `Lease`,
  `UsageReservation`, `UsageLedger`, `NotificationOutbox`.
- Временное хранение: `TemporaryContentStorage`, корень `settings.TEMPORARY_ROOT`.
- UI: `JobDrawer.createDrawerController({shell,toggle,drawer,backdrop,isMobile,returnFocus,focusFirst})`;
  `drawer.js` — единственный владелец регистрации toggle-click.
- Проверки: `manage.py test`, `manage.py migrate`, `manage.py check --deploy`,
  `manage.py makemigrations --check --dry-run`.
# Из recovery таска 08 — пользовательский путь исследования

- `POST /vacancies/<vacancy_id>/research/` (`vacancy-research`) принимает `domain` и `refresh=0|1`.
- Карточка вакансии получает `latest_research` и показывает progress, evidence, sources, conflicts, hypotheses и profile cases.
- Подтверждённый нормализованный домен сохраняется в `Vacancy.company_domain` с увеличением версии.

# Из recovery таска 05 — keyed API и RVC

- `RvcStreamableHttpToolCaller(client_factory=None, timeout=20)` — production Streamable HTTP MCP boundary.
- `default_adapters()` подключает RVC через exact endpoint `https://app.rvc.global/mcp`; live вызов остаётся выключаемым source state.
- Remote Rocketship резервирует job capacity отдельно для каждой uncertain retry attempt; paid adapters по умолчанию выключены.

# Из таска 13 — AI-настройки и Brave

- `BraveSearchProvider.search(query, max_results=5, deadline=None, clock=None) -> list[SearchHit]`.
- `Profile.preferences.openai_model` по умолчанию `gpt-5.6-luna`; `search_provider` — `auto|tavily|brave`.
- `Research.coverage.provider_attempts[] = {query, provider, outcome}` сохраняет прозрачный fallback provenance.
- Секреты/стоимость: `BRAVE_SEARCH_API_KEY`, `BRAVE_LLM_CONTEXT_COST_USD`; неизвестная цена закрывает платный вызов.

# Из таска 10 — мониторинг и Telegram Bot

- `run_cycle(owner, *, adapters, evaluator, collector, transport, now, schedule_slot) -> Run`.
- `schedule_decision(*, now, timezone_name, slots) -> ScheduleDecision` учитывает timezone/DST.
- `deliver_digest(run, *, transport) -> NotificationOutbox | None`; `retry_delivery(...)` и `reconcile_delivery(...)` управляют uncertain delivery.
- `TelegramBotTransport(token=None, chat_id=None, timeout=10, opener=None)` использует отдельные `TELEGRAM_BOT_TOKEN` и `TELEGRAM_OWNER_CHAT_ID`, независимо от MTProto reader.

# Из таска 09 — черновики откликов

- `generate(vacancy, profile, kind, *, language, tone, length, accent, user_note, refresh, gateway, research_service) -> Draft`.
- UI: `GET /vacancies/<id>/drafts/`, `POST .../<kind>/generate/`, `POST .../<kind>/save/`.
- Генерация требует current confirmed profile и exact JD permission/hash; ошибка провайдера сохраняет предыдущий текст.

# Из таска 11 — операции и deployment bundle

- `GET /healthz/` — production health boundary без раскрытия приватных данных.
- Compose project `job-system` слушает только `127.0.0.1:18111`; Caddy публикует `job.web3babka.su` отдельным site block.
- Operations CLI выполняет TTL cleanup, SQLite snapshot и restore verification; backup шифруется `age` public recipient, identity хранится вне VPS.
- Backup retention 30 дней удаляется только после новой успешной копии и всегда сохраняет newest.
- `scripts/setup-integrations.sh` — repeatable 7-stage local `.env` wizard; MTProto stage можно отложить.
