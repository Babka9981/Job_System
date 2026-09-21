# Спецификация: персональный поиск работы — пилот

Статус: подготовка. Выполнение тикетов запрещено до отдельной команды пользователя.
Исходное ТЗ: `Job_Search_MVP_TZ.md` v0.3. Дополнения от 20.09.2026 имеют приоритет:
OpenAI + отдельный веб-поиск; все критерии настраиваются; опыт извлекается LLM из
загруженного CV и проверяется владельцем.

## S1. Доступ, данные и оболочка

Один Django-проект, серверные HTML, SQLite на постоянном диске. Django 5.2 LTS,
Python 3.13 — предлагаемые базовые версии, patch-релизы фиксируются при T01 после
проверки актуальных security releases. Никакого React/Vue-приложения не требуется:
дизайн-система прямо допускает независимость от фреймворка.
Один владелец, session auth, закрытая регистрация, CSRF, ограничение попыток входа,
logout POST. Неавторизованный доступ ведёт на вход; иной пользователь получает 403.
CLI создаёт владельца локально. Секреты — env/private volume, не браузер, не логи.
Production DEBUG=false, HTTPS Caddy, secure cookies, явные hosts/origins.
Три раздела: Вакансии, Мой профиль, Источники. Настройки помещаются в профиль и
источники; отдельная аналитическая панель не создаётся.

UI полностью следует design-system/DESIGN_SPEC.md, tokens.css и checklist:
Inter с системными fallback, общие server partials AppShell, Sidebar/Drawer, Topbar,
PageHeading, Card/MetricCard (только где нужны), FieldGrid, Toolbar/ActionGroup,
StatusBadge, FeedbackBanner (5 тонов), TableContainer, Empty/Loading/Error,
ThemeToggle, Dialog, Pagination. Собственные outline SVG; материалы Vuexy не копировать.
Desktop sidebar 16rem/topbar 4.25rem fixed, скрытие/возврат sidebar. До 992px drawer
с backdrop, Escape, закрытием при навигации, focus trap/return. До 672px одна колонка.
Тема до первого paint: system default, localStorage и storage sync между вкладками.
Все цвета/радиусы/тени/размеры оболочки из tokens.css. Если измерение контраста покажет
дефект токена, исправление только в исходном наборе с записью отклонения.
44px controls, labels/aria-describedby, skip-link, landmarks, focus-visible,
aria-current/expanded/controls, alert/live status. Глобального overflow нет.
Страницы проектируются для loading/refresh/ready/empty/error+retry/403/saving/success/
version conflict/offline; ошибки форм сохраняют ввод, double-submit блокируется.
При конфликте версии изменения не перетираются, предлагается загрузить текущую версию.

## S2. Вакансии и пользовательское состояние

Список: текстовый поиск, фильтры роль/источник/приоритет/статус, пагинация.
Порядок remote > relocation > остальное, затем дата публикации; неизвестная дата не
заменяется текущей. Карточка: название, компания, роль, отрасль, формат, страны/timezone,
оплата, язык, published_at, first_seen_at, last_checked_at, исходный текст, все ссылки,
атрибуция, контакт только если в объявлении, причины и вопросы.
Оплата — min/max/currency/period/basis/fixed-or-total/bonus/equity; unknown сохраняется.
User state new/saved/applied/interview/closed, отдельные hidden и closing_note
(обязательна при closed). Availability unknown/active/removed независимо от user state.
Редактируемая заметка, сохранение, скрытие, возврат скрытого фильтром.
Ручной URL + разрешённый текст: происхождение и право обработки фиксируются отдельно;
вставка Telegram-текста не даёт права LLM. URL только HTTP(S), без автоматического
доверия введённому домену. HTML источника очищается, никогда не исполняется.
SourceRecord связывает source_id/external_id/canonical_url/apply_url с Vacancy.
Идемпотентность внутри источника определяется source+external_id, а при отсутствии ID —
source+точный URL. Между разными источниками URL сам по себе никогда не является
доказательством дубля: объединение разрешено только по совпадающему непустому
нормализованному содержимому либо по permalink, который конкретный adapter подтвердил
как идентификатор объявления. Ручной URL остаётся отдельной записью до явного
подтверждения пользователя или такого adapter evidence. Company+title недостаточно;
разные страны/команды не объединять. Неуверенный случай остаётся отдельным. Ссылки и
пользовательские состояния сохраняются при подтверждённом объединении.

Изменено после T02: универсальная URL-эвристика не различает job detail и listing/category
без знания конкретного источника. Требование дедупликации сохранено, unsafe inference
удалён; основание D01/G06.

## S3. CV, профиль и настройки

PDF/DOCX до 10 MB (изменяемый технический лимит R16.1), проверка формата,
ограничение распаковки/страниц, приватное хранение.
Извлечение текста с маркерами страниц; DOCX без гарантии физической нумерации помечается
как блоки документа. Пустой/скан/повреждённый файл => ручной ввод, оригинал не менять.
Из текста OpenAI извлекает предыдущий опыт, достижения, кейсы, языки в черновик с
ссылками на страницы/блоки. Владелец проверяет и подтверждает; до этого персонализация
не доступна. Обо мне/кейсы редактируемы; сохранение ручной правки подтверждает её
и меняет version. Новый автоматический импорт требует отдельной проверки.
Не переносить факты ТЗ как подтверждённый актуальный CV.
Противоречивые даты/текущая роль остаются вопросами, не догадками.

Критерии: три направления и перечисленные в ТЗ синонимы, fintech/crypto отрасли,
remote/relocation, countries/residence/hiring restrictions, timezone, языки,
salary target/currency/period/gross-net-B2B-unknown. Начальная цель USD 5000/month
помечена допущением и редактируема, basis unknown => salary hard filter off.
Seniority не жёсткий фильтр. Настройки языка, тона, длины, акцента отклика.
Расписание редактируемое 09:00/13:00/17:00/21:00; без timezone не включать.
Суточный бюджет LLM+поиск+чтение настраивается; без заданного положительного лимита
платные автоматические вызовы выключены. Показать pending при исчерпании.

## S4. Реестр и сайты

Seed идемпотентно создаёт ровно 9 сайтов и 41 Telegram-источник из ТЗ. Startup Jobs,
Remotive и компании не входят. Добавление/отключение Telegram по ссылке; сайт —
только поддержанный adapter. Для каждого: enabled, access mode, last success/check,
error, reason, needs credentials/reconnect, coverage window. Частота проверки сервисом
и частота обновления данных поставщиком показываются как два разных показателя.
Статус «работает» только после успешной проверки; 0 результатов не скрывает ошибки.

Remote OK: GET /api, пропустить metadata entry, одна лента за цикл, точные URL,
атрибуция Remote OK + follow-ссылка. Окно ограничено.
Himalayas: role search с сортировкой recent и постраничной выдачей обязателен;
актуальный endpoint/параметры проверить в T04. Общий GET /jobs/api limit<=20,
cursor/nextCursor используется как дополнительный feed. Ежедневное обновление,
не чаще раза в сутки. Поддержать plural fields
timezoneRestrictions/categories из живого payload, страны и salaryPeriod. Атрибуция.
Jobicy: /api/v2/remote-jobs count<=200, целевые tags, industry не трактовать как сектор
компании; не чаще часа, 4 цикла в сутки, нет подтверждённой пагинации => cap warning.
Web3.career: token, role/tag queries, <=100 без cursor; неизменный apply_url и attribution.
Токен из query редактируется в логах. Имя параметра full description проверить с docs.
CryptoJobsList: /public/jobs x-api-key, page/limit<=100, publishedAt/query/tags/remote,
canonicalURL. Проверять в каждом из четырёх циклов с учётом лимитов поставщика.
Подключить только после ключа и подтверждения индивидуальных условий;
поле полного текста проверить с доступом. Отсутствие ключа не блокирует остальные.
RVC: HTTP MCP штатный tool search/get_job, max10, masked_id, attributed_url, teaser-only;
страницы не скрапить, полнота/письмо по JD не заявляются без разрешённого полного текста.
Remote Rocketship: официальный API, платный план/квота, pagination; только temporary
store с hard TTL<=24h для raw/normalized/LLM-derived/cache/logs/backups. Notes/profile
отдельно. До подтверждения допустимости длительного хранения IDs/URLs persistent
link не обещать; временные карточки разрешены при доступном API-плане, даже если
постоянное связывание пока не согласовано. Заблокирован только permanent archive/link.
Remocate/ITCharm: нет подтверждённого разрешённого массового канала => disabled/not-ready
с причиной и ручной ссылкой. Разрешённый канал исследуется в T05; не делать scraper.

Collector единый lease с expiry и owner token, ограниченные retries/backoff/timeouts,
ошибка одного source не останавливает остальные. Checkpoint только после успешной
обработки страницы/поста, повтор частичной страницы идемпотентен. Начальное окно до
7 дней если доступно, потом новые/изменённые; более узкое окно явно показать.
Не получать заново LLM-оценку неизменного текста. Нет записи в feed != removed.

## S5. Telegram intake

Пользовательский API и session, не notification bot. Локальный интерактивный login,
session приватно; источник проверять на доступ, все 41 адреса сохранять даже при отказе.
Checkpoint message_id + контроль редактирований, ссылки на конкретные сообщения,
private link с отметкой о доступе. Несколько вакансий в посте — выделять части
детерминированными правилами, неоднозначный текст помечать для ручной проверки.
Delete/edit применять по доступным событиям/проверкам; не обещать недоступную историю.
По умолчанию только обычные текстовые фильтры. LLM permission с основанием отдельно,
авторизация, forward и manual paste его не заменяют. Без разрешения полный LLM
анализ/генерация по этому тексту блокируется; допустим независимый разрешённый JD.

## S6. Оценка и бюджет

Широкий локальный фильтр + один structured OpenAI extraction/match на разрешённый
неизменённый текст; результаты keyed by content+criteria/profile version.
Различать Product Manager от Marketing/Designer, руководителя поддержки от оператора/
sales Customer Success; обязанности имеют приоритет над совпадением слова.
fit/clarify/reject, 1–3 причины и вопросы. Unknown не отрицательный ответ.
USD annual fixed /12 только на одинаковой basis, hourly без часов/total comp без fixed
не сравнивать. Non-USD без dated FX => clarify. Вилка пересекает цель => переговоры.
Reject salary только вся известная сопоставимая вилка ниже цели и basis подтверждена.
Remote страны/timezone и права найма отделять, employer worldwide не даёт права работы.

Общий usage ledger и атомарное резервирование расходов до вызова, реальная стоимость
после ответа; conservative max reservation, параллельные запросы не обходят cap.
Настраиваемые цены по провайдеру/модели, неизвестная цена не означает ноль. Search/read
тоже учитываются. Нет ключа/лимита — явное unavailable/pending, не фиктивный результат.

## S7. Исследование компании

SearchProvider abstraction: отдельный API; конкретный поставщик ещё не выбран.
До выбора допускается fixture для тестов и disabled production adapter, не притворяться
веб-поиском. T08 начинает с утверждения поставщика и его цен/условий. OpenAI для
synthesis/generation. API model настраивается, фиксируется после проверки docs.
Public fetcher валидирует HTTP(S), DNS IPv4/IPv6 и каждый redirect, блок private/
loopback/linklocal/reserved; DNS rebinding через pinning или проверенный транспорт.
Ограничить bytes/time, content type, redirect count; credentials источников не передавать.
Query содержит company/product/role, не CV/contact. До 5 queries, 8 прочитанных страниц,
90 секунд — редактируемые caps, не SLA. Сниппет только для выбора страницы.
Определить компанию и official domain; ambiguity => спросить домен, до этого только JD.
Изучить продукт/about/careers/blog/docs и надёжные внешние публикации/интервью/запуски/
партнёрства; role-aware выбор. Приоритет новостям последних 12 месяцев.
3–5 фактов с URL, supporting passage, checked_at, event/publication date если известна;
hypotheses/conflicts отдельно; неоднозначные/противоречивые сведения не использовать
в отклике как установленные факты. Сомнительный негатив/отзывы не использовать в первом письме.
2–3 кейса из профиля со ссылкой на запись/CV. Cache company+domain 24h при допустимых
условиях, роль проверяется на достаточность, refresh/domain/company invalidate;
профиль version меняет подбор кейсов без повторения общего поиска.
В OpenAI передавать только необходимые конкретной операции данные профиля и разрешённые
тексты: extraction получает CV без лишних вложений, generation — релевантные проверенные
кейсы/факты; контакты и полный CV в generation не передаются без необходимости.
Job/CV/web content — данные, не инструкции к инструментам. LLM не имеет аккаунтных
инструментов или отправки сообщений. URL/цитаты валидировать по реально прочитанным pages.
Недоступный site => другие страницы; timeout/budget/no results => partial/unavailable,
никаких вымышленных новостей. Ход и источники видны в карточке, отдельный экран не нужен.

## S8. Два отклика

cover_letter/recruiter_message: обе кнопки вызывают общий research-or-cache, требуют
проверенный профиль и разрешённый текст. Missing JD => явно limited draft, не достраивать
роль по новостям. Письмо 1–2 факта и кейсы, сообщение один аргумент и короче.
Язык RU/EN, тон, краткость, акцент и user notes; recruiter contact только из объявления.
Редактор, сохранение, copy, источники вне копируемого текста. Никакой автоматической
отправки. Provenance связывает факты с page и profile record/version. При partial
research предложить маркированный draft по профилю/JD. При OpenAI failure сохранить
предыдущий draft и дать retry, не имитировать успешную генерацию.

## S9. Мониторинг и доставка

Команда collect и планировщик ОС, timezone/DST-aware расписание; перед повторным
циклом lease, provider frequency, last success; checkpoint и recovery.
Один digest fit/clarify новых вакансий, top10 + полный список, причины и точные ссылки.
Notification outbox: отправлять только owner chat, bot отдельно от reader account,
подтверждённые delivery не повторять. При неопределённом send timeout отметить
delivery uncertain: не обещать exactly-once у Telegram без idempotency key.
Повторный цикл не создаёт повторных уведомлений. Ошибки source видны отдельно.
Без настроек bot/chat/site URL доставка не отмечается выполненной.

## S10. Эксплуатация и приёмка

Docker Compose + Caddy, постоянный том, команды миграции/создания owner/сбора/очистки/
backup/restore, env.example только имена. Health без secrets.
TTL cleaner исполняется до чтения/backup и по расписанию; backup исключает temporary
Rocketship store и приватные ключи. Restore тест на profile/state/checkpoint/resume.
VPS ресурсы, существующие сервисы, домен и исходящий доступ проверяются до deployment.
Deployment требует предоставленного доступа; сейчас планируется, не выполняется.
Никаких live вызовов платных API и сообщений до корректной настройки владельцем.

Приёмка: все 10 пунктов исходного ТЗ плюс design-system checklist. Django client/
service tests с fake HTTP проверяют happy/error/auth/CSRF/stale/duplicate/salary/role/
geo/TTL/budget/SSRF/cache/provenance. Live smoke Remote OK/Himalayas/Jobicy из приложения,
повторная обработка и отсутствие дублей; keyed API отдельно когда доступны.
UI обе темы на 1920x1080,1280x800,992x900,768x1024,390x844, keyboard, 200% text,
overflow, axe serious/critical=0, clean console. Показывать loading/empty/error/403.
Отчёт отдельно отмечает verified/mock-only/unverified/blocked; 50/50 заявлять только
после всех подключений. Код готовности и внешняя приёмка различаются.

## S11. Границы и швы

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

## S12. Нерешённое и исключения

Блокеры внешней активации: API secrets, search provider/price, VPS/domain, Telegram
account/bot/chat, подтверждение контентных прав и Rocketship terms. Эти вопросы
не блокируют T01–T04 и offline tests. CV загружается через готовую форму.
Ручной ввод настроек решает salary/geo/timezone — не выдумывать личные факты.
Исключены согласно ТЗ autoapply, отдельная аналитика, vector DB, обучение, direct
company monitoring, Startup Jobs/Remotive. Ничего не отменено молча.
GitHub зеркалирование задач/файлов авторизовано ранее; без доступа сохранять локально,
статус sync pending. Принудительно перезаписывать remote history нельзя.

## S13. Аккуратная таблица источников

Экран «Источники» сохраняет все десять полей и бизнес-логику, но получает отдельную
табличную композицию: явные ширины колонок, визуально отделённые строки, компактные
ячейки, читаемые badges, закреплённую шапку и закреплённую первую колонку при
горизонтальной прокрутке. Длинные причины безопасно переносятся внутри своей ячейки;
даты и короткие значения не дробятся на случайные строки.

Таблица размещается в собственной карточке без двойного padding и прокручивается
только внутри `TableContainer`; страница не получает горизонтальный overflow.
Metric cards над таблицей получают единый ритм и не конкурируют с данными.

Приёмка: обе темы; ширины 1920, 1280, 992, 768 и 390 px; keyboard focus области
прокрутки; zoom 200%; Axe без serious/critical; чистая console; существующие тексты,
статусы, owner scoping и ссылка управления Telegram сохраняются.

## S14. Проверка извлечённого текста CV

Успешно извлечённый CV показывается не как одна широкая monospace-строка, а как
читабельный документ: ограниченная комфортная ширина текста, системный шрифт,
нормальные переносы и отдельные визуальные блоки. Технические маркеры
`[Блок N]` / `[Страница N]` становятся компактными подписями и не конкурируют
с содержимым.

Точное значение `Resume.text` не меняется и продолжает передаваться LLM без
преобразований. Для буквальной сверки рядом доступен вторичный раскрываемый
«Исходный извлечённый текст» с безопасными переносами; это не редактор и не копия
в базе. Не распознанные строки не теряются.

Композиция должна выдерживать длинные строки, ссылки, смешанные RU/EN тексты,
200% zoom, обе темы и мобильный viewport без горизонтального overflow страницы.
Details/summary остаются клавиатурно доступными; owner scoping и действия
«Создать LLM-черновик» не меняются.

## S15. Рабочая конфигурация LLM-черновика

Setup и runtime используют реальную публичную API-модель `gpt-5-mini`, доступную
проекту владельца, вместо внутреннего имени `gpt-5.6-luna`. Для неё в setup-flow и
`.env.example` задаётся явная fail-closed таблица стандартных token prices из
официальной документации OpenAI: input `0.25`, output `2.00` USD за миллион токенов.

Значение профиля остаётся настраиваемым. Уже сохранённое неподдерживаемое значение не
должно молча отправлять CV или подменяться в базе: production recovery меняет только
поле модели на `gpt-5-mini` после явной диагностики, сохраняя остальные preferences.
API key не выводится; проверка конфигурации не отправляет CV и не вызывает платный API.

Перед исправлением regression test воспроизводит точный `unknown_price`. После него
profile extraction должен пройти price/model preflight, вызвать fixture transport и
сохранить черновик; настоящий OpenAI-вызов выполняется отдельно только после backup,
deploy и с пользовательским CV в обычном owner action.

## S16. Согласованный OpenAI deadline

Изолированный HTTP worker не должен молча обрезать разрешённый вызывающим кодом
socket timeout до 30 секунд. Верхняя граница worker должна позволять штатному OpenAI
transport завершить большой CV extraction, но родительский `run_http_exchange` остаётся
владельцем жёсткого deadline, убивает зависший subprocess и не передаёт secrets через
argv/environment.

Regression test обязан воспроизводить прежнее ограничение без live API: fake HTTPS
connection получает socket timeout больше 30 секунд при разрешённом transport deadline;
отдельно сохраняются clamp для невалидных/чрезмерных значений и существующие deadline,
network/error, body-size и secret-isolation контракты. После deploy повтор выполняется
только через budget-enforced gateway; uncertain прошлые попытки остаются pending.

## S17. OpenAI transport использует полный bounded timeout

`OpenAIResponsesTransport` не должен создавать отдельный меньший лимит 45 секунд поверх
worker maximum 120 секунд. Default request timeout задаётся централизованно и передаётся
как hard deadline в `run_http_exchange`; socket timeout равен оставшемуся времени в
границах worker. Явный caller deadline по-прежнему имеет приоритет и не расширяется.

Regression test без сети фиксирует: default transport передаёт примерно 120 секунд,
явный короткий deadline остаётся коротким, истёкший deadline fail-closed, budget
reservation settle/pending семантика не меняется, secrets/CV не попадают в argv/env/log.
Production retry выполняется штатным profile service и обязан создать сохраняемый draft.

## S18. Достаточный лимит structured profile output

Default `JOB_PROFILE_MAX_OUTPUT_TOKENS` равен 8000 для `gpt-5-mini`: production доказал,
что 4000 завершается incomplete/invalid response, а 8000 создаёт сохраняемый draft.
Значение остаётся environment override и участвует в консервативном budget reservation;
суточный лимит и точная model price продолжают ограничивать вызов до transport.

Regression test фиксирует передаваемый max_output_tokens=8000 и рассчитанный максимум
стоимости, без live API/CV. `.env.example` документирует значение; deployment wizard не
перезаписывает ручной override. Production уже использует 8000 и draft v2 доступен owner.
