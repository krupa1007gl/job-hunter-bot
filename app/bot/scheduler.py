"""Цикл сбора: источники → дедуп → оценка для каждого пользователя → карточки."""

import asyncio
import logging
from collections.abc import Awaitable, Callable

import httpx
from sqlalchemy import select

from ..config import Settings
from ..core.cards import render_card
from ..core.crypto import KeyCipher
from ..models import User
from ..services import offers, users
from ..services.ai import make_llm
from ..sources import habr, hh
from ..sources.common import SourceError

log = logging.getLogger(__name__)

Send = Callable[[int, str, int], Awaitable[None]]


def build_queries(filters_list: list[dict], stacks: list[list[str]]) -> set[str]:
    queries: set[str] = set()
    for f, stack in zip(filters_list, stacks, strict=True):
        terms = (f.get("keywords") or []) or (stack or [])
        q = " ".join(sorted(terms[:3]))
        if q:
            queries.add(q)
    return queries


class _GuardedLLM:
    def __init__(self, inner):
        self.inner, self.failed = inner, False

    async def __call__(self, messages):
        try:
            return await self.inner(messages)
        except Exception:
            self.failed = True
            raise


def _guard(llm):
    return _GuardedLLM(llm) if llm is not None else None


async def _active_users(s) -> list[User]:
    rows = (await s.scalars(select(User).where(User.consent_at.is_not(None), User.is_onboarded.is_(True)))).all()
    return list(rows)


async def run_cycle(sm, http: httpx.AsyncClient, settings: Settings, cipher: KeyCipher, send: Send) -> dict:
    stats = {"queries": 0, "new_offers": 0, "sent": 0, "errors": 0}
    async with sm() as s:
        active = await _active_users(s)
        queries = build_queries(
            [u.filters or {} for u in active], [(u.profile or {}).get("stack") or [] for u in active]
        )
    stats["queries"] = len(queries)

    hh_client = hh.HHClient(http, settings.hh_token.get_secret_value(), settings.user_agent)
    collected = []
    for q in sorted(queries):
        for name, fetch in (
            ("habr", lambda q=q: habr.fetch(http, q, settings.user_agent)),
            ("hh", lambda q=q: hh_client.search(q)),
        ):
            try:
                collected += await fetch()
            except SourceError as e:
                stats["errors"] += 1
                log.warning("source %s failed: %s", name, e)
            if name == "habr" or hh_client.enabled:
                await asyncio.sleep(settings.source_delay)

    async with sm() as s:
        new = await offers.ingest(s, collected)
        stats["new_offers"] = len(new)
        for user in await _active_users(s):
            llm = _guard(make_llm(http, cipher, await users.get_ai_key(s, user)))
            for row in new:
                if llm is not None and llm.failed:  # R-04: провайдер упал — остаток цикла без LLM
                    llm = None
                d = await offers.evaluate(s, user, row, llm=llm, daily_limit=settings.llm_daily_limit)
                if d is None or d.status != "sent":
                    continue
                try:
                    await send(user.tg_id, render_card(offers.to_data(row), d.score, d.reason, d.ai_used), row.id)
                    stats["sent"] += 1
                except Exception as e:  # пользователь заблокировал бота и т. п. — не мешает остальным
                    log.warning("send to user %s failed: %s", user.id, type(e).__name__)
    return stats


async def scheduler_loop(sm, http, settings: Settings, cipher: KeyCipher, send: Send) -> None:
    cycles = 0
    while True:
        try:
            stats = await run_cycle(sm, http, settings, cipher, send)
            log.info("cycle done: %s", stats)
            cycles += 1
            if cycles % max(1, (24 * 60) // settings.poll_minutes) == 0:
                async with sm() as s:
                    log.info("cleanup removed %s offers", await offers.cleanup_old(s))
        except Exception:
            log.exception("cycle failed")
        await asyncio.sleep(settings.poll_minutes * 60)
