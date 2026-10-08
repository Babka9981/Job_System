# T21 — Подключение Telegram к штатному сбору

Status: in-progress

## Запрос пользователя

«Давай начнем с подключения телеграм каналов. У меня есть `API_ID`/`API_HASH`».
После запуска мастера авторизации: «готово».

## Основание

G18: подключить авторизованную пользовательскую Telegram-сессию и сбор каналов.
D06: inspection 09.10.2026 установил, что `collect_sources` пропускает Telegram:
`default_adapters` не содержит reader, а `run_cycle` вызывает только collector.
Сессия production проверена: authorized=true, без вывода personal data/secrets.

## Результат

- Штатный collector выполняет TelegramReader только для включённых Telegram-источников выбранного owner и source_slugs.
- Использовать существующие parser, permission, seven-day initial window, checkpoint и update contracts; не переписывать reader под Batch.
- Telegram работает под общей collector lease и fencing: не записывать при утрате lease; не разрешать параллельное использование сессии одним owner.
- Runtime client открывается лениво, освобождается всегда; отсутствии ключей/сессии соответствует безопасный статус источников, прочие источники продолжают работу.
- Отчёт включает Telegram successes/failures/records и не обещает LLM permission по факту доступности канала.
- FloodWait останавливает дальнейшие запросы в этой попытке без ожидания внутри процесса; сохраняется корректная повторная попытка.
- Добавить scoped команду пробного сбора Telegram без LLM, matching, notifications или включения мониторинга. Она выводит только агрегаты/slug/status, никогда raw text или upstream errors.
- Feature tests fixtures: collector invocation, owner/enabled/source filters, isolation failure, cleanup, lease loss, no configuration and FloodWait. Existing feature contracts остаются зелёными.
- Канонический локальный тикет `.scratch/job-search-mvp/issues/21-telegram-live-collection.md` и GitHub mirror создать при реализации.

## Границы

Python/Django current dependencies; schema migrations и новые зависимости не нужны.
Не менять пользовательское подтверждение профиля/LLM permissions, не включать monitoring.
Никаких keys/session/CV contents в outputs/files/commits. Production deployment выполняет root отдельно после review/tests.

## Контракты/проверки

Читать `.autopilot/2026-09-20-job-search-mvp--wip/interfaces.md` полностью и spec разделы про Telegram/monitoring.
Команды: `.venv/Scripts/python.exe manage.py test jobs.tests.test_telegram jobs.tests.test_public_sources jobs.tests.test_monitoring`.
Single file: `.venv/Scripts/python.exe manage.py test jobs.tests.test_telegram`.
