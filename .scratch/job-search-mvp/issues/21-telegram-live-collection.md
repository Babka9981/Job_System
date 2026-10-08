# T21 — Подключение Telegram к штатному сбору

Status: in-progress
GitHub-Issue: #13
Autopilot-Ticket: .autopilot/2026-09-20-job-search-mvp--wip/tickets/21-telegram-live-collection.md

Каноническая локальная задача: подключить существующий TelegramReader к штатному
collector с owner/enabled/source filters, fencing и последовательным использованием
пользовательской сессии. Авторизация выполнена пользователем 09.10.2026.

## Acceptance

- Shared lazy runtime client, guaranteed cleanup, safe configuration failures.
- Lease-guarded source writes; no writes after ownership loss.
- FloodWait stops Telegram calls without sleeping and keeps checkpoint retryable.
- Scoped collect_telegram command without LLM, matching, notifications or monitoring enablement.
- Existing parser, permission, initial seven-day window and update contracts retained.
- Tests fixtures at collector/runtime boundaries; production review/deploy performed separately.

## Comments

Production credentials, session contents and raw messages are never published.
