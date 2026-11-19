"""Paying back: receipt or «I paid», postpone, payer confirms, pings."""
from __future__ import annotations

import datetime as dt

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from .. import ui
from ..core import Ctx, dm, maybe_done, refresh_group, send_debt

router = Router()
PING_GAP = dt.timedelta(minutes=10)


class ReceiptForm(StatesGroup):
    wait = State()


async def load(cb: CallbackQuery, ctx: Ctx, debt_id: int):
    d = await ctx.repo.debt(debt_id)
    if not d:
        await cb.answer("Не нашёл этот долг.", show_alert=True)
        return None, None
    return d, await ctx.repo.bill(d["bill_id"])


# --- debtor --------------------------------------------------------------------------


@router.callback_query(ui.DebtA.filter(F.a.in_({"paid", "receipt", "later", "l1", "l3", "lt"})))
async def debtor(cb: CallbackQuery, callback_data: ui.DebtA, ctx: Ctx, state: FSMContext):
    d, bill = await load(cb, ctx, callback_data.debt)
    if not d:
        return
    if d["user_id"] != cb.from_user.id:
        await cb.answer("Это не твой долг.", show_alert=True)
        return
    if d["status"] != "pending":
        await cb.answer("Уже отмечено как оплачено.", show_alert=True)
        return
    a = callback_data.a
    if a == "receipt":
        await state.set_state(ReceiptForm.wait)
        await state.update_data(debt=d["id"])
        await cb.message.answer("Пришли чек: фото или файл. /cancel — отмена")
    elif a == "paid":
        await report(ctx, d, bill, None)
        await cb.message.edit_reply_markup(reply_markup=None)
        await cb.message.answer("👍 Передал платившему, ждём его подтверждения.")
    elif a == "later":
        await cb.message.edit_reply_markup(reply_markup=ui.later_kb(d["id"]))
    else:
        until = ui.later_until(a, ctx.cfg.tz)
        await ctx.repo.postpone(d["id"], until)
        await cb.message.edit_reply_markup(reply_markup=None)
        await cb.message.answer(f"⏰ Напомню {until:%d.%m в %H:%M}.")
        await dm(ctx, bill["payer_id"], f"⏰ {ui.who(d['user_id'], d['first_name'], d['username'])} отложил оплату "
                                        f"{ui.money(d['amount'], ctx.cfg)} за «{ui.escape(bill['title'])}» до {until:%d.%m %H:%M}.")
        await refresh_group(ctx, bill["id"])
    await cb.answer()


@router.message(ReceiptForm.wait, F.chat.type == "private", F.photo | F.document)
async def receipt(m: Message, ctx: Ctx, state: FSMContext):
    data = await state.get_data()
    await state.clear()
    d = await ctx.repo.debt(data["debt"])
    if not d or d["status"] != "pending":
        await m.answer("Этот долг уже отмечен.")
        return
    await report(ctx, d, await ctx.repo.bill(d["bill_id"]), m)
    await m.answer("👍 Чек отправил платившему, ждём его подтверждения.")


@router.message(ReceiptForm.wait, F.chat.type == "private")
async def receipt_wrong(m: Message):
    await m.answer("Жду фото или файл чека. /cancel — отмена")


async def report(ctx: Ctx, d, bill, receipt_msg: Message | None):
    await ctx.repo.report_debt(d["id"], receipt_msg.chat.id if receipt_msg else None,
                               receipt_msg.message_id if receipt_msg else None)
    text = (f"💸 {ui.who(d['user_id'], d['first_name'], d['username'])} пишет, что скинул "
            f"{ui.money(d['amount'], ctx.cfg)} за «{ui.escape(bill['title'])}»." + ("" if receipt_msg else " Без чека."))
    if receipt_msg:
        await ctx.bot.copy_message(bill["payer_id"], receipt_msg.chat.id, receipt_msg.message_id, caption=text,
                                   reply_markup=ui.payer_check_kb(d["id"]))
    else:
        await dm(ctx, bill["payer_id"], text, ui.payer_check_kb(d["id"]))
    await refresh_group(ctx, bill["id"])


# --- payer -----------------------------------------------------------------------------


@router.callback_query(ui.DebtA.filter(F.a.in_({"ok", "no", "ping"})))
async def payer(cb: CallbackQuery, callback_data: ui.DebtA, ctx: Ctx):
    d, bill = await load(cb, ctx, callback_data.debt)
    if not d:
        return
    if bill["payer_id"] != cb.from_user.id:
        await cb.answer("Это может только тот, кто платил.", show_alert=True)
        return
    a = callback_data.a
    name = ui.who(d["user_id"], d["first_name"], d["username"])
    if a == "ok":
        if await ctx.repo.confirm_debt(d["id"]):
            await dm(ctx, d["user_id"], f"✅ Платёж {ui.money(d['amount'], ctx.cfg)} за «{ui.escape(bill['title'])}» "
                                        "подтверждён. Спасибо!\n\nЕсли бот помог — можно поддержать сервер или купить "
                                        "автору кофе.", ui.donate_nudge_kb())
            await refresh_group(ctx, bill["id"])
            await maybe_done(ctx, bill["id"])
        await cb.answer("Подтвердил")
        await cb.message.edit_reply_markup(reply_markup=None)
    elif a == "no":
        await ctx.repo.reject_debt(d["id"])
        await send_debt(ctx, await ctx.repo.debt(d["id"]), bill, await ctx.repo.user(bill["payer_id"]),
                        prefix="❌ Платившему деньги не пришли. Проверь перевод.\n\n")
        await refresh_group(ctx, bill["id"])
        await cb.answer("Отправил ему ещё раз")
        await cb.message.edit_reply_markup(reply_markup=None)
    else:
        if not await ctx.repo.touch_ping(d["id"], PING_GAP):
            await cb.answer("Уже пинговал недавно или он уже оплатил. Пинговать можно раз в 10 минут.", show_alert=True)
            return
        payer_u = await ctx.repo.user(bill["payer_id"])
        ok = await send_debt(ctx, d, bill, payer_u, prefix="🔔 Напоминание: скинь, пожалуйста.\n\n")
        await cb.answer(f"Пинганул {d['first_name']}" if ok else "Не смог написать: он не запускал бота.", show_alert=not ok)
        if not ok and bill["chat_id"]:
            await ctx.bot.send_message(bill["chat_id"], f"🔔 {name}, напиши мне в личку @{ctx.username}: "
                                                        f"там твоя сумма за «{ui.escape(bill['title'])}».")
