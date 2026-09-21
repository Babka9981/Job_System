# T19 — Убрать второй 45-second cap OpenAI transport

Статус: done

## Симптом

После T18 worker принимает до 120 секунд, но `OpenAIResponsesTransport.create_response`
сам задаёт `clock()+45` и `socket_timeout=min(45, remaining)`. Штатный full-CV extraction
снова завершается provider_error, черновик не создан, reservation остаётся pending.

## Требование

- Test-first regression на transport→run_http_exchange boundary: default позволяет до
  120 секунд, explicit shorter deadline не расширяется.
- Один централизованный bounded default без ослабления hard deadline, kill/reap,
  budget accounting, secret isolation или body/error contracts.
- Live API/CV в тестах запрещены.
- Targeted gateway/http/profile/budget + full suite.
- После deploy штатный budget-enforced extraction создаёт draft.

## Результат

Реализован test-first regression на границе
`OpenAIResponsesTransport -> run_http_exchange`. Внутренний bounded default transport
увеличен до 120 секунд в соответствии с worker cap; явный меньший deadline сохраняется,
а истёкший deadline fail-closed отклоняется до запуска subprocess. Kill/reap, pending
reservation, изоляция секретов и безопасные ошибки не изменены.

Проверки: RED зафиксировал прежние 45 секунд и неверный `provider_error`; GREEN —
3 regression/kill-reap теста; targeted gateway/budget/profile/research/http — 96 passed; полный Django
suite 372 passed, 1 skipped; Node UI 10 passed; migration drift отсутствует;
`pip check` и `git diff --check` пройдены.
