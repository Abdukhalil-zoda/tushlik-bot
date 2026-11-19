"""Bill lifecycle: JSON in DM -> pick a group -> people pick dishes -> payer closes."""
from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from .. import ui
from ..bill_schema import BillError, parse
from ..core import Ctx, close_bill, dm, refresh_group, split_of
from ..split import bill_total

log = logging.getLogger(__name__)
router = Router()

MEMBER = {"creator", "administrator", "member", "restricted"}


async def is_member(bot: Bot, chat_id: int, uid: int) -> bool:
    try:
        cm = await bot.get_chat_member(chat_id, uid)
    except TelegramBadRequest:
        return False
    return cm.status in MEMBER


# --- 1. JSON in DM ---------------------------------------------------------------------


@router.message(F.chat.type == "private", F.text.startswith("{"))
async def json_text(m: Message, ctx: Ctx):
    await take_bill(m, ctx, m.text)


@router.message(F.chat.type == "private", F.document.file_name.lower().endswith(".json"))
async def json_file(m: Message, ctx: Ctx):
    if m.document.file_size and m.document.file_size > 100_000:
        await m.answer("Файл слишком большой (больше 100 КБ).")
        return
    buf = await ctx.bot.download(m.document)
    await take_bill(m, ctx, buf.read().decode("utf-8-sig", errors="replace"))


async def take_bill(m: Message, ctx: Ctx, text: str):
    await ctx.repo.upsert_user(m.from_user.id, m.from_user.username, m.from_user.first_name)
    try:
        draft = parse(text)
    except BillError as e:
        await m.answer("❌ Не смог прочитать счёт:\n• " + "\n• ".join(ui.escape(p) for p in e.problems)
                       + "\n\nПример: /help")
        return
    bid = await ctx.repo.create_draft(m.from_user.id, draft)
    items = await ctx.repo.items(bid)
    total = bill_total(items, draft.service_percent)
    lines = [f"🧾 <b>{ui.escape(draft.title)}</b>: {len(items)} позиций, итого {ui.money(total, ctx.cfg)}"]
    if draft.total is not None and abs(draft.total - total) > ctx.cfg.round_to:
        lines.append(f"⚠️ В чеке написано {ui.money(draft.total, ctx.cfg)}, а у меня выходит {ui.money(total, ctx.cfg)}. "
                     "Проверь цены и количество. Если всё верно, просто публикуй.")
    await m.answer("\n".join(lines))
    await offer_pending_post(m, ctx)


async def offer_pending_post(m: Message, ctx: Ctx):
    uid = m.chat.id
    bill = await ctx.repo.last_draft(uid)
    if not bill:
        return
    u = await ctx.repo.user(uid)
    if not u or not u["card_number"]:
        await m.answer("Сначала укажи свою карту, чтобы ребята знали, куда скидывать: /card")
        return
    b = InlineKeyboardBuilder()
    for c in (await ctx.repo.active_chats())[:30]:
        if await is_member(ctx.bot, c["id"], uid):
            b.button(text=c["title"][:40] or str(c["id"]), callback_data=ui.Post(bill=bill["id"], chat=c["id"]))
    b.adjust(1)
    kb = b.as_markup()
    if not kb.inline_keyboard:
        await m.answer(f"Добавь меня в вашу группу (@{ctx.username}), потом пришли JSON ещё раз "
                       "или нажми /menu → 📋 Мои счета.")
        return
    await m.answer("В какую группу отправить счёт?", reply_markup=kb)


# --- 2. publish to the group -------------------------------------------------------------


@router.callback_query(ui.Post.filter())
async def post(cb: CallbackQuery, callback_data: ui.Post, ctx: Ctx):
    bill = await ctx.repo.bill(callback_data.bill)
    if not bill or bill["payer_id"] != cb.from_user.id or bill["status"] != "draft":
        await cb.answer("Этот счёт уже опубликован или отменён.", show_alert=True)
        return
    if not await is_member(ctx.bot, callback_data.chat, cb.from_user.id):
        await cb.answer("Ты не в этой группе.", show_alert=True)
        return
    bill, items, claims, people, s = await split_of(ctx, bill["id"])
    payer = await ctx.repo.user(bill["payer_id"])
    opened = dict(bill) | {"status": "open"}
    msg = await ctx.bot.send_message(callback_data.chat, ui.bill_text(ctx.cfg, opened, payer, items, claims, people, s),
                                     reply_markup=ui.bill_kb(opened, ctx.username), disable_web_page_preview=True)
    await ctx.repo.publish(bill["id"], callback_data.chat, msg.message_id)
    await cb.message.edit_text("✅ Счёт отправлен в группу. Твои блюда из JSON («me») уже отмечены — проверь или поправь:")
    await cb.answer()
    await open_picker(cb.message, ctx, bill["id"], uid=cb.from_user.id)


# --- 3. picking dishes in DM -------------------------------------------------------------


async def picker_text(ctx: Ctx, bid: int, uid: int) -> tuple[str, dict[int, int]]:
    bill, items, claims, people, s = await split_of(ctx, bid)
    mine = {iid: c[uid] for iid, c in claims.items() if uid in c}
    shared = [it for it in items if it.shared]
    lines = [f"🧾 <b>{ui.escape(bill['title'])}</b>", "Нажимай на то, что ты ел: «+» добавляет порцию, «−» убирает."]
    if shared:
        lines.append("Общее делится на всех: " + ", ".join(ui.escape(it.name) for it in shared))
    if uid in s.amounts:
        lines.append(f"\nСейчас твоя доля: <b>{ui.money(s.amounts[uid], ctx.cfg)}</b> (общее пересчитается, когда отметятся все)")
    return "\n".join(lines), mine


async def open_picker(m: Message, ctx: Ctx, bid: int, uid: int | None = None):
    uid = uid or m.from_user.id
    bill = await ctx.repo.bill(bid)
    if not bill or bill["status"] != "open":
        await m.answer("Этот счёт уже закрыт или отменён.")
        return
    if not await is_member(ctx.bot, bill["chat_id"], uid):
        await m.answer("Отмечать могут только участники группы, где опубликован счёт.")
        return
    text, mine = await picker_text(ctx, bid, uid)
    await m.answer(text, reply_markup=ui.pick_kb(bid, await ctx.repo.items(bid), mine))


async def redraw_picker(cb: CallbackQuery, ctx: Ctx, bid: int):
    text, mine = await picker_text(ctx, bid, cb.from_user.id)
    try:
        await cb.message.edit_text(text, reply_markup=ui.pick_kb(bid, await ctx.repo.items(bid), mine))
    except TelegramBadRequest as e:
        if "not modified" not in str(e):
            raise


@router.callback_query(ui.Pick.filter())
async def pick(cb: CallbackQuery, callback_data: ui.Pick, ctx: Ctx):
    bid, uid = callback_data.bill, cb.from_user.id
    bill = await ctx.repo.bill(bid)
    if not bill or bill["status"] != "open":
        await cb.answer("Счёт уже закрыт.", show_alert=True)
        return
    item = next((it for it in await ctx.repo.items(bid) if it.id == callback_data.item), None)
    if not item:
        await cb.answer()
        return
    await ctx.repo.upsert_user(uid, cb.from_user.username, cb.from_user.first_name)
    have = (await ctx.repo.claims(bid)).get(item.id, {}).get(uid, 0)
    n = min(item.qty, have + 1) if callback_data.op == "+" else max(0, have - 1)
    if n == have:
        await cb.answer("Больше порций нет." if callback_data.op == "+" else "")
        return
    await ctx.repo.set_claim(item.id, uid, n)
    if n:
        await ctx.repo.join(bid, uid)
    elif uid != bill["payer_id"] and not any(uid in c for c in (await ctx.repo.claims(bid)).values()):
        await ctx.repo.leave(bid, uid)  # picked nothing any more: no share of the common things either
    await redraw_picker(cb, ctx, bid)
    await cb.answer()


@router.callback_query(ui.BillA.filter(F.a.in_({"done", "shared"})))
async def picked(cb: CallbackQuery, callback_data: ui.BillA, ctx: Ctx):
    bid, uid = callback_data.bill, cb.from_user.id
    bill = await ctx.repo.bill(bid)
    if not bill or bill["status"] != "open":
        await cb.answer("Счёт уже закрыт.", show_alert=True)
        return
    await ctx.repo.upsert_user(uid, cb.from_user.username, cb.from_user.first_name)
    if callback_data.a == "shared":
        await ctx.repo.join(bid, uid)
        await redraw_picker(cb, ctx, bid)
    await refresh_group(ctx, bid)
    if callback_data.a == "done":
        _, _, _, _, s = await split_of(ctx, bid)
        if uid in s.amounts:
            await cb.message.edit_text(
                f"✅ Записал. Твоя доля сейчас {ui.money(s.amounts[uid], ctx.cfg)}. Когда платил закроет счёт, "
                "я пришлю точную сумму и карту.")
        else:
            await cb.message.edit_text("Ты ничего не отметил. Если передумаешь, нажми кнопку в группе ещё раз.")
    await cb.answer("Записал")


# --- 4. closing -----------------------------------------------------------------------


@router.callback_query(ui.BillA.filter(F.a.in_({"close", "close_ok"})))
async def close(cb: CallbackQuery, callback_data: ui.BillA, ctx: Ctx):
    bid = callback_data.bill
    bill = await ctx.repo.bill(bid)
    if not bill or bill["status"] != "open":
        await cb.answer("Счёт уже закрыт.", show_alert=True)
        return
    if bill["payer_id"] != cb.from_user.id:
        await cb.answer("Закрыть счёт может только тот, кто платил.", show_alert=True)
        return
    _, _, _, _, s = await split_of(ctx, bid)
    if callback_data.a == "close" and (s.unclaimed or not s.amounts):
        b = InlineKeyboardBuilder()
        b.button(text="✅ Закрыть всё равно", callback_data=ui.BillA(bill=bid, a="close_ok"))
        un = ", ".join(f"{ui.escape(it.name)} ×{n}" for it, n, _ in s.unclaimed) or "—"
        sent = await dm(ctx, bill["payer_id"],
                        f"Никто не отметил: {un} ({ui.money(s.unclaimed_total, ctx.cfg)}). Эта часть останется на тебе. "
                        "Закрыть счёт сейчас?", b.as_markup())
        await cb.answer("Не всё выбрано — подтверди в личке." if sent else "Не всё выбрано.", show_alert=True)
        return
    n = await close_bill(ctx, bid)
    await cb.answer(f"Закрыл. Сумму получили {n} чел.")
    if cb.message.chat.type == "private":
        await cb.message.edit_text(f"✅ Счёт закрыт, отправил суммы {n} людям. Статус: /menu → 📋 Мои счета.")
    else:
        await dm(ctx, bill["payer_id"], f"✅ Счёт закрыт, отправил суммы {n} людям. Статус: /menu → 📋 Мои счета.")


@router.callback_query(ui.BillA.filter(F.a == "cancel"))
async def cancel(cb: CallbackQuery, callback_data: ui.BillA, ctx: Ctx):
    bill = await ctx.repo.bill(callback_data.bill)
    if not bill or bill["payer_id"] != cb.from_user.id or bill["status"] not in ("draft", "open"):
        await cb.answer("Отменить можно только свой незакрытый счёт.", show_alert=True)
        return
    await ctx.repo.set_status(bill["id"], "cancelled")
    if bill["group_message_id"]:
        try:
            await ctx.bot.edit_message_text(f"🚫 Счёт «{ui.escape(bill['title'])}» отменён.", chat_id=bill["chat_id"],
                                            message_id=bill["group_message_id"])
        except TelegramBadRequest:
            pass
    await cb.message.edit_text("🚫 Счёт отменён.")
    await cb.answer()


# --- 5. my bills -----------------------------------------------------------------------

LABEL = {"draft": "черновик", "open": "отмечают", "closed": "собираем", "done": "закрыт"}


async def list_bills(m: Message, ctx: Ctx, uid: int):
    rows = await ctx.repo.my_bills(uid)
    if not rows:
        await m.answer("Счетов пока нет. Как создать: /help")
        return
    b = InlineKeyboardBuilder()
    for r in rows:
        role = "💳" if r["payer_id"] == uid else "🍽"
        b.button(text=f"{role} {r['title'][:28]} · {LABEL.get(r['status'], r['status'])}",
                 callback_data=ui.BillA(bill=r["id"], a="view"))
    b.adjust(1)
    await m.answer("📋 Твои счета (💳 — платил ты):", reply_markup=b.as_markup())


@router.callback_query(ui.BillA.filter(F.a == "view"))
async def view(cb: CallbackQuery, callback_data: ui.BillA, ctx: Ctx):
    bid, uid = callback_data.bill, cb.from_user.id
    bill = await ctx.repo.bill(bid)
    if not bill:
        await cb.answer()
        return
    b = InlineKeyboardBuilder()
    lines = [f"🧾 <b>{ui.escape(bill['title'])}</b> · {LABEL.get(bill['status'], bill['status'])}"]
    if bill["payer_id"] == uid:
        if bill["status"] == "draft":
            await cb.answer()
            await offer_pending_post(cb.message, ctx)
            return
        if bill["status"] == "open":
            b.button(text="✅ Закрыть счёт", callback_data=ui.BillA(bill=bid, a="close"))
            b.button(text="🚫 Отменить", callback_data=ui.BillA(bill=bid, a="cancel"))
        for d in await ctx.repo.debts(bid):
            mark = ui.STATUS.get(d["status"], "⏳")
            if d["status"] == "pending" and d["remind_at"]:
                mark = "⏰"
            lines.append(f"{mark} {ui.who(d['user_id'], d['first_name'], d['username'])} — {ui.money(d['amount'], ctx.cfg)}")
            if d["status"] == "pending":
                b.button(text=f"🔔 Пингануть {d['first_name'][:20]}", callback_data=ui.DebtA(debt=d["id"], a="ping"))
            elif d["status"] == "reported":
                b.button(text=f"✅ {d['first_name'][:20]}: деньги пришли", callback_data=ui.DebtA(debt=d["id"], a="ok"))
    else:
        d = await ctx.repo.debt_for(bid, uid)
        if d:
            lines.append(f"Твоя сумма: {ui.money(d['amount'], ctx.cfg)} {ui.STATUS.get(d['status'], '')}")
            if d["status"] == "pending":
                await cb.message.answer("\n".join(lines), reply_markup=ui.debt_kb(d["id"]))
                await cb.answer()
                return
        elif bill["status"] == "open":
            b.button(text="🍽 Изменить, что я ел", url=f"https://t.me/{ctx.username}?start=b{bid}")
    b.adjust(1)
    await cb.message.answer("\n".join(lines), reply_markup=b.as_markup() if b.as_markup().inline_keyboard else None)
    await cb.answer()
