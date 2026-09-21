# 14 — Brave Search fallback для доступного плана

Status: done
Execution: production recovery after live integration check
Blocked by: 13
Зона: jobs/intelligence/search/brave.py · jobs/tests/test_research.py · docs/planning/integrations.md
Требования: G09, D02

## Что должно заработать

Brave provider сначала использует официальный LLM Context. Если официальный ответ
явно сообщает OPTION_NOT_IN_PLAN, provider в том же bounded request budget переходит
на официальный Web Search endpoint и преобразует web.results в SearchHit. Другие
4xx, quota, auth, timeout и network ошибки не маскируются fallback-ом.

## Критерии приёмки

- [x] LLM Context остаётся первым endpoint и текущий успешный путь не меняется.
- [x] Только OPTION_NOT_IN_PLAN активирует Web Search fallback.
- [x] Общий deadline и физический request cap сохраняются.
- [x] Provenance остаётся brave и не выдаёт snippet за подтверждённый evidence.
- [x] Ошибки/тела/ключи не попадают в исключения и логи.
- [x] Fixture-тесты красно-зелёно покрывают success/fallback/non-plan 4xx/quota/deadline.
- [x] Full Django, Node, migration drift и pip check зелёные.

## Production evidence

- LLM Context: HTTP 400, safe code OPTION_NOT_IN_PLAN.
- Web Search: HTTP 200, один результат.
- Значение BRAVE_SEARCH_API_KEY не читалось и не выводилось.

## Результат

- Product commit: `54dcee4`.
- Django: 364/364, один ожидаемый skip; research: 56/56.
- Node: 10/10; migration drift отсутствует; `pip check` и `diff --check` чистые.
- Manifest/spec review: done; craft review: blocking findings отсутствуют.
