import re
from html import unescape


class SourceError(Exception):
    """Источник временно недоступен или ответил ошибкой."""


_TAGS = re.compile(r"<[^>]+>")
_SPACES = re.compile(r"[ \t\r\f\v]+")
_BLOCKS = re.compile(r"<(script|style|noscript)[^>]*>.*?</\1>", re.S | re.I)


def strip_html(html: str | None) -> str:
    text = _BLOCKS.sub(" ", html or "")
    text = unescape(_TAGS.sub(" ", text))
    lines = [_SPACES.sub(" ", ln).strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln).strip()
