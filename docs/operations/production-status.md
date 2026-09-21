# Job System: production status

Фактическое состояние на 2026-09-21. Это снимок развёртывания; безопасные процедуры
обновления, backup и restore находятся в `README.md`.

## Развёрнуто и проверено

- VPS repo deployment commit — `c36ea5d`; GitHub/main включает этот deployment commit
  и последующую документацию. Product commits T17–T20: `eafa798`, `05fcfe5`, `4bbeb07`, `7c790b2`.
- Immutable image tag: `20260921T154656Z`; compose project: `job-system`; container healthy.
- Web привязан только к `127.0.0.1:18111` и опубликован через Caddy как
  `https://job.web3babka.su`.
- Container healthy, HTTPS health endpoint отвечает успешно; login и static endpoints
  возвращают HTTP 200.
- Читаемый preview извлечённого CV отображается обычными блоками; secondary raw view
  сохраняет точное представление, а `Resume.text` и LLM input не меняются.
- Preview CSS присутствует в collected static и доступен по HTTP 200.
- Owner создан: `active_owners=1`, usable password — `true`; значение пароля не записано.
- Публичная регистрация закрыта: signup/register возвращает HTTP 404.
- Source registry инициализирован: 50 источников (`9 site + 41 telegram`).
- Controlled restart прошёл успешно.
- До изменения Caddy сохранён backup
  `/etc/caddy/Caddyfile.before-job-system-20260921T044832Z`.
- Существующие сервисы после обновления не затронуты: Eggent отвечает HTTP 307,
  SkyPay — HTTP 200, blog — HTTP 200.
- `job-system-cleanup.timer` и `job-system-backup.timer` включены и активны;
  `job-system-monitor.timer` выключен, `JOB_MONITORING_ENABLED=false`.
- Без записи значений подтверждена настройка `OPENAI_API_KEY`, `TAVILY_API_KEY`,
  `BRAVE_SEARCH_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_OWNER_CHAT_ID` и
  `BACKUP_AGE_RECIPIENT`.
- OpenAI authentication, Tavily search, Telegram `getMe`/`getChat` проверены live.
- Production OpenAI model/profile — `gpt-5-mini`; настроены цены за миллион токенов:
  input `0.25`, cached input `0.025`, output `2.00`. API key настроен без записи значения;
  read-only `GET /v1/models/gpt-5-mini` вернул HTTP 200.
- Дневной бюджет profile — `5`; OpenAI worker и transport bounded 120 секунд; profile
  extraction output default и production — 8000 токенов.
- Brave LLM Context возвращает `OPTION_NOT_IN_PLAN`; application fallback на Brave Web
  Search проверен live и вернул один результат.
- Созданы encrypted backups `job-system-20260921T090715Z.tar.age`, pre-update
  `job-system-20260921T103955Z.tar.age`, pre-T15
  `job-system-20260921T115026Z.tar.age` и pre-T16
  `job-system-20260921T125955Z.tar.age`; перед T17 создан
  `job-system-20260921T135346Z.tar.age`; финальный pre-update backup —
  `job-system-20260921T154633Z.tar.age`. Правила доступа к файлам проверены.

## Проверки T16

- Локально: Django 365 passed, 1 skipped; Node UI 10 passed; browser matrix 50 scans.
- Blind desktop 1440 и mobile 390: PASS; exact raw match, keyboard Enter и отсутствие
  горизонтального overflow подтверждены.
- Production: container healthy; HTTPS health, `accounts/login` и static отвечают HTTP 200.
- После rollout preview CSS найден в collected static и доступен по HTTP 200.
- Существующие сервисы не затронуты: Eggent отвечает HTTP 307, SkyPay и blog — HTTP 200.

## Проверки T17

- Локально: targeted 143 passed; Django 367 passed, 1 skipped; Node UI 10 passed;
  browser matrix 50 scans; blind relevant 50 passed.
- Initial model/price error исправлена. Exact action остановилась до transport из-за
  отсутствия положительного дневного бюджета: `daily_budget_usd=0.0000`.
- CV не отправлялся, paid inference не выполнялся.
- Production health и login отвечают HTTP 200; Eggent — HTTP 307, SkyPay и blog — HTTP 200.

## Финальные проверки T17–T20

- Локально: Django 374 passed, 1 skipped; Node UI 10 passed; final blind 6 targeted PASS.
- Durable proposed profile draft v2 содержит 42 неподтверждённых элемента: 26 facts,
  15 questions и 1 about; `load_profile_draft` и UI path проверены.
- `confirmed_version=0`, подтверждённых facts 0; monitoring остаётся выключенным до
  ручного подтверждения профиля.
- Usage ledger: одна settled запись стоимостью `0.0105`, четыре pending, zero reserved.
- Production HTTPS health/login отвечают HTTP 200; Eggent — HTTP 307, SkyPay и blog — HTTP 200.

## Ожидает ручного действия

- Proposed profile draft нужно вручную проверить и подтвердить; до этого monitoring выключен.
- Четыре pending usage-записи требуют reconciliation; reserved-записей нет.
- `TELEGRAM_API_ID` и `TELEGRAM_API_HASH` пока пусты, поэтому MTProto reader отложен.
- Encrypted backup нужно скачать на доверенную машину, расшифровать private AGE identity,
  которая не хранится на VPS, и выполнить off-server verification.
- Restore drill ещё не выполнен.

Не записывать в этот файл значения credentials, tokens, паролей или private AGE identity.
