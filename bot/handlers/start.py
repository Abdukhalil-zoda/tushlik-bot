"""Private chat: /start, menu, card, help, about."""
from __future__ import annotations

import re

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from .. import ui
from ..core import Ctx, server_status
from . import bills

router = Router()
router.message.filter(F.chat.type == "private")


class CardForm(StatesGroup):
    number = State()
    holder = State()


async def remember(ctx: Ctx, u) -> None:
    await ctx.repo.upsert_user(u.id, u.username, u.first_name)


@router.message(CommandStart())
async def start(m: Message, command: CommandObject, ctx: Ctx, state: FSMContext):
    await state.clear()
    await remember(ctx, m.from_user)
    arg = command.args or ""
    if re.fullmatch(r"b\d+", arg):
        await bills.open_picker(m, ctx, int(arg[1:]))
        return
    await m.answer(ui.home_text(), reply_markup=ui.home_kb())


@router.message(Command("menu"))
async def menu(m: Message, ctx: Ctx, state: FSMContext):
    await state.clear()
    await remember(ctx, m.from_user)
    await m.answer(ui.home_text(), reply_markup=ui.home_kb())


@router.message(Command("help"))
async def help_(m: Message):
    await m.answer(ui.HELP.format(example=ui.escape(ui.EXAMPLE)))


@router.message(Command("about"))
async def about_cmd(m: Message, ctx: Ctx):
    await m.answer(about_text(ctx))


def about_text(ctx: Ctx) -> str:
    return ("ℹ️ <b>Это open source</b>\n"
            f"Код открыт: {ctx.cfg.repo_url}\n"
            "Любой может предложить улучшение: открой issue или пришли pull request.")


@router.message(Command("card"))
async def card_cmd(m: Message, ctx: Ctx, state: FSMContext):
    await remember(ctx, m.from_user)
    await ask_card(m, ctx, state)


async def ask_card(m: Message, ctx: Ctx, state: FSMContext):
    u = await ctx.repo.user(m.chat.id)
    now = ui.card_text(u["card_number"], u["card_holder"]) if u else "карта не указана"
    await state.set_state(CardForm.number)
    await m.answer(f"💳 Сейчас: {now}\n\nПришли номер карты, на которую тебе будут скидывать деньги "
                   "(16 цифр). Её увидят только те, кто должен тебе, и только в личке со мной.\n/cancel — отмена")


@router.message(Command("cancel"))
async def cancel(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("Отменил.", reply_markup=ui.home_kb())


@router.message(CardForm.number, F.text)
async def card_number(m: Message, state: FSMContext):
    digits = re.sub(r"\D", "", m.text)
    if not 12 <= len(digits) <= 19:
        await m.answer("Не похоже на номер карты. Пришли 16 цифр, можно с пробелами.")
        return
    await state.update_data(number=" ".join(digits[i:i + 4] for i in range(0, len(digits), 4)))
    await state.set_state(CardForm.holder)
    await m.answer("Имя на карте (как в банке), чтобы ребята не перепутали. Или «-», чтобы пропустить.")


@router.message(CardForm.holder, F.text)
async def card_holder(m: Message, ctx: Ctx, state: FSMContext):
    data = await state.get_data()
    holder = "" if m.text.strip() == "-" else m.text.strip()[:64]
    await ctx.repo.set_card(m.from_user.id, data["number"], holder)
    await state.clear()
    await m.answer(f"✅ Карта сохранена: {ui.card_text(data['number'], holder)}")
    await bills.offer_pending_post(m, ctx)


# --- menu buttons ------------------------------------------------------------------


@router.callback_query(ui.Menu.filter())
async def menu_cb(cb: CallbackQuery, callback_data: ui.Menu, ctx: Ctx, state: FSMContext):
    await remember(ctx, cb.from_user)
    a = callback_data.a
    m = cb.message
    if a == "home":
        await m.answer(ui.home_text(), reply_markup=ui.home_kb())
    elif a == "help":
        await m.answer(ui.HELP.format(example=ui.escape(ui.EXAMPLE)))
    elif a == "card":
        await ask_card(m, ctx, state)
    elif a == "bills":
        await bills.list_bills(m, ctx, cb.from_user.id)
    elif a == "donate":
        await m.answer(await server_status(ctx) + "\n\nВыбери, на что:", reply_markup=ui.donate_kb())
    elif a == "top":
        from .donations import show_top
        await show_top(m, ctx, "month")
    elif a == "server":
        await m.answer(await server_status(ctx), reply_markup=ui.donate_kb())
    elif a == "about":
        await m.answer(about_text(ctx))
    await cb.answer()
