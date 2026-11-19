"""Donations: for the server (monthly goal) or a coffee. The owner confirms each one."""
from __future__ import annotations

import datetime as dt
import re

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from .. import ui
from ..core import Ctx, dm, month_bounds, server_status

router = Router()


class DonForm(StatesGroup):
    amount = State()
    receipt = State()


@router.message(F.chat.type == "private", F.text.regexp(r"^/(donate|server)"))
async def donate_cmd(m: Message, ctx: Ctx):
    await m.answer(await server_status(ctx) + "\n\nВыбери, на что:", reply_markup=ui.donate_kb())


@router.message(F.chat.type == "private", F.text.regexp(r"^/top"))
async def top_cmd(m: Message, ctx: Ctx):
    await show_top(m, ctx, "month")


@router.callback_query(ui.Don.filter(F.a == "kind"))
async def kind(cb: CallbackQuery, callback_data: ui.Don, ctx: Ctx, state: FSMContext):
    await ctx.repo.upsert_user(cb.from_user.id, cb.from_user.username, cb.from_user.first_name)
    await state.set_state(DonForm.amount)
    await state.update_data(kind=callback_data.kind)
    await cb.message.answer(f"Донат {ui.KIND[callback_data.kind]}. Сколько отправишь? Напиши сумму в {ctx.cfg.currency}, "
                            "например 20000. /cancel — отмена")
    await cb.answer()


@router.message(DonForm.amount, F.chat.type == "private", F.text)
async def amount(m: Message, ctx: Ctx, state: FSMContext):
    digits = re.sub(r"[\s_.,]", "", m.text)
    if not digits.isdigit() or int(digits) <= 0:
        await m.answer("Напиши сумму числом, например 20000.")
        return
    n = int(digits)
    data = await state.update_data(amount=n)
    await state.set_state(None)
    await m.answer(f"Переведи <b>{ui.money(n, ctx.cfg)}</b> на карту {ui.card_text(ctx.cfg.owner_card, ctx.cfg.owner_card_holder)}, "
                   "потом нажми кнопку.", reply_markup=ui.donate_send_kb(data["kind"]))


@router.callback_query(ui.Don.filter(F.a.in_({"sent", "receipt", "cancel"})))
async def sent(cb: CallbackQuery, callback_data: ui.Don, ctx: Ctx, state: FSMContext):
    data = await state.get_data()
    if callback_data.a == "cancel" or "amount" not in data:
        await state.clear()
        await cb.message.edit_text("Ок, отменил." if callback_data.a == "cancel" else "Начни заново: /donate")
        await cb.answer()
        return
    if callback_data.a == "receipt":
        await state.set_state(DonForm.receipt)
        await cb.message.answer("Пришли чек: фото или файл.")
        await cb.answer()
        return
    await state.clear()
    await cb.message.edit_reply_markup(reply_markup=None)
    await submit(ctx, cb.from_user, data["kind"], data["amount"], None)
    await cb.message.answer("💛 Спасибо! Автор подтвердит, когда увидит перевод.")
    await cb.answer()


@router.message(DonForm.receipt, F.chat.type == "private", F.photo | F.document)
async def don_receipt(m: Message, ctx: Ctx, state: FSMContext):
    data = await state.get_data()
    await state.clear()
    await submit(ctx, m.from_user, data["kind"], data["amount"], m)
    await m.answer("💛 Спасибо! Автор подтвердит, когда увидит перевод.")


async def submit(ctx: Ctx, user, kind: str, amount: int, receipt: Message | None):
    did = await ctx.repo.create_donation(user.id, kind, amount, receipt.chat.id if receipt else None,
                                        receipt.message_id if receipt else None)
    text = (f"💛 Донат {ui.KIND[kind]}: {ui.who(user.id, user.first_name, user.username)} — "
            f"<b>{ui.money(amount, ctx.cfg)}</b>" + ("" if receipt else " (без чека)") + ". Пришло?")
    if receipt:
        await ctx.bot.copy_message(ctx.cfg.owner_id, receipt.chat.id, receipt.message_id, caption=text,
                                   reply_markup=ui.owner_don_kb(did))
    else:
        await dm(ctx, ctx.cfg.owner_id, text, ui.owner_don_kb(did))


@router.callback_query(ui.Don.filter(F.a.in_({"ok", "no"})))
async def decide(cb: CallbackQuery, callback_data: ui.Don, ctx: Ctx):
    if cb.from_user.id != ctx.cfg.owner_id:
        await cb.answer("Только для автора.", show_alert=True)
        return
    ok = callback_data.a == "ok"
    d = await ctx.repo.donation(callback_data.id)
    if not d or not await ctx.repo.decide_donation(d["id"], ok):
        await cb.answer("Уже решено.")
        return
    await cb.message.edit_reply_markup(reply_markup=None)
    if ok:
        await dm(ctx, d["user_id"], f"✅ Автор подтвердил твой донат {ui.KIND[d['kind']]} — {ui.money(d['amount'], ctx.cfg)}. "
                                    "Спасибо! 💛 Ты в лидерборде: /top")
    else:
        await dm(ctx, d["user_id"], f"❌ Автор не нашёл перевод {ui.money(d['amount'], ctx.cfg)} {ui.KIND[d['kind']]}. "
                                    "Если это ошибка, напиши ему.")
    await cb.answer("Подтвердил" if ok else "Отменил")


# --- leaderboard ---------------------------------------------------------------------------


@router.callback_query(ui.Top.filter())
async def top_cb(cb: CallbackQuery, callback_data: ui.Top, ctx: Ctx):
    await show_top(cb.message, ctx, callback_data.period, edit=True)
    await cb.answer()


async def show_top(m: Message, ctx: Ctx, period: str, edit: bool = False):
    today = dt.datetime.now(ctx.cfg.tz).date()
    if period == "all":
        start = end = None
        title = "за всё время"
    else:
        day = today if period == "month" else today.replace(day=1) - dt.timedelta(days=1)
        start, end = month_bounds(day, ctx.cfg.tz)
        title = f"{day:%m.%Y}"
    rows = await ctx.repo.leaderboard(start, end)
    covered = await ctx.repo.covers(start.date() if start else None, end.date() if end else None)
    lines = [f"🏆 <b>Лидерборд донатов, {title}</b>"]
    medals = ["🥇", "🥈", "🥉"]
    for i, r in enumerate(rows):
        parts = []
        if r["server"]:
            parts.append(f"🖥 {ui.money(r['server'], ctx.cfg)}")
        if r["coffee"]:
            parts.append(f"☕ {ui.money(r['coffee'], ctx.cfg)}")
        lines.append(f"{medals[i] if i < 3 else f'{i + 1}.'} {ui.escape(r['first_name'] or r['username'] or '')} — "
                     f"{ui.money(r['total'], ctx.cfg)} ({', '.join(parts)})")
    if not rows:
        lines.append("Пока никого. Стань первым: /donate")
    if covered:
        lines.append(f"\n👤 Автор доплатил за сервер из своего кармана: {ui.money(covered, ctx.cfg)}")
    text = "\n".join(lines)
    if edit:
        try:
            await m.edit_text(text, reply_markup=ui.top_kb())
            return
        except Exception:
            pass
    await m.answer(text, reply_markup=ui.top_kb())
