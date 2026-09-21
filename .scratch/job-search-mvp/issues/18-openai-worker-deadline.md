# T18 — Согласовать OpenAI transport и worker deadline

Статус: done

## Симптом и доказательство

Минимальный gpt-5-mini Responses strict-schema запрос отвечает 200. Полный CV extraction
через штатный gateway дважды завершается provider_error примерно через 30–32 секунды,
включая попытку с transport deadline 120 секунд. `http_worker._exchange` принудительно
ограничивает `socket_timeout` максимумом 30 секунд.

## Требование

- Сначала regression test, красный на фактическом worker timeout cap.
- Worker должен уважать OpenAI socket timeout выше 30 секунд в рамках безопасного
  максимума; parent hard deadline остаётся обязательным и убивает subprocess.
- Не ослаблять secret isolation, redirect ban, size limits и fail-closed errors.
- Не вызывать live API и не читать CV в тестах.
- Проверить intelligence/budget/profile tests и полный suite.
- После deploy повторить owner action только через budget-enforced gateway.

## Результат

- RED: `WorkerSocketTimeoutTests.test_worker_passes_socket_timeout_above_thirty_seconds_to_connection`
  получил `30.0` вместо запрошенных `75.0`.
- Worker передаёт конечный timeout до `120.0` секунд, сохраняет нижнюю границу `0.1`
  секунды и отклоняет нечисловые/NaN/infinite значения; default остаётся `30.0` секунд.
- Parent hard deadline/kill, allowlist окружения, redirect-free exchange и ограничения
  тела/заголовков не менялись; существующие проверки deadline/изоляции проходят.
- Проверки: targeted research/http worker/gateway/budget/profile — passed; полный Django
  suite — 370 passed, 1 skipped; UI logic — 10 passed; migration drift — none;
  `pip check` и `git diff --check` — passed. Browser suite не запускался: UI не менялся.
