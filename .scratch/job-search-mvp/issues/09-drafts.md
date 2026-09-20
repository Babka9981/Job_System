# 09 — Два отклика с редактированием и provenance

Status: ready-for-agent
Execution: not-started; запуск только по отдельной команде пользователя
GitHub-Issue: #9 — https://github.com/Babka9981/Job_System/issues/9
Blocked by: 02,03,08
Волна: 5
Зона: `jobs/drafts/` · `jobs/templates/vacancies/drafts/` · `jobs/tests/test_drafts.py`
Требования: R19, R23, R42, R43, R44, R45, R49, R54, R55, G01
Спецификация: S8,S11

## Что должно заработать

Два отклика с редактированием и provenance. Полный пользовательский путь включает данные, сервис, интерфейс и тесты
там, где применимо. Не использовать неразрешённый Telegram текст в генерации. Generation получает минимальный набор релевантных фактов.

## Из брифа, дословно

> «персонализированные письма требуют заполненного профиля.»
>
> «Контакт Telegram показывать только если он действительно указан»
>
> «LLM-обработку Telegram-текста включать только при подтверждённом основании»

## Обязательный контекст

- `Job_Search_MVP_TZ.md` и дополнения в `.autopilot/2026-09-20-job-search-mvp--wip/2026-09-20-brief.md`.
- `.autopilot/2026-09-20-job-search-mvp--wip/spec.md`, разделы S8,S11.
- `.autopilot/2026-09-20-job-search-mvp--wip/interfaces.md`.
- Для UI: `design-system/README.md`, DESIGN_SPEC.md, tokens.css и ACCEPTANCE_CHECKLIST.md.
- API-факты: `docs/planning/integrations.md`; перед реализацией повторно проверить контракт.

## Критерии приёмки

- [ ] Обе кнопки запускают общий research или свежий cache, второе действие не делает повторный company поиск.
- [ ] Verified profile/permission gates; неполное JD => явно limited, требования роли не выдумываются.
- [ ] Cover 1–2 company facts+cases, recruiter один уместный аргумент; RU/EN/tone/length/accent/user note.
- [ ] Редактор/save/copy и отдельный company sources accordion, ссылки не попадают в copy по умолчанию.
- [ ] Факты прослеживаются к page и profile record/version; contact только из вакансии.
- [ ] Partial research даёт маркированный draft; LLM failure сохраняет старый текст и retry; без auto-send.

## Проверки и свидетельства

Автоматические проверки через публичные сервисные границы и Django test client;
провайдеры заменяются fixtures. Реальные API/доставка/LLM/VPS проверяются отдельно.
Зафиксировать команды, результаты и ограничения в отчёте тикета.
Изменения shared schema/миграций согласовать с владельцем core и выполнять
последовательно: соседние тикеты одной волны не правят одни файлы.
Не считать блокированный внешний сценарий прошедшим.

## Comments

Подготовлен 20.09.2026. Реализация не запускалась.
