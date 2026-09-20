# 08 — Веб-исследование компании и доказательства

Status: ready-for-agent
Execution: not-started; запуск только по отдельной команде пользователя
GitHub-Issue: #8 — https://github.com/Babka9981/Job_System/issues/8
Blocked by: 03,07
Волна: 4
Зона: `jobs/intelligence/research/` · `jobs/intelligence/search/` · `jobs/intelligence/fetch/` · `jobs/tests/test_research.py`
Требования: R45, R46, R47, R48, R49, R50, R51, R52, R53, R54, R55, R67, G01
Спецификация: S7,S11

## Что должно заработать

Веб-исследование компании и доказательства. Полный пользовательский путь включает данные, сервис, интерфейс и тесты
там, где применимо. Выбор поставщика поиска и цены — входное условие live части, можно тестировать transport fixtures. Не использовать web snippets как проверенные факты.

## Из брифа, дословно

> «LLM не выдумывает опыт, достижения, зарплату, имя рекрутера или факты о компании.»
>
> «Если идентификация неоднозначна, запросить домен»
>
> «Для новостей по умолчанию приоритет последним 12 месяцам»

## Обязательный контекст

- `Job_Search_MVP_TZ.md` и дополнения в `.autopilot/2026-09-20-job-search-mvp--wip/2026-09-20-brief.md`.
- `.autopilot/2026-09-20-job-search-mvp--wip/spec.md`, разделы S7,S11.
- `.autopilot/2026-09-20-job-search-mvp--wip/interfaces.md`.
- Для UI: `design-system/README.md`, DESIGN_SPEC.md, tokens.css и ACCEPTANCE_CHECKLIST.md.
- API-факты: `docs/planning/integrations.md`; перед реализацией повторно проверить контракт.

## Критерии приёмки

- [ ] Search provider выбран/настроен, реальные search+read отличены от mock; до настройки status unavailable.
- [ ] SSRF tests покрывают IPv4/IPv6/DNS rebinding/redirect/private targets, byte/time bounds и credential isolation.
- [ ] Company/domain ambiguity требует уточнения; тёзки/агентство не смешиваются.
- [ ] Role-aware official/external sources, <=5 queries/8 pages/90s и общий budget; CV не попадает в search.
- [ ] Факты с actual page evidence/URL/dates/checked_at, conflicts/hypotheses отдельно и не утверждаются в тексте.
- [ ] 24h cache/refresh/company/domain invalidation и role sufficiency; profile changes обновляют cases.
- [ ] Failure/timeout/no evidence => partial/unavailable и прозрачный progress без фиктивных новостей.

## Проверки и свидетельства

Автоматические проверки через публичные сервисные границы и Django test client;
провайдеры заменяются fixtures. Реальные API/доставка/LLM/VPS проверяются отдельно.
Зафиксировать команды, результаты и ограничения в отчёте тикета.
Изменения shared schema/миграций согласовать с владельцем core и выполнять
последовательно: соседние тикеты одной волны не правят одни файлы.
Не считать блокированный внешний сценарий прошедшим.

## Comments

Подготовлен 20.09.2026. Реализация не запускалась.
