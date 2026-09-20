# План разработки Job Search MVP

**Подготовлено; реализация остановлена до отдельной команды пользователя.**

ТЗ: [Job_Search_MVP_TZ.md](../../Job_Search_MVP_TZ.md).
Спецификация: [spec.md](../../.autopilot/2026-09-20-job-search-mvp--wip/spec.md).
Требования: [manifest.md](../../.autopilot/2026-09-20-job-search-mvp--wip/manifest.md).
Контракты: [interfaces.md](../../.autopilot/2026-09-20-job-search-mvp--wip/interfaces.md).
API-проверка: [integrations.md](integrations.md).
GitHub: [12 подготовленных Issues](https://github.com/Babka9981/Job_System/issues).
Канонические тикеты: `.scratch/job-search-mvp/issues/`. Автопилот содержит ссылки,
а не конкурирующие копии. GitHub Issues — синхронизируемое зеркало.

## Порядок

Ярус T3: независимые ingestion, profile/matching, research/drafting, monitoring.
12 тикетов, 7 волн. Волны означают допустимый порядок; не разрешение начать.

| Тикет | Результат | Зависимости | Волна |
|---|---|---|---|
| [01](../../.scratch/job-search-mvp/issues/01-foundation.md) | Закрытый вход и общая оболочка | — | 1 |
| [02](../../.scratch/job-search-mvp/issues/02-vacancies.md) | Вакансии, ручное добавление и статусы | 01 | 2 |
| [03](../../.scratch/job-search-mvp/issues/03-profile.md) | CV через LLM, проверяемый профиль и настройки | 01 | 2 |
| [04](../../.scratch/job-search-mvp/issues/04-public-sources.md) | 50 источников и сбор открытых API | 01,02 | 3 |
| [05](../../.scratch/job-search-mvp/issues/05-restricted-web.md) | API с ключами, RVC и временные карточки | 04 | 4 |
| [06](../../.scratch/job-search-mvp/issues/06-telegram-reader.md) | Telegram-источники и допустимая обработка | 04 | 4 |
| [07](../../.scratch/job-search-mvp/issues/07-matching.md) | Оценка вакансий и сопоставимость условий | 02,03 | 3 |
| [08](../../.scratch/job-search-mvp/issues/08-company-research.md) | Веб-исследование компании и доказательства | 03,07 | 4 |
| [09](../../.scratch/job-search-mvp/issues/09-drafts.md) | Два отклика с редактированием и provenance | 02,03,08 | 5 |
| [10](../../.scratch/job-search-mvp/issues/10-monitoring.md) | Расписание, восстановление и Telegram-подборка | 04,05,06,07 | 5 |
| [11](../../.scratch/job-search-mvp/issues/11-operations.md) | VPS, HTTPS, резервирование и срок хранения | 05,06,09,10 | 6 |
| [12](../../.scratch/job-search-mvp/issues/12-acceptance.md) | Сквозная приёмка пилота и дизайн-аудит | 01,02,03,04,05,06,07,08,09,10,11 | 7 |

## Что уже решено

- Django/server HTML/SQLite, Docker Compose/Caddy. Дизайн из design-system, обе темы.
- OpenAI + отдельный search API; выбор поставщика отражается в decision-log ниже.
- Критерии редактируемые; CV извлекает LLM, владелец проверяет профиль.
- Public источники не ждут ключей остальных. Частичная поставка называется пилотом.
- Все 50 источников сохраняются в registry с честными статусами.
- Только ручная отправка откликов. Секреты вводятся локально, не в GitHub/чат.

## Что потребуется позже

| Когда | Данные/решение | Блокирует |
|---|---|---|
| После команды старта | Ничего дополнительного для T01 | Реализацию сейчас блокирует только команда пользователя |
| T03 live | OpenAI key/model/budget и CV через форму | Реальную extraction/generation; mock tests доступны |
| T05 live | Web3/CryptoJobsList/Rocketship keys, plan/terms | Соответствующий adapter smoke, не public APIs |
| T06 live | Telegram account/session и допустимость content usage | Проверку41 источника и разрешённый LLM path |
| T08 live | Search provider/key/budget | Реальное исследование |
| T10 live | Bot token, owner chat, private site URL | Доставку владельцу |
| T11 deploy | VPS/domain/доступ/backup retention+destination | Удалённое развёртывание и restore на VPS |

## Приёмка и известные риски

Исходные10 критериев обязательны, плюс весь design-system/ACCEPTANCE_CHECKLIST.md.
T12 различает implemented/tested/live-verified/blocked. Успешные mock tests не равны
работающему аккаунту. Автообход ограниченных источников не обещается.
Rocketship content физически отделён с TTL24h; иначе backup создаст запрещённый архив.
Telegram message permissions проверяются до LLM. Site fetcher требует SSRF/DNS тестов.
При timeout отправки bot возможна неопределённость доставки — отдельное состояние.
Dark primary token может потребовать проверки контраста white-on-purple; проверять
измерением и корректировать исходный токен с записью отклонения, не заводить второй набор.

## Решения

20.09.2026: исходные роли CV из ТЗ не считать актуальным подтверждённым профилем;
владелец выбрал загрузку и LLM extraction. Подготовка заканчивается до начала T01.
