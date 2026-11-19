"""Background loop: postponed-payment reminders and the monthly server balance."""
from __future__ import annotations

import asyncio
import datetime as dt
import logging

from . import ui
from .core import Ctx, dm, month_bounds, send_debt

log = logging.getLogger(__name__)


async def run(ctx: Ctx, every: int = 60) -> None:
    while True:
        try:
            await reminders(ctx)
            await month_end(ctx)
        except Exception:
            log.exception("scheduler tick failed")
        await asyncio.sleep(every)


async def reminders(ctx: Ctx) -> None:
    for d in await ctx.repo.due_reminders():
        bill = await ctx.repo.bill(d["bill_id"])
        payer = await ctx.repo.user(bill["payer_id"])
        await send_debt(ctx, d, bill, payer, prefix="⏰ Ты просил напомнить.\n\n")


async def month_end(ctx: Ctx) -> None:
    """Once the month is over, record how much of the server goal the owner had to pay himself."""
    today = dt.datetime.now(ctx.cfg.tz).date()
    prev = today.replace(day=1) - dt.timedelta(days=1)
    month = prev.replace(day=1)
    if month < await ctx.repo.started_month() or await ctx.repo.cover_done(month):
        return
    start, end = month_bounds(prev, ctx.cfg.tz)
    got = await ctx.repo.collected("server", start, end)
    goal = ctx.cfg.server_goal
    await ctx.repo.save_cover(month, goal, got)
    gap = max(0, goal - got)
    await dm(ctx, ctx.cfg.owner_id,
             f"🖥 Итог за {month:%m.%Y}: на сервер собрали {ui.money(got, ctx.cfg)} из {ui.money(goal, ctx.cfg)}."
             + (f" Остаток {ui.money(gap, ctx.cfg)} записан на тебя в лидерборде." if gap else " Хватило 🎉"))
