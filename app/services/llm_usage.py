"""Суточный лимит LLM-вызовов на пользователя (REQ-009 п. 3)."""

from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import LLMUsage


async def try_consume(s: AsyncSession, user_id: int, limit: int, today: date | None = None) -> bool:
    day = today or date.today()
    row = await s.scalar(select(LLMUsage).where(LLMUsage.user_id == user_id, LLMUsage.day == day))
    if row is None:
        row = LLMUsage(user_id=user_id, day=day, count=0)
        s.add(row)
    if row.count >= limit:
        return False
    row.count += 1
    await s.commit()
    return True
