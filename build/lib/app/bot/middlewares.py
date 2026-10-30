"""Сессия БД, доступ (allowlist) и rate limit для каждого апдейта (REQ-001, REQ-015)."""

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from ..core.ratelimit import SlidingWindowLimiter
from ..services import users


class AppMiddleware(BaseMiddleware):
    def __init__(self, sm, settings, cipher, http) -> None:
        self.sm, self.settings, self.cipher, self.http = sm, settings, cipher, http
        self.limiter = SlidingWindowLimiter(settings.rate_limit_per_min, 60)

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        tg_user = getattr(event, "from_user", None)
        if tg_user is None or tg_user.is_bot:
            return None
        if not users.check_access(tg_user.id, self.settings.allowlist_ids):
            if isinstance(event, Message):
                await event.answer("Бот сейчас в закрытой бете. Скоро откроемся!")
            return None
        if not self.limiter.allow(tg_user.id):
            if isinstance(event, CallbackQuery):
                await event.answer("Слишком часто, подожди минуту", show_alert=False)
            elif isinstance(event, Message):
                await event.answer("Слишком часто, подожди минуту")
            return None
        async with self.sm() as session:
            data.update(
                session=session,
                user=await users.get_user(session, tg_user.id),
                settings=self.settings,
                cipher=self.cipher,
                http=self.http,
            )
            return await handler(event, data)
