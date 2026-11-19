"""Groups: remember where the bot was added, /server and /help in the group."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import JOIN_TRANSITION, LEAVE_TRANSITION, ChatMemberUpdatedFilter, Command
from aiogram.types import ChatMemberUpdated, Message

from ..core import Ctx, server_status

router = Router()
router.message.filter(F.chat.type.in_({"group", "supergroup"}))


@router.my_chat_member(ChatMemberUpdatedFilter(JOIN_TRANSITION))
async def added(ev: ChatMemberUpdated, ctx: Ctx):
    if ev.chat.type not in ("group", "supergroup"):
        return
    await ctx.repo.upsert_chat(ev.chat.id, ev.chat.title or "", True)
    await ctx.bot.send_message(
        ev.chat.id,
        "👋 Привет! Я делю общий счёт за обед.\n\n"
        f"Тот, кто платил, присылает мне в личку (@{ctx.username}) JSON с чеком, и я публикую его здесь. "
        "Каждый отмечает, что ел, и я считаю, кто сколько должен.\n\nПодробно: напиши мне /help в личку.")


@router.my_chat_member(ChatMemberUpdatedFilter(LEAVE_TRANSITION))
async def removed(ev: ChatMemberUpdated, ctx: Ctx):
    if ev.chat.type not in ("group", "supergroup"):
        return
    await ctx.repo.upsert_chat(ev.chat.id, ev.chat.title or "", False)


@router.message(Command("server", "donate"))
async def server(m: Message, ctx: Ctx):
    await m.answer(await server_status(ctx) + f"\n\nПоддержать: напиши мне в личку @{ctx.username} → /donate")


@router.message(Command("help", "start"))
async def help_(m: Message, ctx: Ctx):
    await ctx.repo.upsert_chat(m.chat.id, m.chat.title or "", True)
    await m.answer(f"Чтобы разделить счёт, пришли JSON с чеком мне в личку: @{ctx.username}. Пример там же: /help")


@router.message(F.migrate_to_chat_id)
async def migrated(m: Message, ctx: Ctx):
    await ctx.repo.upsert_chat(m.chat.id, m.chat.title or "", False)
    await ctx.repo.upsert_chat(m.migrate_to_chat_id, m.chat.title or "", True)
