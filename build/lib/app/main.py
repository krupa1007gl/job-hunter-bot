"""Точка входа: бот (long polling) + планировщик + служебный API в одном процессе."""

import asyncio
import logging

import httpx
import uvicorn
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from .api import create_app
from .bot.handlers import router
from .bot.keyboards import card_kb
from .bot.middlewares import AppMiddleware
from .bot.scheduler import scheduler_loop
from .config import Settings
from .core.crypto import KeyCipher
from .db import init_db, make_engine, make_sessionmaker


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # не пишем URL запросов (в них бывают токены)
    settings = Settings()
    cipher = KeyCipher(settings.master_key.get_secret_value())
    engine = make_engine(settings.database_url)
    await init_db(engine)
    sm = make_sessionmaker(engine)

    bot = Bot(settings.bot_token.get_secret_value(), default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    async with httpx.AsyncClient(follow_redirects=False) as http:
        mw = AppMiddleware(sm, settings, cipher, http)
        dp.message.outer_middleware(mw)
        dp.callback_query.outer_middleware(mw)
        dp.include_router(router)

        async def send(tg_id: int, text: str, offer_id: int) -> None:
            await bot.send_message(tg_id, text, reply_markup=card_kb(offer_id), disable_web_page_preview=True)

        api = uvicorn.Server(
            uvicorn.Config(
                create_app(sm, settings.internal_token.get_secret_value()),
                host=settings.api_host,
                port=settings.api_port,
                log_level="warning",
            )
        )
        tasks = [
            asyncio.create_task(api.serve()),
            asyncio.create_task(scheduler_loop(sm, http, settings, cipher, send)),
        ]
        try:
            await dp.start_polling(bot)
        finally:
            api.should_exit = True
            for t in tasks:
                t.cancel()
            await bot.session.close()
            await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
