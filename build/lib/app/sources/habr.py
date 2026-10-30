"""Хабр Карьера: публичный RSS вакансий (REQ-005)."""

from datetime import UTC, datetime
from urllib.parse import urlencode

import feedparser
import httpx

from ..core.types import OfferData
from ..core.urls import normalize_url
from .common import SourceError, strip_html

__all__ = ["SourceError", "build_rss_url", "parse_rss", "fetch"]

RSS_BASE = "https://career.habr.com/vacancies/rss"


def build_rss_url(query: str) -> str:
    return f"{RSS_BASE}?{urlencode({'q': query, 'type': 'all'})}"


def parse_rss(xml: str) -> list[OfferData]:
    feed = feedparser.parse(xml)
    out: list[OfferData] = []
    for e in feed.entries:
        link = e.get("link")
        title = (e.get("title") or "").strip()
        if not link or not title:
            continue
        published = None
        if e.get("published_parsed"):
            published = datetime(*e.published_parsed[:6], tzinfo=UTC)
        out.append(
            OfferData(
                source="habr",
                external_id=e.get("id") or normalize_url(link),
                url=normalize_url(link),
                title=title,
                company=(e.get("author") or None),
                description=strip_html(e.get("summary") or e.get("description")),
                published_at=published,
            )
        )
    return out


async def fetch(http: httpx.AsyncClient, query: str, user_agent: str) -> list[OfferData]:
    try:
        r = await http.get(build_rss_url(query), headers={"User-Agent": user_agent}, timeout=30)
    except httpx.HTTPError as e:
        raise SourceError(f"habr: {type(e).__name__}") from None
    if r.status_code != 200:
        raise SourceError(f"habr: HTTP {r.status_code}")
    return parse_rss(r.text)
