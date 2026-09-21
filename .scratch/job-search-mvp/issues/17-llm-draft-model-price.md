# T17 — Рабочая модель и цена для LLM-черновика

Статус: done

## Симптом

Production при создании LLM-черновика показывает: «Модель или её цена не настроены;
вызов отключён.» Безопасный repro: selected model `gpt-5.6-luna`, price models пусты,
API key настроен; CV и секреты в диагностике не читались.

## Требование

- Заменить runtime/UI/setup defaults на публичную `gpt-5-mini`.
- Задать явный `OPENAI_PRICES_JSON` для стандартной цены input 0.25/output 2.00 USD
  за миллион токенов; неизвестные модели продолжают fail-closed.
- Добавить regression test, красный на прежнем setup/runtime контракте и зелёный после.
- Не выводить API key, CV или credential values; не делать live paid call в тестах.
- Сохранить возможность изменить модель в настройках.
- Проверить setup contracts, profile extraction fixture, matching/drafts/research и полный suite.

## Результат

- Default модели во всех runtime/UI путях заменён на `gpt-5-mini`; custom model
  сохраняется без автоматической подмены, неизвестная цена остаётся fail-closed.
- `.env.example` и setup wizard записывают стандартную цену: input `0.25`, cached
  input `0.025`, output `2.00` USD за миллион токенов.
- Для custom model wizard явно требует `OPENAI_PRICES_JSON` и прекращает настройку,
  если таблица цены не введена.
- RED: deployment regression падал на `OPENAI_MODEL=gpt-5.6-luna` и пустом
  `OPENAI_PRICES_JSON`; после исправления тест зелёный.
- Проверки: targeted Django `143 passed`; full Django `367 passed, 1 skipped`;
  UI `10 passed`; browser `50 scans` (Axe 0, console clean); migration drift отсутствует;
  `pip check`, `git diff --check` и `bash -n deployment/setup-wizard.sh` успешны.
