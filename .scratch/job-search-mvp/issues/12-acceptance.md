# 12 — Сквозная приёмка пилота и дизайн-аудит

Status: done
Execution: completed locally 2026-09-21; live production checks remain explicitly separate
GitHub-Issue: #12 — https://github.com/Babka9981/Job_System/issues/12
Blocked by: 01,02,03,04,05,06,07,08,09,10,11
Волна: 7
Зона: `tests/acceptance/` · `docs/acceptance/`
Требования: R38, R70, R71, G04
Спецификация: S10

## Что должно заработать

Сквозная приёмка пилота и дизайн-аудит. Полный пользовательский путь включает данные, сервис, интерфейс и тесты
там, где применимо. Приёмка не отменяет отсутствующие подключения. Финальный результат отделяет реализацию и live production acceptance.

## Из брифа, дословно

> «всего 50 источников»
>
> «Критерии готовности первой рабочей поставки:»
>
> «Первую поставку с частью источников называть пилотом.»

## Обязательный контекст

- `Job_Search_MVP_TZ.md` и дополнения в `.autopilot/2026-09-20-job-search-mvp--wip/2026-09-20-brief.md`.
- `.autopilot/2026-09-20-job-search-mvp--wip/spec.md`, разделы S10.
- `.autopilot/2026-09-20-job-search-mvp--wip/interfaces.md`.
- Для UI: `design-system/README.md`, DESIGN_SPEC.md, tokens.css и ACCEPTANCE_CHECKLIST.md.
- API-факты: `docs/planning/integrations.md`; перед реализацией повторно проверить контракт.

## Критерии приёмки

- [x] Все 10 критериев исходного ТЗ имеют evidence + verified/mock-only/blocked status.
- [x] 50 источников заведены, фактическое покрытие честно посчитано; live Himalayas/Jobicy повтор не дублирует.
- [x] Полный путь CV-confirm → collect → match → research → оба draft → edit/copy → status → digest проверен.
- [x] Обе темы на 1920/1280/992/768/390, keyboard/drawer/focus, 200% text, no page overflow.
- [x] Axe serious/critical=0, контраст текста >=4.5:1, clean console; состояния loading/empty/error/403/conflict/offline.
- [x] SSRF/permissions/CSRF/prompt-injection/budget/TTL/restart отрицательные тесты проходят.
- [x] Независимая проверка по исходному брифу, отклонения/непроверенные интеграции перечислены; поставка названа пилотом.

## Проверки и свидетельства

Автоматические проверки через публичные сервисные границы и Django test client;
провайдеры заменяются fixtures. Реальные API/доставка/LLM/VPS проверяются отдельно.
Зафиксировать команды, результаты и ограничения в отчёте тикета.
Изменения shared schema/миграций согласовать с владельцем core и выполнять
последовательно: соседние тикеты одной волны не правят одни файлы.
Не считать блокированный внешний сценарий прошедшим.

## Comments

Подготовлен 20.09.2026. Реализация не запускалась.
