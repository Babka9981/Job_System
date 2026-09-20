# 02 — Вакансии, ручное добавление и статусы

Status: ready-for-agent
Execution: not-started; запуск только по отдельной команде пользователя
GitHub-Issue: #2 — https://github.com/Babka9981/Job_System/issues/2
Blocked by: 01
Волна: 2
Зона: `jobs/vacancies/` · `jobs/templates/vacancies/` · `jobs/tests/test_vacancies.py`
Требования: R02, R07, R11, R13, R14, R15, R22, R23, R26, R60, R61, R62
Спецификация: S2,S11

## Что должно заработать

Вакансии, ручное добавление и статусы. Полный пользовательский путь включает данные, сервис, интерфейс и тесты
там, где применимо. Контракт upsert_record принимает NormalizedRecord из interfaces.md. Пока нет collectors — проверять fixture/manual пути, не seed вымышленные live вакансии.

## Из брифа, дословно

> «трёх экранов: «Вакансии», «Мой профиль», «Источники»»
>
> «Зарплату хранить вместе с валютой, периодом, gross/net и составом»
>
> «Вилки без зарплаты сохранять.»

## Обязательный контекст

- `Job_Search_MVP_TZ.md` и дополнения в `.autopilot/2026-09-20-job-search-mvp--wip/2026-09-20-brief.md`.
- `.autopilot/2026-09-20-job-search-mvp--wip/spec.md`, разделы S2,S11.
- `.autopilot/2026-09-20-job-search-mvp--wip/interfaces.md`.
- Для UI: `design-system/README.md`, DESIGN_SPEC.md, tokens.css и ACCEPTANCE_CHECKLIST.md.
- API-факты: `docs/planning/integrations.md`; перед реализацией повторно проверить контракт.

## Критерии приёмки

- [ ] Список/карточка/поиск/четыре фильтра/пагинация и remote-relocation-freshness порядок работают на сохранённых данных.
- [ ] Ручное добавление URL/разрешённого текста валидирует provenance/permission и очищает HTML.
- [ ] Пять user statuses, closing note, hide/unhide и availability независимы; optimistic conflict не теряет данные.
- [ ] upsert_record идемпотентен внутри источника по source+external_id, а без ID —
  по source+точному URL; cross-source URL сам по себе никогда не merge evidence.
- [ ] Cross-source merge разрешён только по совпадающему непустому нормализованному
  содержимому либо adapter-confirmed permalink; manual URL остаётся отдельным до
  явного подтверждения/adapter evidence, ambiguous всегда no-merge.
- [ ] Company+title без других доказательств и разные страны/команды не объединяются.
- [ ] Неизвестные даты/зарплаты/контакты не изобретаются; attr/apply URL видны точно.

## Проверки и свидетельства

Автоматические проверки через публичные сервисные границы и Django test client;
провайдеры заменяются fixtures. Реальные API/доставка/LLM/VPS проверяются отдельно.
Зафиксировать команды, результаты и ограничения в отчёте тикета.
Изменения shared schema/миграций согласовать с владельцем core и выполнять
последовательно: соседние тикеты одной волны не правят одни файлы.
Не считать блокированный внешний сценарий прошедшим.

## Comments

Подготовлен 20.09.2026. Реализация не запускалась.

Amendment 20.09.2026: пользователь подтвердил строгий dedup-контракт после того, как
сборка доказала небезопасность общей URL-эвристики. Основание D01/G06.
