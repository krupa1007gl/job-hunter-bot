"""Нормализация ссылок для дедупликации (REQ-006) и определение площадки (REQ-013)."""

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_SOURCES = {
    "hh.ru": "hh",
    "career.habr.com": "habr",
    "kwork.ru": "kwork",
    "fl.ru": "fl",
}


def _host(netloc: str) -> str:
    host = netloc.lower().split("@")[-1].split(":")[0]
    return host[4:] if host.startswith("www.") else host


def normalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    query = sorted(
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not k.lower().startswith("utm_")
    )
    path = parts.path.rstrip("/") or ""
    return urlunsplit(("https", _host(parts.netloc), path, urlencode(query), ""))


def detect_source(url: str) -> str | None:
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    host = _host(parts.netloc)
    for domain, name in _SOURCES.items():
        if host == domain or (domain == "hh.ru" and host.endswith(".hh.ru")):
            return name
    return None
