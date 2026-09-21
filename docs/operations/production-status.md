# Job System: production status

Фактическое состояние на 2026-09-21. Это снимок развёртывания; безопасные процедуры
обновления, backup и restore находятся в `README.md`.

## Развёрнуто и проверено

- Commit `622d52c` находится в GitHub/main и развёрнут на VPS `169.58.93.185` в
  `/srv/job-system`; входящий product commit T17 — `eafa798`.
- Immutable image tag: `20260921T135406Z`; compose project: `job-system`; container healthy.
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
- Brave LLM Context возвращает `OPTION_NOT_IN_PLAN`; application fallback на Brave Web
  Search проверен live и вернул один результат.
- Созданы encrypted backups `job-system-20260921T090715Z.tar.age`, pre-update
  `job-system-20260921T103955Z.tar.age`, pre-T15
  `job-system-20260921T115026Z.tar.age` и pre-T16
  `job-system-20260921T125955Z.tar.age`; перед T17 создан
  `job-system-20260921T135346Z.tar.age`. Правила доступа к файлам проверены.

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

## Ожидает ручного действия

- Production-проверка вернула `profiles=1`, `resumes=1`, `confirmed_profiles=0`: CV
  загружен, но подтверждённых профилей нет; monitoring остаётся выключенным до подтверждения.
- Перед генерацией отклика owner должен выбрать положительный `daily_budget_usd`.
- `TELEGRAM_API_ID` и `TELEGRAM_API_HASH` пока пусты, поэтому MTProto reader отложен.
- Encrypted backup нужно скачать на доверенную машину, расшифровать private AGE identity,
  которая не хранится на VPS, и выполнить off-server verification.
- Restore drill ещё не выполнен.

Не записывать в этот файл значения credentials, tokens, паролей или private AGE identity.
