import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand, BotCommandScopeAllGroupChats, BotCommandScopeAllPrivateChats

from . import config, db, scheduler
from .core import Ctx
from .handlers import bills, debts, donations, group, start


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = config.load()
    pool = await db.connect(cfg.database_url)
    bot = Bot(cfg.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    me = await bot.get_me()
    ctx = Ctx(cfg, db.Repo(pool), bot, me.username)

    dp = Dispatcher(ctx=ctx)
    # Order matters: form states (card, receipt, donation amount) must win over the generic JSON handler.
    dp.include_routers(start.router, debts.router, donations.router, bills.router, group.router)

    await bot.set_my_commands([
        BotCommand(command="menu", description="Меню"),
        BotCommand(command="help", description="Как разделить счёт"),
        BotCommand(command="card", description="Моя карта"),
        BotCommand(command="donate", description="Поддержать бота"),
        BotCommand(command="top", description="Лидерборд донатов"),
        BotCommand(command="about", description="Open source"),
    ], scope=BotCommandScopeAllPrivateChats())
    await bot.set_my_commands([
        BotCommand(command="help", description="Как разделить счёт"),
        BotCommand(command="server", description="Сколько собрали на сервер"),
    ], scope=BotCommandScopeAllGroupChats())

    tick = asyncio.create_task(scheduler.run(ctx))
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        tick.cancel()
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
