# Job System: production operations

Статус на 2026-09-21: production-развёртывание выполнено и проверено; точный снимок
версии, сервисов и оставшихся ручных шагов — в `production-status.md`. Live API,
первый backup и restore drill не считаются проверенными без отдельных операторских
действий, описанных ниже.

## Изоляция и подтверждённый target

- VPS `169.58.93.185`, Ubuntu 22.04.5, 6 CPU, 11 GiB RAM, около 28 GiB свободно.
- Docker активен. Caddy 2.11.4 уже владеет `80/443`; сайты Eggent, SkyPay и blog не трогаются.
- Заняты loopback-порты `3000`, `3100`, `3101`, `18000`. Job System использует только
  новый bind `127.0.0.1:18111` и compose project `job-system`.
- `job.web3babka.su` обслуживается Caddy по HTTPS; перед добавлением сайта сохранён
  `/etc/caddy/Caddyfile.before-job-system-20260921T044832Z`.
- Runtime-каталог: `/srv/job-system`; private/database/temporary/backups разнесены.
  `temporary/` никогда не включается в backup, `.env` и Telegram session также исключены.

## Read-only preflight (до deploy gate)

Локально:

```bash
dig +short A job.web3babka.su
ssh root@169.58.93.185 'uname -a; cat /etc/os-release; free -h; df -h /; docker version; docker compose version'
ssh root@169.58.93.185 'ss -lntup; systemctl --no-pager status caddy docker; caddy version'
ssh root@169.58.93.185 'sed -n "1,240p" /etc/caddy/Caddyfile; find /etc/caddy -maxdepth 2 -type f -print'
ssh root@169.58.93.185 'curl -fsS --max-time 10 https://api.openai.com/ >/dev/null; curl -fsS --max-time 10 https://api.tavily.com/ >/dev/null; curl -fsS --max-time 10 https://api.search.brave.com/ >/dev/null'
```

Последние HTTP-команды проверяют только исходящий TLS/DNS, не ключи и не платные вызовы.
Повторно убедиться, что `18111` свободен: `ssh root@169.58.93.185 'ss -lnt | grep -F ":18111 " || true'`.

## Operator env setup

На доверенной рабочей станции из каталога `deployment/`:

```bash
cp ../.env.example .env
chmod 600 .env
bash ./setup-wizard.sh
```

Wizard скрывает secret input и пишет только локальный `deployment/.env`; CI не
ссылается на эти значения, поэтому GitHub secrets не создаются. Сгенерировать
`DJANGO_SECRET_KEY` можно командой `python -c 'import secrets; print(secrets.token_urlsafe(64))'`.
Пароль owner не хранить постоянно: передать его лишь команде `create_owner`.

## Первый deploy / повторяемая установка (только после отдельного разрешения)

Ниже команды изолированы от других compose projects. `Caddyfile.job-system` — точный
добавляемый site block. Сначала подтвердить, что действующий Caddyfile уже импортирует
`/etc/caddy/sites-enabled/*`; если нет — остановиться и согласовать единственное изменение
главного Caddyfile, не угадывать структуру.

```bash
sudo install -d -m 0755 -o 10001 -g 10001 /srv/job-system/deployment/runtime
sudo install -d -m 0700 -o 10001 -g 10001 /srv/job-system/deployment/runtime/{data,private,temporary,backups}
sudo install -d -m 0755 -o 10001 -g 10001 /srv/job-system/deployment/runtime/staticfiles
sudo rsync -a --delete --exclude /.git --exclude /deployment/.env --exclude /deployment/release.env --exclude /deployment/runtime/** ./ /srv/job-system/
cd /srv/job-system/deployment
chmod 600 .env
export JOB_IMAGE_TAG="$(date -u +%Y%m%dT%H%M%SZ)"
printf 'JOB_IMAGE_TAG=%s\n' "$JOB_IMAGE_TAG" > release.env.tmp
chmod 0644 release.env.tmp
mv release.env.tmp release.env
docker compose config --quiet
docker compose build web
docker compose run --rm -T web python manage.py check --deploy
docker compose run --rm -T web python manage.py migrate --plan
docker compose run --rm -T web python manage.py migrate
docker compose run --rm -T web python manage.py collectstatic --no-input
docker compose up -d web
docker compose ps
curl -fsS -H 'X-Forwarded-Proto: https' http://127.0.0.1:18111/healthz/
sudo install -m 0644 Caddyfile.job-system /etc/caddy/sites-enabled/job-system.caddy
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
curl -fsS https://job.web3babka.su/healthz/
```

Один и тот же экспортированный `JOB_IMAGE_TAG` используется build, всех one-off
контейнеров, `create_owner`, `up` и затем timer units через `release.env`. Owner
создаётся без shell trace, вывода, аргумента командной строки или записи пароля на диск:

```bash
set +x
cleanup_owner_password() { unset JOB_OWNER_PASSWORD; }
trap cleanup_owner_password EXIT HUP INT TERM
read -rsp 'Owner password: ' JOB_OWNER_PASSWORD; printf '\n'
export JOB_OWNER_PASSWORD
docker compose run --rm -T -e JOB_OWNER_PASSWORD web python manage.py create_owner --no-input
owner_status=$?
unset JOB_OWNER_PASSWORD
trap - EXIT HUP INT TERM
test "$owner_status" -eq 0
```

Установить timer units отдельными новыми файлами, затем проверить именно их:

```bash
sudo install -m 0644 systemd/job-system-* /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now job-system-monitor.timer job-system-cleanup.timer job-system-backup.timer
systemctl list-timers 'job-system-*'
```

На 2026-09-21 включён только `job-system-cleanup.timer`. Не включать monitor и backup
таймеры, пока локальный wizard не подготовит production credentials и публичный
`BACKUP_AGE_RECIPIENT`. Owner account/password также создаются только интерактивно;
пароль не записывать в документы, shell history или файлы. MTProto reader отложен.

## Обновление, migration и rollback

Перед каждым обновлением создать backup, сохранить предыдущий immutable image tag и
прочитать `migrate --plan`. T11 не добавляет schema migration.

```bash
cd /srv/job-system/deployment
set -a; . ./release.env; set +a
docker compose run --rm -T web bash deployment/backup.sh
PREVIOUS_TAG="$JOB_IMAGE_TAG"
docker image inspect "job-system:${PREVIOUS_TAG}" >/dev/null
export JOB_IMAGE_TAG="${NEW_TAG}"
docker compose build web
docker compose run --rm -T web python manage.py check --deploy
docker compose run --rm -T web python manage.py migrate --plan
docker compose stop web
docker compose run --rm -T web python manage.py migrate
docker compose up -d web
curl -fsS -H 'X-Forwarded-Proto: https' http://127.0.0.1:18111/healthz/
printf 'JOB_IMAGE_TAG=%s\n' "$JOB_IMAGE_TAG" > release.env.tmp
chmod 0644 release.env.tmp
mv release.env.tmp release.env
```

App rollback (schema должна быть backward-compatible):

```bash
cd /srv/job-system/deployment
export JOB_IMAGE_TAG="${PREVIOUS_TAG}"
docker image inspect "job-system:${JOB_IMAGE_TAG}" >/dev/null
printf 'JOB_IMAGE_TAG=%s\n' "$JOB_IMAGE_TAG" > release.env.tmp
chmod 0644 release.env.tmp
mv release.env.tmp release.env
docker compose up -d --no-build web
curl -fsS -H 'X-Forwarded-Proto: https' http://127.0.0.1:18111/healthz/
```

Если migration не backward-compatible, не запускать старый image: сначала восстановить
последний backup либо выполнить заранее проверенный reverse target командой
`docker compose run --rm -T web python manage.py migrate APP PREVIOUS_MIGRATION`.

## Backup, retention и off-server verification

`backup.sh` сначала выполняет TTL cleanup и SQLite online snapshot. Private allowlist
добавляет только CV, Rocketship user state и durable source quota state. Всё остальное,
включая temporary/raw/derived content, `*-wal`, `*-shm`, caches, `.env`, ключи и любые
Telegram sessions, не рассматривается для копирования. Затем snapshot немедленно шифруется `age`. На VPS находится только
public `BACKUP_AGE_RECIPIENT`; private identity остаётся вне сервера. После успешного
создания новой копии удаляются копии старше 30 дней, но newest successful не удаляется.

```bash
cd /srv/job-system/deployment
set -a; . ./release.env; set +a
docker compose run --rm -T web bash deployment/backup.sh
find runtime/backups -maxdepth 1 -type f -name 'job-system-*.tar.age' -ls
journalctl -u job-system-backup.service --since today --no-pager
```

Проверка выполняется на доверенной машине после скачивания encrypted backup:

```bash
scp root@169.58.93.185:/srv/job-system/deployment/runtime/backups/job-system-YYYYmmddTHHMMSSZ.tar.age .
bash ./deployment/verify-backup.sh job-system-YYYYmmddTHHMMSSZ.tar.age /secure/off-server/job-system-backup-key.txt
```

Она проверяет checksums, SQLite integrity чтением, active user, profile, source checkpoint,
run state и наличие каждого Resume private file. Успех имеет строку `snapshot_verified`.

## Restore drill / restore

Сначала off-server расшифровать и проверить; private age identity на VPS не копировать:

```bash
age -d -i /secure/off-server/job-system-backup-key.txt -o snapshot.tar job-system-YYYYmmddTHHMMSSZ.tar.age
./.venv/bin/python manage.py verify_snapshot snapshot.tar
scp snapshot.tar root@169.58.93.185:/srv/job-system/deployment/runtime/restore.snapshot.tar
```

На VPS restore выполняет единственный fail-fast script с `set -Eeuo pipefail`. Он
отключает timers, останавливает все writer services и дважды требует состояние
`inactive` — второй раз непосредственно перед первым `mv`. Любой другой статус
завершает процесс до swap. Пустой private payload поддерживается явно: каждый valid
snapshot всегда содержит каталог `payload/private/`.

```bash
cd /srv/job-system/deployment
bash ./restore.sh runtime/restore.snapshot.tar
```

Script проверяет snapshot до staging, делает recoverable directory swap и автоматически
возвращает прежние mounts при ошибке migration/start/health; writers при ошибке остаются
отключёнными для осмотра. `release.env` не меняется, поэтому web и timers используют тот
же проверенный immutable tag. Не удалять plaintext archive,
stage или rollback автоматически. Удалять их только после ручной проверки profile, user status, source
checkpoint, CV download и следующего успешного encrypted backup. Secrets/session
восстанавливаются повторным wizard/Telegram authorization, а caches пересоздаются.
