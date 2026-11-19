"""All SQL lives here. Handlers call Repo methods and never write SQL themselves."""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import asyncpg

from .split import Item

MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"


async def connect(url: str) -> asyncpg.Pool:
    pool = await asyncpg.create_pool(url, min_size=1, max_size=5)
    async with pool.acquire() as c:
        await c.execute("CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY, at TIMESTAMPTZ DEFAULT now())")
        done = {r["name"] for r in await c.fetch("SELECT name FROM schema_migrations")}
        for f in sorted(MIGRATIONS.glob("*.sql")):
            if f.name in done:
                continue
            async with c.transaction():
                await c.execute(f.read_text())
                await c.execute("INSERT INTO schema_migrations (name) VALUES ($1)", f.name)
    return pool


class Repo:
    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    # users / chats ---------------------------------------------------------
    async def upsert_user(self, uid: int, username: str | None, first_name: str) -> None:
        await self.pool.execute(
            """INSERT INTO users (id, username, first_name) VALUES ($1, $2, $3)
               ON CONFLICT (id) DO UPDATE SET username = $2, first_name = $3""", uid, username, first_name or "")

    async def user(self, uid: int) -> asyncpg.Record | None:
        return await self.pool.fetchrow("SELECT * FROM users WHERE id = $1", uid)

    async def set_card(self, uid: int, number: str, holder: str) -> None:
        await self.pool.execute("UPDATE users SET card_number = $2, card_holder = $3 WHERE id = $1", uid, number, holder)

    async def upsert_chat(self, cid: int, title: str, active: bool) -> None:
        await self.pool.execute(
            """INSERT INTO chats (id, title, active) VALUES ($1, $2, $3)
               ON CONFLICT (id) DO UPDATE SET title = $2, active = $3""", cid, title or "", active)

    async def active_chats(self) -> list[asyncpg.Record]:
        return await self.pool.fetch("SELECT * FROM chats WHERE active ORDER BY title")

    # bills -----------------------------------------------------------------
    async def create_draft(self, payer: int, draft) -> int:
        async with self.pool.acquire() as c, c.transaction():
            bid = await c.fetchval(
                "INSERT INTO bills (payer_id, title, service_percent, receipt_total) VALUES ($1, $2, $3, $4) RETURNING id",
                payer, draft.title, draft.service_percent, draft.total)
            await c.executemany(
                "INSERT INTO bill_items (bill_id, position, name, unit_price, qty, shared) VALUES ($1, $2, $3, $4, $5, $6)",
                [(bid, i, it.name, it.unit_price, it.qty, it.shared) for i, it in enumerate(draft.items)])
        return bid

    async def last_draft(self, uid: int) -> asyncpg.Record | None:
        return await self.pool.fetchrow(
            "SELECT * FROM bills WHERE payer_id = $1 AND status = 'draft' ORDER BY id DESC LIMIT 1", uid)

    async def bill(self, bid: int) -> asyncpg.Record | None:
        return await self.pool.fetchrow("SELECT * FROM bills WHERE id = $1", bid)

    async def items(self, bid: int) -> list[Item]:
        rows = await self.pool.fetch("SELECT * FROM bill_items WHERE bill_id = $1 ORDER BY position", bid)
        return [Item(r["id"], r["name"], r["unit_price"], r["qty"], r["shared"]) for r in rows]

    async def publish(self, bid: int, chat_id: int, message_id: int) -> None:
        await self.pool.execute(
            "UPDATE bills SET chat_id = $2, group_message_id = $3, status = 'open' WHERE id = $1", bid, chat_id, message_id)

    async def set_status(self, bid: int, status: str) -> None:
        await self.pool.execute(
            "UPDATE bills SET status = $2, closed_at = CASE WHEN $2 = 'closed' THEN now() ELSE closed_at END WHERE id = $1",
            bid, status)

    async def my_bills(self, uid: int, limit: int = 10) -> list[asyncpg.Record]:
        return await self.pool.fetch(
            """SELECT DISTINCT b.* FROM bills b
               LEFT JOIN participants p ON p.bill_id = b.id AND p.user_id = $1
               WHERE (b.payer_id = $1 OR p.user_id IS NOT NULL) AND b.status <> 'cancelled'
               ORDER BY b.id DESC LIMIT $2""", uid, limit)

    # picking dishes ----------------------------------------------------------
    async def join(self, bid: int, uid: int) -> None:
        await self.pool.execute("INSERT INTO participants (bill_id, user_id) VALUES ($1, $2) ON CONFLICT DO NOTHING", bid, uid)

    async def leave(self, bid: int, uid: int) -> None:
        await self.pool.execute("DELETE FROM participants WHERE bill_id = $1 AND user_id = $2", bid, uid)

    async def participants(self, bid: int) -> list[asyncpg.Record]:
        return await self.pool.fetch(
            """SELECT u.* FROM participants p JOIN users u ON u.id = p.user_id
               WHERE p.bill_id = $1 ORDER BY p.joined_at""", bid)

    async def claims(self, bid: int) -> dict[int, dict[int, int]]:
        rows = await self.pool.fetch(
            """SELECT c.item_id, c.user_id, c.units FROM claims c JOIN bill_items i ON i.id = c.item_id
               WHERE i.bill_id = $1 AND c.units > 0""", bid)
        out: dict[int, dict[int, int]] = {}
        for r in rows:
            out.setdefault(r["item_id"], {})[r["user_id"]] = r["units"]
        return out

    async def set_claim(self, item_id: int, uid: int, units: int) -> None:
        await self.pool.execute(
            """INSERT INTO claims (item_id, user_id, units) VALUES ($1, $2, $3)
               ON CONFLICT (item_id, user_id) DO UPDATE SET units = $3""", item_id, uid, units)

    # debts -----------------------------------------------------------------
    async def create_debts(self, bid: int, amounts: dict[int, int]) -> None:
        async with self.pool.acquire() as c, c.transaction():
            await c.executemany(
                "INSERT INTO debts (bill_id, user_id, amount) VALUES ($1, $2, $3) ON CONFLICT DO NOTHING",
                [(bid, u, a) for u, a in amounts.items()])
            await c.execute("UPDATE bills SET status = 'closed', closed_at = now() WHERE id = $1", bid)

    async def debts(self, bid: int) -> list[asyncpg.Record]:
        return await self.pool.fetch(
            """SELECT d.*, u.first_name, u.username FROM debts d JOIN users u ON u.id = d.user_id
               WHERE d.bill_id = $1 ORDER BY d.id""", bid)

    async def debt(self, did: int) -> asyncpg.Record | None:
        return await self.pool.fetchrow(
            """SELECT d.*, u.first_name, u.username FROM debts d JOIN users u ON u.id = d.user_id WHERE d.id = $1""", did)

    async def debt_for(self, bid: int, uid: int) -> asyncpg.Record | None:
        return await self.pool.fetchrow("SELECT * FROM debts WHERE bill_id = $1 AND user_id = $2", bid, uid)

    async def open_debts_of(self, uid: int) -> list[asyncpg.Record]:
        return await self.pool.fetch(
            "SELECT * FROM debts WHERE user_id = $1 AND status = 'pending' ORDER BY id DESC", uid)

    async def report_debt(self, did: int, receipt_chat: int | None = None, receipt_msg: int | None = None) -> None:
        await self.pool.execute(
            """UPDATE debts SET status = 'reported', reported_at = now(), remind_at = NULL,
               receipt_chat_id = $2, receipt_msg_id = $3 WHERE id = $1 AND status = 'pending'""",
            did, receipt_chat, receipt_msg)

    async def confirm_debt(self, did: int) -> bool:
        r = await self.pool.execute(
            "UPDATE debts SET status = 'confirmed', confirmed_at = now(), remind_at = NULL WHERE id = $1 AND status <> 'confirmed'",
            did)
        return r.endswith(" 1")

    async def reject_debt(self, did: int) -> None:
        await self.pool.execute(
            "UPDATE debts SET status = 'pending', reported_at = NULL, receipt_chat_id = NULL, receipt_msg_id = NULL WHERE id = $1",
            did)

    async def postpone(self, did: int, until: dt.datetime) -> None:
        await self.pool.execute("UPDATE debts SET remind_at = $2 WHERE id = $1", did, until)

    async def touch_ping(self, did: int, min_gap: dt.timedelta) -> bool:
        """True if the ping is allowed now (and records it)."""
        r = await self.pool.execute(
            """UPDATE debts SET last_ping_at = now() WHERE id = $1 AND status = 'pending'
               AND (last_ping_at IS NULL OR last_ping_at < now() - $2::interval)""", did, min_gap)
        return r.endswith(" 1")

    async def due_reminders(self) -> list[asyncpg.Record]:
        return await self.pool.fetch(
            """UPDATE debts SET remind_at = NULL WHERE remind_at <= now() AND status = 'pending'
               RETURNING *""")

    # donations ---------------------------------------------------------------
    async def create_donation(self, uid: int, kind: str, amount: int, rchat: int | None, rmsg: int | None) -> int:
        return await self.pool.fetchval(
            """INSERT INTO donations (user_id, kind, amount, receipt_chat_id, receipt_msg_id)
               VALUES ($1, $2, $3, $4, $5) RETURNING id""", uid, kind, amount, rchat, rmsg)

    async def donation(self, did: int) -> asyncpg.Record | None:
        return await self.pool.fetchrow(
            "SELECT d.*, u.first_name, u.username FROM donations d JOIN users u ON u.id = d.user_id WHERE d.id = $1", did)

    async def decide_donation(self, did: int, ok: bool) -> bool:
        r = await self.pool.execute(
            "UPDATE donations SET status = $2, decided_at = now() WHERE id = $1 AND status = 'pending'",
            did, "confirmed" if ok else "rejected")
        return r.endswith(" 1")

    async def collected(self, kind: str, start: dt.datetime, end: dt.datetime) -> int:
        return await self.pool.fetchval(
            """SELECT COALESCE(SUM(amount), 0)::bigint FROM donations
               WHERE status = 'confirmed' AND kind = $1 AND decided_at >= $2 AND decided_at < $3""", kind, start, end)

    async def leaderboard(self, start: dt.datetime | None, end: dt.datetime | None, limit: int = 10) -> list[asyncpg.Record]:
        return await self.pool.fetch(
            """SELECT u.id, u.first_name, u.username,
                      SUM(d.amount) FILTER (WHERE d.kind = 'server')::bigint AS server,
                      SUM(d.amount) FILTER (WHERE d.kind = 'coffee')::bigint AS coffee,
                      SUM(d.amount)::bigint AS total
               FROM donations d JOIN users u ON u.id = d.user_id
               WHERE d.status = 'confirmed' AND ($1::timestamptz IS NULL OR d.decided_at >= $1)
                 AND ($2::timestamptz IS NULL OR d.decided_at < $2)
               GROUP BY u.id ORDER BY total DESC LIMIT $3""", start, end, limit)

    async def covers(self, start: dt.date | None, end: dt.date | None) -> int:
        return await self.pool.fetchval(
            """SELECT COALESCE(SUM(covered), 0)::bigint FROM server_covers
               WHERE ($1::date IS NULL OR month >= $1) AND ($2::date IS NULL OR month < $2)""", start, end)

    async def cover_done(self, month: dt.date) -> bool:
        return bool(await self.pool.fetchval("SELECT 1 FROM server_covers WHERE month = $1", month))

    async def save_cover(self, month: dt.date, goal: int, collected: int) -> None:
        await self.pool.execute(
            "INSERT INTO server_covers (month, goal, collected, covered) VALUES ($1, $2, $3, $4) ON CONFLICT DO NOTHING",
            month, goal, collected, max(0, goal - collected))

    async def started_month(self) -> dt.date:
        v = await self.pool.fetchval("SELECT value FROM meta WHERE key = 'started_month'")
        return dt.date.fromisoformat(v)
