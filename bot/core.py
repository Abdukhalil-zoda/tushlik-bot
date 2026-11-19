"""Shared actions used by several handlers: recompute a bill, refresh its group message, notify people."""
from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import InlineKeyboardMarkup

from . import ui
from .config import Config
from .db import Repo
from .split import Split, compute

log = logging.getLogger(__name__)


@dataclass
class Ctx:
    cfg: Config
    repo: Repo
    bot: Bot
    username: str  # bot username, for deep links


async def dm(ctx: Ctx, uid: int, text: str, kb: InlineKeyboardMarkup | None = None) -> bool:
    """Private message; False if the person never started the bot or blocked it."""
    try:
        await ctx.bot.send_message(uid, text, reply_markup=kb, disable_web_page_preview=True)
        return True
    except (TelegramForbiddenError, TelegramBadRequest) as e:
        log.info("dm to %s failed: %s", uid, e)
        return False


async def split_of(ctx: Ctx, bid: int):
    bill = await ctx.repo.bill(bid)
    items = await ctx.repo.items(bid)
    claims = await ctx.repo.claims(bid)
    parts = await ctx.repo.participants(bid)
    people = {p["id"]: (p["first_name"], p["username"]) for p in parts}
    s: Split = compute(items, claims, set(people), bill["service_percent"], ctx.cfg.round_to)
    return bill, items, claims, people, s


async def refresh_group(ctx: Ctx, bid: int) -> None:
    bill, items, claims, people, s = await split_of(ctx, bid)
    if not bill["group_message_id"]:
        return
    payer = await ctx.repo.user(bill["payer_id"])
    debts = await ctx.repo.debts(bid) if bill["status"] in ("closed", "done") else None
    text = ui.bill_text(ctx.cfg, bill, payer, items, claims, people, s, debts)
    try:
        await ctx.bot.edit_message_text(text, chat_id=bill["chat_id"], message_id=bill["group_message_id"],
                                        reply_markup=ui.bill_kb(bill, ctx.username), disable_web_page_preview=True)
    except TelegramBadRequest as e:
        if "not modified" not in str(e):
            log.warning("refresh bill %s: %s", bid, e)


async def close_bill(ctx: Ctx, bid: int) -> int:
    """Freeze amounts, create debts, DM every debtor. Returns the number of debtors."""
    bill, items, claims, people, s = await split_of(ctx, bid)
    owed = {u: a for u, a in s.amounts.items() if u != bill["payer_id"] and a > 0}
    await ctx.repo.create_debts(bid, owed)
    payer = await ctx.repo.user(bill["payer_id"])
    for d in await ctx.repo.debts(bid):
        await send_debt(ctx, d, bill, payer)
    if not owed:
        await ctx.repo.set_status(bid, "done")
    await refresh_group(ctx, bid)
    return len(owed)


async def send_debt(ctx: Ctx, debt, bill, payer, prefix: str = "") -> bool:
    text = (f"{prefix}🧾 <b>{ui.escape(bill['title'])}</b>\n"
            f"Ты должен {ui.who(payer['id'], payer['first_name'], payer['username'])}: <b>{ui.money(debt['amount'], ctx.cfg)}</b>\n\n"
            f"Карта: {ui.card_text(payer['card_number'], payer['card_holder'])}\n\n"
            "Переведи и пришли чек (фото или файл) или просто нажми «Я оплатил».")
    return await dm(ctx, debt["user_id"], text, ui.debt_kb(debt["id"]))


async def maybe_done(ctx: Ctx, bid: int) -> None:
    debts = await ctx.repo.debts(bid)
    if debts and all(d["status"] == "confirmed" for d in debts):
        bill = await ctx.repo.bill(bid)
        if bill["status"] == "done":
            return
        await ctx.repo.set_status(bid, "done")
        total = sum(d["amount"] for d in debts)
        await dm(ctx, bill["payer_id"],
                 f"🎉 Все скинули за «{ui.escape(bill['title'])}»: {ui.money(total, ctx.cfg)}.\n\n"
                 "Если бот помог — можно поддержать сервер или купить автору кофе.", ui.donate_nudge_kb())


def month_bounds(day: dt.date, tz) -> tuple[dt.datetime, dt.datetime]:
    start = dt.datetime(day.year, day.month, 1, tzinfo=tz)
    nxt = dt.datetime(day.year + (day.month == 12), day.month % 12 + 1, 1, tzinfo=tz)
    return start, nxt


async def server_status(ctx: Ctx) -> str:
    today = dt.datetime.now(ctx.cfg.tz).date()
    start, end = month_bounds(today, ctx.cfg.tz)
    got = await ctx.repo.collected("server", start, end)
    goal = ctx.cfg.server_goal
    return (f"🖥 <b>Сервер, {today:%m.%Y}</b>\n{ui.bar(got, goal)}\n"
            f"Собрано {ui.money(got, ctx.cfg)} из {ui.money(goal, ctx.cfg)} "
            f"(${ctx.cfg.server_cost_usd:g} + {ctx.cfg.server_tax_percent:g}% налог = ${ctx.cfg.server_goal_usd}).\n"
            "Если до конца месяца не соберётся, остаток автор платит сам — это видно в лидерборде.")
