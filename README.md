# Job Search MVP

Личный Django-сервис поиска работы: Python 3.13.15, Django 5.2.17 LTS,
server-rendered HTML и SQLite. Регистрация закрыта; доступ к рабочим разделам
получает только пользователь с именем `JOB_OWNER_USERNAME`.
Для регрессионных тестов поведения drawer используется Node.js 22.20.0 без npm-зависимостей;
production runtime остаётся Python/Django-only.

## Локальный запуск (Windows PowerShell)

```powershell
py install 3.13.15
py -V:3.13.15 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe manage.py migrate
$env:JOB_OWNER_PASSWORD = Read-Host -AsSecureString | ConvertFrom-SecureString -AsPlainText
.\.venv\Scripts\python.exe manage.py create_owner
Remove-Item Env:JOB_OWNER_PASSWORD
.\.venv\Scripts\python.exe manage.py runserver
```

Для Linux/VPS замените путь к Python на `.venv/bin/python`. Django settings читает
переменные процесса и **не загружает `.env` автоматически**: `.env.example` — перечень
имён для process manager/контейнера, а не runtime-файл приложения. Значения секретов
не коммитятся.

## Production security check

Следующий PowerShell-блок воспроизводимо задаёт production-окружение текущего
процесса и должен завершаться `System check identified no issues (0 silenced)`:

```powershell
$env:DJANGO_DEBUG = 'false'
$env:DJANGO_SECRET_KEY = & .\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(64))"
$env:DJANGO_ALLOWED_HOSTS = 'jobs.example.com'
$env:DJANGO_CSRF_TRUSTED_ORIGINS = 'https://jobs.example.com'
$env:DJANGO_SECURE_SSL_REDIRECT = 'true'
$env:DJANGO_SECURE_HSTS_SECONDS = '31536000'
$env:JOB_OWNER_USERNAME = 'owner'
.\.venv\Scripts\python.exe manage.py check --deploy
```

Замените `jobs.example.com` реальным доменом; передайте те же переменные сервису
через его process manager. Пароль владельца передаётся только на время выполнения
`create_owner` и проходит стандартные Django password validators.

## Проверки

```powershell
# полный suite
.\.venv\Scripts\python.exe manage.py test

# один файл
.\.venv\Scripts\python.exe manage.py test jobs.core.tests.test_auth

# конфигурация и актуальность миграций
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
```

## Данные и резервные копии

- `db.sqlite3` и `private/` — постоянные данные, включаются в согласованную резервную копию.
- `temporary/` — физически отдельное хранилище временного контента источников;
  его необходимо исключить из backup и очищать по `expires_at`.
- Даты хранятся в UTC; пользовательский часовой пояс задаёт `JOB_TIME_ZONE`.

Production запускается за HTTPS reverse proxy (планируется Caddy) с
`DJANGO_DEBUG=false`, secure cookies и явными hosts/origins. Реальные API, LLM и
доставка в этом bootstrap не активируются.

План разработки: [docs/planning/README.md](docs/planning/README.md). Требования:
[Job_Search_MVP_TZ.md](Job_Search_MVP_TZ.md). UI: [design-system/README.md](design-system/README.md).
