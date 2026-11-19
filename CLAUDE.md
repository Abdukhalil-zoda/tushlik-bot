# Notes for AI coding agents (Claude Code, Cursor, …)

Telegram bot that splits a group lunch bill. Python 3.12, aiogram 3, asyncpg, PostgreSQL 16, long polling.

Layout:
- `bot/split.py` — the money math, pure functions. Every share is rounded UP to ROUND_TO. Change only with tests.
- `bot/bill_schema.py` — parses the bill JSON (format in `examples/bill.json`, explained in `ui.HELP`).
- `bot/db.py` — `Repo`, the only place with SQL. Migrations in `migrations/*.sql` run on start; add new files, never edit old ones.
- `bot/ui.py` — all texts, keyboards and callback data (≤ 64 bytes). User-facing language: Russian.
- `bot/core.py` — shared actions: recompute a bill, refresh the group message, close a bill, notify.
- `bot/handlers/` — start (menu, card), bills (JSON → group → picking → close → my bills),
  debts (receipt, paid, postpone, confirm, ping), donations (server/coffee, owner confirms, leaderboard), group.
- `bot/scheduler.py` — every minute: postponed reminders; after a month ends, records what the owner covered.

Flows: bill status draft → open → closed → done (or cancelled); debt pending → reported → confirmed
(rejected goes back to pending); donation pending → confirmed | rejected.

Privacy: card numbers are shown only in private chats, never in groups or logs.

Check before committing: `ruff check bot tests && pytest -q`.
