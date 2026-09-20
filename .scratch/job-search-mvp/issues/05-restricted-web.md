# 05 — API с ключами, RVC и временные карточки

Status: ready-for-agent
Execution: not-started; запуск только по отдельной команде пользователя
GitHub-Issue: #5 — https://github.com/Babka9981/Job_System/issues/5
Blocked by: 04
Волна: 4
Зона: `jobs/sources/keyed/` · `jobs/sources/rvc/` · `jobs/tests/test_keyed_sources.py`
Требования: R21, R30, R31, R32, R33, R34, R37
Спецификация: S4,S10,S11

## Что должно заработать

API с ключами, RVC и временные карточки. Полный пользовательский путь включает данные, сервис, интерфейс и тесты
там, где применимо. До включения подтвердить индивидуальные API-условия. Не имитировать неподтверждённые поля контрактов. Требования ключа закрывают пользователи локально, не в issue body.

## Из брифа, дословно

> «Добавление нового сайта требует поддержанного способа подключения»
>
> «Web3.career»
>
> «RVC»

## Обязательный контекст

- `Job_Search_MVP_TZ.md` и дополнения в `.autopilot/2026-09-20-job-search-mvp--wip/2026-09-20-brief.md`.
- `.autopilot/2026-09-20-job-search-mvp--wip/spec.md`, разделы S4,S10,S11.
- `.autopilot/2026-09-20-job-search-mvp--wip/interfaces.md`.
- Для UI: `design-system/README.md`, DESIGN_SPEC.md, tokens.css и ACCEPTANCE_CHECKLIST.md.
- API-факты: `docs/planning/integrations.md`; перед реализацией повторно проверить контракт.

## Критерии приёмки

- [ ] Web3 token role queries <=100, limit status, immutable apply_url; query token никогда не логируется.
- [ ] CryptoJobsList x-api-key, page/totalPages, canonicalURL, 4 цикла, idempotence; full description field подтверждён.
- [ ] RVC официальный MCP teaser search/get_job <=10, attributed_url; нельзя выдавать teaser за полное JD.
- [ ] Rocketship plan/quota/pagination и 24h temporary store покрывают raw/derived/cache; без perpetual archive.
- [ ] Rocketship может работать временно без разрешения permanent ID links; это ограничение показано честно.
- [ ] Remocate/ITCharm доступный разрешённый канал проверен; если не найден — конкретный not-ready и manual link.
- [ ] 401/429/timeouts/expired data и нет ключа не мешают public sources; live tests отмечены credential-gated.

## Проверки и свидетельства

Автоматические проверки через публичные сервисные границы и Django test client;
провайдеры заменяются fixtures. Реальные API/доставка/LLM/VPS проверяются отдельно.
Зафиксировать команды, результаты и ограничения в отчёте тикета.
Изменения shared schema/миграций согласовать с владельцем core и выполнять
последовательно: соседние тикеты одной волны не правят одни файлы.
Не считать блокированный внешний сценарий прошедшим.

## Comments

Подготовлен 20.09.2026. Реализация не запускалась.
