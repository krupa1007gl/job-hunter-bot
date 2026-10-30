"""Чтение одной страницы по ссылке пользователя (REQ-013) с соблюдением robots.txt."""

import re
from html import unescape
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from ..core.types import OfferData
from ..core.urls import detect_source, normalize_url
from .common import strip_html


class PageError(Exception):
    """Страницу прочитать не удалось — просим пользователя прислать текст."""


class PageBlocked(PageError):
    """robots.txt площадки запрещает автоматическое чтение этой страницы."""


_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)
_META = re.compile(r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']*)["\']', re.I)
_BODY = re.compile(r"<body[^>]*>(.*)</body>", re.S | re.I)
MAX_BYTES = 2_000_000


def parse_html(html: str, url: str, source: str) -> OfferData:
    title_m = _TITLE.search(html)
    title = strip_html(unescape(title_m.group(1))) if title_m else ""
    meta_m = _META.search(html)
    meta = unescape(meta_m.group(1)).strip() if meta_m else ""
    body_m = _BODY.search(html)
    body = strip_html(body_m.group(1) if body_m else html)
    if not title and not body:
        raise PageError("пустая страница")
    desc = (meta + "\n" + body).strip()[:8000]
    return OfferData(source=source, external_id=url, url=url, title=title[:500] or url, description=desc)


async def _robots_allows(http: httpx.AsyncClient, url: str, user_agent: str) -> bool:
    p = urlsplit(url)
    try:
        r = await http.get(f"https://{p.netloc}/robots.txt", headers={"User-Agent": user_agent}, timeout=15)
    except httpx.HTTPError:
        return False
    if r.status_code >= 500:
        return False  # не знаем правил — не читаем
    if r.status_code >= 400:
        return True  # robots.txt нет — ограничений нет
    rp = RobotFileParser()
    rp.parse(r.text.splitlines())
    return rp.can_fetch(user_agent, url)


async def fetch_page(http: httpx.AsyncClient, url: str, user_agent: str) -> OfferData:
    source = detect_source(url)
    if source is None:
        raise PageError("поддерживаются ссылки hh.ru, Хабр Карьеры, Kwork и FL.ru")
    norm = normalize_url(url)
    if not await _robots_allows(http, norm, user_agent):
        raise PageBlocked("площадка запрещает автоматическое чтение этой страницы")
    try:
        r = await http.get(norm, headers={"User-Agent": user_agent}, timeout=20, follow_redirects=False)
    except httpx.HTTPError:
        raise PageError("страница не открылась") from None
    if r.status_code != 200 or "html" not in r.headers.get("content-type", ""):
        raise PageError(f"страница ответила HTTP {r.status_code}")
    return parse_html(r.text[:MAX_BYTES], norm, source)
