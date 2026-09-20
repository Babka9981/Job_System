# 07 — Оценка вакансий и сопоставимость условий

Status: ready-for-agent
Execution: not-started; запуск только по отдельной команде пользователя
GitHub-Issue: #7 — https://github.com/Babka9981/Job_System/issues/7
Blocked by: 02,03
Волна: 3
Зона: `jobs/matching/` · `jobs/tests/test_matching.py`
Требования: R03, R04, R05, R06, R07, R08, R09, R10, R11, R12, R13, R24, R25, R27, R28, R42, R64, G01
Спецификация: S3,S6,S11

## Что должно заработать

Оценка вакансий и сопоставимость условий. Полный пользовательский путь включает данные, сервис, интерфейс и тесты
там, где применимо. Gateway/ledger переиспользуется из T03. Non-USD без FX остаётся clarify, источник курса не добавлять произвольно.

## Из брифа, дословно

> «Product Manager, Project Manager, руководитель службы поддержки»
>
> «Fintech и криптопроекты»
>
> «География работодателей»

## Обязательный контекст

- `Job_Search_MVP_TZ.md` и дополнения в `.autopilot/2026-09-20-job-search-mvp--wip/2026-09-20-brief.md`.
- `.autopilot/2026-09-20-job-search-mvp--wip/spec.md`, разделы S3,S6,S11.
- `.autopilot/2026-09-20-job-search-mvp--wip/interfaces.md`.
- Для UI: `design-system/README.md`, DESIGN_SPEC.md, tokens.css и ACCEPTANCE_CHECKLIST.md.
- API-факты: `docs/planning/integrations.md`; перед реализацией повторно проверить контракт.

## Критерии приёмки

- [ ] Широкий prefilter и один structured OpenAI extraction/match разрешённого текста дают fit/clarify/reject.
- [ ] Marketing/Designer/operator/sales-success не проходят по одному слову; роль определяется обязанностями.
- [ ] USD annual fixed/basis, salary crossing/unknown/hourly/total/nonUSD covered; hard reject только comparable.
- [ ] Country/timezone restrictions и неизвестное право найма отображаются с вопросами, не угадываются.
- [ ] Причины 1–3, questions; profile/criteria version и content hash управляют cache.
- [ ] Permission проверяется до gateway, бюджет/ошибки оставляют pending, повтор unchanged не оплачивается.

## Проверки и свидетельства

Автоматические проверки через публичные сервисные границы и Django test client;
провайдеры заменяются fixtures. Реальные API/доставка/LLM/VPS проверяются отдельно.
Зафиксировать команды, результаты и ограничения в отчёте тикета.
Изменения shared schema/миграций согласовать с владельцем core и выполнять
последовательно: соседние тикеты одной волны не правят одни файлы.
Не считать блокированный внешний сценарий прошедшим.

## Comments

Подготовлен 20.09.2026. Реализация не запускалась.
