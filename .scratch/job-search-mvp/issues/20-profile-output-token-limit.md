# T20 — Закрепить 8000 output tokens для profile extraction

Статус: done

## Доказательство

После T19 штатный gpt-5-mini extraction при default 4000 завершился invalid_response.
Budget-enforced recovery с 8000 успешно создал draft v2: 26 фактов и 15 вопросов.

## Требование

- Test-first: profile service передаёт default max_output_tokens=8000.
- Сохранить environment override и budget reservation по фактическому лимиту.
- Добавить `JOB_PROFILE_MAX_OUTPUT_TOKENS=8000` в `.env.example`.
- Без live API/CV в тестах; targeted profile/budget/deployment + full suite.
- Один commit; production уже закреплён environment override 8000.

## Результат

- RED: default boundary передавал `4000` вместо `8000`; deployment contract не находил переменную в `.env.example`. Explicit override уже проходил.
- `extract_profile(...)` теперь передаёт `8000` при отсутствии `JOB_PROFILE_MAX_OUTPUT_TOKENS` и сохраняет явный environment override.
- `.env.example` документирует `JOB_PROFILE_MAX_OUTPUT_TOKENS=8000`.
- Budget reservation не обойдена: фактический лимит по-прежнему передаётся в существующий `gateway.structured(...)`, где рассчитывается максимальная стоимость.
- Проверки: targeted profile/budget/operations — 54 passed; Django — 374 passed, 1 skipped; UI logic — 10 passed; migration drift — none; `pip check` — clean; `git diff --check` — clean.
