# 10 — Расписание, восстановление и Telegram-подборка

Status: ready-for-agent
Execution: not-started; запуск только по отдельной команде пользователя
GitHub-Issue: #10 — https://github.com/Babka9981/Job_System/issues/10
Blocked by: 04,05,06,07
Волна: 5
Зона: `jobs/monitoring/` · `jobs/notifications/` · `jobs/tests/test_monitoring.py`
Требования: R56, R57, R58, R59, R63
Спецификация: S9,S11

## Что должно заработать

Расписание, восстановление и Telegram-подборка. Полный пользовательский путь включает данные, сервис, интерфейс и тесты
там, где применимо. Реальная доставка — credential-gated acceptance с владельцем; тестовый bot transport проверяет format/dedupe независимо.

## Из брифа, дословно

> «09:00, 13:00, 17:00 и 21:00»
>
> «Стартовая загрузка — до 7 последних дней»
>
> «После цикла — одна подборка новых подходящих вакансий и требующих уточнения»

## Обязательный контекст

- `Job_Search_MVP_TZ.md` и дополнения в `.autopilot/2026-09-20-job-search-mvp--wip/2026-09-20-brief.md`.
- `.autopilot/2026-09-20-job-search-mvp--wip/spec.md`, разделы S9,S11.
- `.autopilot/2026-09-20-job-search-mvp--wip/interfaces.md`.
- Для UI: `design-system/README.md`, DESIGN_SPEC.md, tokens.css и ACCEPTANCE_CHECKLIST.md.
- API-факты: `docs/planning/integrations.md`; перед реализацией повторно проверить контракт.

## Критерии приёмки

- [ ] Timezone-aware 4 daily slots редактируются, нет timezone => disabled; DST/restart тесты.
- [ ] Один lease и provider-specific cadence, bounded retries, checkpoints и single-cycle errors.
- [ ] Digest owner-only fit/clarify new, top10+list URL, причины и исходные ссылки.
- [ ] Повтор цикла не уведомляет повторно; outbox tracks confirmed/failed/uncertain delivery.
- [ ] Таймаут неопределённой отправки не выдаётся за exactly-once; явный retry/reconcile action.
- [ ] Нет bot/chat/site URL/credentials => честный статус; collector и browser не отправляют отклики.

## Проверки и свидетельства

Автоматические проверки через публичные сервисные границы и Django test client;
провайдеры заменяются fixtures. Реальные API/доставка/LLM/VPS проверяются отдельно.
Зафиксировать команды, результаты и ограничения в отчёте тикета.
Изменения shared schema/миграций согласовать с владельцем core и выполнять
последовательно: соседние тикеты одной волны не правят одни файлы.
Не считать блокированный внешний сценарий прошедшим.

## Comments

Подготовлен 20.09.2026. Реализация не запускалась.
