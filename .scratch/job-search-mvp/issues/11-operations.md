# 11 — VPS, HTTPS, резервирование и срок хранения

Status: done
Execution: completed locally; remote deploy intentionally not performed
GitHub-Issue: #11 — https://github.com/Babka9981/Job_System/issues/11
Blocked by: 05,06,09,10
Волна: 6
Зона: `deployment/` · `jobs/operations/` · `docs/operations/`
Требования: R01, R32, R65, R66, R68, R69
Спецификация: S10,S11

## Что должно заработать

VPS, HTTPS, резервирование и срок хранения. Полный пользовательский путь включает данные, сервис, интерфейс и тесты
там, где применимо. Не менять действующие сервисы VPS вслепую. Backup destination/retention согласуется до production; инструкции должны быть воспроизводимыми.

## Из брифа, дословно

> «Личный сервис для одного пользователя на его VPS»
>
> «Remote Rocketship»
>
> «Личный вход, закрытая регистрация, HTTPS.»

## Обязательный контекст

- `Job_Search_MVP_TZ.md` и дополнения в `.autopilot/2026-09-20-job-search-mvp--wip/2026-09-20-brief.md`.
- `.autopilot/2026-09-20-job-search-mvp--wip/spec.md`, разделы S10,S11.
- `.autopilot/2026-09-20-job-search-mvp--wip/interfaces.md`.
- Для UI: `design-system/README.md`, DESIGN_SPEC.md, tokens.css и ACCEPTANCE_CHECKLIST.md.
- API-факты: `docs/planning/integrations.md`; перед реализацией повторно проверить контракт.

## Критерии приёмки

- [x] Docker Compose Django+SQLite persistent disk+Caddy и OS scheduler config документированы, secrets private.
- [x] Production checks hosts/CSRF/secure cookies/DEBUG; private CV/session недоступны из static/media.
- [x] TTL cleanup во всех чтениях и перед backup; Rocketship raw/derived/temporary исключены из постоянных копий.
- [x] Backup/restore проверяет profile/user status/checkpoint/CV, без credentials и TTL data.
- [x] VPS readiness checklist ресурсы/порты/существующие сервисы/domain/outbound доступ выполнен до deploy.
- [x] Локальный restart smoke, health, migrations, operator env setup без вывода ключей.
- [x] Без VPS доступа пакет готовности отмечен, удалённый deploy не заявляется выполненным.

## Проверки и свидетельства

Автоматические проверки через публичные сервисные границы и Django test client;
провайдеры заменяются fixtures. Реальные API/доставка/LLM/VPS проверяются отдельно.
Зафиксировать команды, результаты и ограничения в отчёте тикета.
Изменения shared schema/миграций согласовать с владельцем core и выполнять
последовательно: соседние тикеты одной волны не правят одни файлы.
Не считать блокированный внешний сценарий прошедшим.

## Comments

Подготовлен 20.09.2026. Реализация не запускалась.
