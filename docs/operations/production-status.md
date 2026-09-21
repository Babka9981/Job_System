# Job System: production status

Фактическое состояние на 2026-09-21. Это снимок развёртывания; безопасные процедуры
обновления, backup и restore находятся в `README.md`.

## Развёрнуто и проверено

- Commit `8bd45ea` развёрнут на VPS `169.58.93.185` в `/srv/job-system`.
- Immutable image tag: `20260921T044606Z`; compose project: `job-system`.
- Web привязан только к `127.0.0.1:18111` и опубликован через Caddy как
  `https://job.web3babka.su`.
- Health endpoint отвечает успешно; login и static endpoints возвращают HTTP 200.
- Controlled restart прошёл успешно.
- До изменения Caddy сохранён backup
  `/etc/caddy/Caddyfile.before-job-system-20260921T044832Z`.
- Существующие Eggent, SkyPay и blog после развёртывания проверены и продолжают отвечать.
- `job-system-cleanup.timer` включён.

## Ожидает ручной настройки

- `job-system-monitor.timer` выключен до заполнения production credentials локальным
  `deployment/setup-wizard.sh`.
- `job-system-backup.timer` выключен до задания публичного `BACKUP_AGE_RECIPIENT`;
  private AGE identity должна оставаться вне VPS.
- Owner account и пароль ещё не созданы. Создать интерактивно процедурой из runbook;
  пароль нигде не фиксировать.
- MTProto reader отложен.
- Live provider/API checks, первый encrypted backup и restore drill ещё не подтверждены.

Не записывать в этот файл значения credentials, tokens, паролей или private AGE identity.