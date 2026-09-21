# 16 — Читаемая проверка извлечённого CV

Status: done
Execution: user-requested UI recovery
Blocked by: 03, 12
Зона: jobs/profile/ · jobs/templates/profile/ · jobs/static/ui/ · jobs/tests/test_profile.py · tests/acceptance/
Требования: G16

## Что должно заработать

После загрузки CV пользователь раскрывает проверку и читает документ как набор
аккуратных текстовых блоков, а не как широкий технический дамп. При необходимости
можно отдельно раскрыть точный исходный извлечённый текст.

## Из брифа, дословно

> «Кажется что там все правильно, но проверять текст в таком виде очень сложно.»

## Разделы спецификации

S14 «Проверка извлечённого текста CV»; design-system DESIGN_SPEC §§3–6, 13–15.

## Критерии приёмки

- [x] `Resume.text` не изменяется и LLM получает прежний точный текст.
- [x] Маркированные блоки/страницы отображаются отдельными читаемыми элементами.
- [x] Неизвестная разметка и пустые/длинные строки не теряются и не ломают страницу.
- [x] Доступен вторичный raw-view с точным исходным текстом и безопасными переносами.
- [x] Preview не создаёт page-level horizontal overflow на контрольных ширинах/200%.
- [x] Обе темы, keyboard/details, Axe и console проходят browser acceptance.
- [x] Owner scoping и существующие upload/extract/confirm действия не меняются.

## Результат

- Product commit: `5283df2`.
- Django: 365/365, один ожидаемый skip; profile: 22/22.
- Node: 10/10; browser: 50 scans, Axe serious/critical 0, console clean.
- Manifest/spec review: done; craft review: blocking findings отсутствуют.
