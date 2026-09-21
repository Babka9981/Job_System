# Job System: production status

Фактическое состояние на 2026-09-21. Это снимок развёртывания; безопасные процедуры
обновления, backup и restore находятся в `README.md`.

## Развёрнуто и проверено

- Commit `bbeb12b` развёрнут на VPS `169.58.93.185` в `/srv/job-system`; входящий в него
  product commit — `54dcee4`.
- Immutable image tag: `20260921T104205Z`; compose project: `job-system`.
- Web привязан только к `127.0.0.1:18111` и опубликован через Caddy как
  `https://job.web3babka.su`.
- Container healthy, HTTPS health endpoint отвечает успешно; login и static endpoints
  возвращают HTTP 200.
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
- Brave LLM Context возвращает `OPTION_NOT_IN_PLAN`; application fallback на Brave Web
  Search проверен live и вернул один результат.
- Созданы encrypted backups `job-system-20260921T090715Z.tar.age` и pre-update
  `job-system-20260921T103955Z.tar.age`; правила доступа к файлам проверены.

## Ожидает ручного действия

- Профиль и настройки поиска существуют, но `confirmed_version=0`. Owner должен загрузить
  и подтвердить CV/профиль; только после этого включаются monitoring flag и timer.
- `TELEGRAM_API_ID` и `TELEGRAM_API_HASH` пока пусты, поэтому MTProto reader отложен.
- Encrypted backup нужно скачать на доверенную машину, расшифровать private AGE identity,
  которая не хранится на VPS, и выполнить off-server verification.
- Restore drill ещё не выполнен.

Не записывать в этот файл значения credentials, tokens, паролей или private AGE identity.
