# 04 — 50 источников и сбор открытых API

Status: ready-for-agent
Execution: not-started; запуск только по отдельной команде пользователя
GitHub-Issue: #4 — https://github.com/Babka9981/Job_System/issues/4
Blocked by: 01,02
Волна: 3
Зона: `jobs/sources/core/` · `jobs/sources/public/` · `jobs/templates/sources/` · `jobs/tests/test_public_sources.py`
Требования: R02, R20, R21, R28, R29, R35, R36, R38, R57, R60, R63
Спецификация: S4,S11

## Что должно заработать

50 источников и сбор открытых API. Полный пользовательский путь включает данные, сервис, интерфейс и тесты
там, где применимо. Не переподключать сайты по произвольному URL. Source register включает честные needs credentials/not-ready, включая все будущие адаптеры.

## Из брифа, дословно

> «трёх экранов: «Вакансии», «Мой профиль», «Источники»»
>
> «Предзаполненный список; добавление/отключение Telegram-источников»
>
> «Добавление нового сайта требует поддержанного способа подключения»

## Обязательный контекст

- `Job_Search_MVP_TZ.md` и дополнения в `.autopilot/2026-09-20-job-search-mvp--wip/2026-09-20-brief.md`.
- `.autopilot/2026-09-20-job-search-mvp--wip/spec.md`, разделы S4,S11.
- `.autopilot/2026-09-20-job-search-mvp--wip/interfaces.md`.
- Для UI: `design-system/README.md`, DESIGN_SPEC.md, tokens.css и ACCEPTANCE_CHECKLIST.md.
- API-факты: `docs/planning/integrations.md`; перед реализацией повторно проверить контракт.

## Критерии приёмки

- [ ] Seed даёт ровно 9 сайтов +41 Telegram и повторно не дублирует записи.
- [ ] Remote OK, Himalayas role search recent/page и Jobicy реальные адаптеры с точными URL/атрибуцией.
- [ ] Himalayas cursor feed/plural payload compatibility, ежедневная частота; Jobicy <=200 и >=1h интервал.
- [ ] Source screen показывает enabled/status/mode/last success/error/coverage, отдельно service interval/provider freshness.
- [ ] Lease, checkpoints after page, limited retry/backoff и per-source isolation; crash/resume не теряет и не дублирует.
- [ ] Кап/узкое окно видны; отсутствие записи в feed не означает removed; published и first seen различаются.
- [ ] Live smoke public API и повторная обработка фиксируются отдельно от unit tests.

## Проверки и свидетельства

Автоматические проверки через публичные сервисные границы и Django test client;
провайдеры заменяются fixtures. Реальные API/доставка/LLM/VPS проверяются отдельно.
Зафиксировать команды, результаты и ограничения в отчёте тикета.
Изменения shared schema/миграций согласовать с владельцем core и выполнять
последовательно: соседние тикеты одной волны не правят одни файлы.
Не считать блокированный внешний сценарий прошедшим.

## Comments

Подготовлен 20.09.2026. Реализация не запускалась.
