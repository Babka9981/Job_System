# Мониторинг и Telegram-подборка

Запускайте `python manage.py run_monitoring` из системного планировщика не реже раза
в минуту. Команда сама выбирает локальный слот из `JOB_MONITORING_SCHEDULE`
(по умолчанию `09:00,13:00,17:00,21:00`) в `JOB_TIME_ZONE`, переживает рестарт и
не запускает слот повторно. Пустой `JOB_TIME_ZONE` отключает расписание. Первый
сбор ограничен последними семью днями; последующая частота поставщика задаётся его
`service_interval_seconds`.

Доставка использует отдельного Telegram-бота и только owner chat:
`TELEGRAM_BOT_TOKEN`, `TELEGRAM_OWNER_CHAT_ID` и абсолютный `JOB_SITE_URL`.
`TELEGRAM_API_ID`/`TELEGRAM_API_HASH` принадлежат reader account и для доставки не
используются. Без конфигурации сбор остаётся рабочим, а доставка имеет честный
disabled/failed статус.

Таймаут Telegram имеет статус `uncertain`, не `sent`: автоматического повтора нет.
Оператор вызывает `reconcile_delivery(outbox_id, delivered=True|False)`, а после
подтверждения отсутствия доставки — `retry_delivery(outbox_id)`. Число попыток
ограничено `JOB_NOTIFICATION_MAX_ATTEMPTS` (по умолчанию 3). Lease цикла задаётся
`JOB_MONITORING_LEASE_SECONDS` (по умолчанию 1800 секунд).
