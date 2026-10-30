"""Предложения: приём с дедупликацией, оценка, отметки, очистка (REQ-004/006/009/014)."""

import logging
from collections.abc import Awaitable, Callable
from datetime import timedelta

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..core import filtering, llm_parse, prompts, scoring
from ..core.types import OfferData
from ..core.urls import normalize_url
from ..models import Delivery, Offer, User, utcnow
from . import llm_usage

log = logging.getLogger(__name__)

LLMCall = Callable[[list[dict]], Awaitable[str]]
USER_STATUSES = {"hidden", "favorite"}


def to_data(o: Offer) -> OfferData:
    return OfferData(
        source=o.source,
        external_id=o.external_id,
        url=o.url,
        title=o.title,
        company=o.company,
        description=o.description or "",
        salary_from=o.salary_from,
        salary_to=o.salary_to,
        currency=o.currency,
        city=o.city,
        remote=o.remote,
        published_at=o.published_at,
    )


def private_key(user_id: int, url: str) -> str:
    """Ручной текст пользователя хранится под личным ключом и не смешивается с общими вакансиями (R-01)."""
    return f"manual:{user_id}:{normalize_url(url)}"


def _new_row(it: OfferData, norm: str) -> Offer:
    return Offer(
        source=it.source,
        external_id=it.external_id[:300],
        url_norm=norm,
        url=normalize_url(it.url),
        title=it.title[:500],
        company=(it.company or None) and it.company[:300],
        description=(it.description or "")[:8000],
        salary_from=it.salary_from,
        salary_to=it.salary_to,
        currency=it.currency,
        city=it.city,
        remote=it.remote,
        published_at=it.published_at,
    )


async def ingest(s: AsyncSession, items: list[OfferData], private_for: int | None = None) -> list[Offer]:
    """Сохраняет новые предложения, возвращает только новые."""
    new: list[Offer] = []
    seen: set[str] = set()
    for it in items:
        norm = private_key(private_for, it.url) if private_for is not None else normalize_url(it.url)
        if norm in seen:
            continue
        seen.add(norm)
        if await s.scalar(select(Offer.id).where(Offer.url_norm == norm)):
            continue
        row = Offer(
            source=it.source,
            external_id=it.external_id[:300],
            url_norm=norm,
            url=normalize_url(it.url),
            title=it.title[:500],
            company=(it.company or None) and it.company[:300],
            description=(it.description or "")[:8000],
            salary_from=it.salary_from,
            salary_to=it.salary_to,
            currency=it.currency,
            city=it.city,
            remote=it.remote,
            published_at=it.published_at,
        )
        s.add(row)
        new.append(row)
    await s.commit()
    return new


async def upsert_one(s: AsyncSession, item: OfferData, private_for: int | None = None) -> Offer:
    norm = private_key(private_for, item.url) if private_for is not None else normalize_url(item.url)
    existing = await s.scalar(select(Offer).where(Offer.url_norm == norm))
    if existing:
        if private_for is not None:  # пользователь прислал текст заново — обновляем его личную копию
            existing.title, existing.description = item.title[:500], (item.description or "")[:8000]
            await s.commit()
        return existing
    row = _new_row(item, norm)
    try:
        async with s.begin_nested():  # savepoint: конфликт не откатывает и не «протухает» всю сессию (R-08)
            s.add(row)
    except IntegrityError:
        return await s.scalar(select(Offer).where(Offer.url_norm == norm))
    await s.commit()
    return row


async def _llm_score(s, user: User, data: OfferData, llm: LLMCall, daily_limit: int):
    msgs = prompts.build_score_messages(data, user.profile or {})
    for _ in range(2):
        if not await llm_usage.try_consume(s, user.id, daily_limit):
            return None
        try:
            parsed = llm_parse.parse_score_json(await llm(msgs))
        except Exception as e:  # провайдер недоступен — тихо падаем на запасную оценку
            log.warning("llm score failed: %s", type(e).__name__)
            return None
        if parsed:
            return parsed
    return None


async def evaluate(
    s: AsyncSession, user: User, offer: Offer, *, llm: LLMCall | None, daily_limit: int, manual: bool = False
) -> Delivery | None:
    """Оценивает предложение для пользователя. None — уже оценивалось раньше."""
    if await s.scalar(select(Delivery.id).where(Delivery.user_id == user.id, Delivery.offer_id == offer.id)):
        return None
    data = to_data(offer)
    filters = user.filters or {}
    d = Delivery(user_id=user.id, offer_id=offer.id)
    if not manual:
        ok, reason = filtering.passes_filters(data, filters, (user.profile or {}).get("stack"))
        if not ok:
            log.info("offer %s filtered for user %s: %s", offer.id, user.id, reason)
            d.status, d.reason = "filtered", reason
            s.add(d)
            await s.commit()
            return d
    result = await _llm_score(s, user, data, llm, daily_limit) if llm else None
    if result:
        d.score, d.reason, d.ai_used = result[0], result[1], True
    else:
        d.score = scoring.keyword_score(data, user.profile or {}, filters)
        d.reason, d.ai_used = "совпадение по ключевым словам и стеку", False
    d.status = "sent" if manual or d.score >= int(filters.get("threshold", 60)) else "skipped"
    s.add(d)
    await s.commit()
    return d


async def get_offer_for_user(s: AsyncSession, user: User, offer_id: int) -> Offer | None:
    return await s.scalar(
        select(Offer)
        .join(Delivery, Delivery.offer_id == Offer.id)
        .where(Offer.id == offer_id, Delivery.user_id == user.id)
    )


async def mark(s: AsyncSession, user: User, offer_id: int, status: str) -> bool:
    if status not in USER_STATUSES:
        raise ValueError("неизвестный статус")
    d = await s.scalar(select(Delivery).where(Delivery.user_id == user.id, Delivery.offer_id == offer_id))
    if d is None:
        return False
    d.status = status
    await s.commit()
    return True


async def cleanup_old(s: AsyncSession, days: int = 90) -> int:
    cutoff = utcnow() - timedelta(days=days)
    favorites = select(Delivery.offer_id).where(Delivery.status == "favorite")
    old = (await s.scalars(select(Offer.id).where(Offer.fetched_at <= cutoff, Offer.id.not_in(favorites)))).all()
    if old:
        await s.execute(delete(Delivery).where(Delivery.offer_id.in_(old)))
        await s.execute(delete(Offer).where(Offer.id.in_(old)))
    await s.commit()
    return len(old)
