"""Карточка предложения для Telegram, parse_mode=HTML (REQ-010)."""

from html import escape

from .types import OfferData

SOURCE_LABELS = {"habr": "Хабр Карьера", "hh": "hh.ru", "kwork": "Kwork", "fl": "FL.ru", "manual": "Ссылка"}
_CURRENCY = {"RUR": "₽", "RUB": "₽", "USD": "$", "EUR": "€"}
LIMIT = 4096


def _num(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def format_salary(salary_from: int | None, salary_to: int | None, currency: str | None) -> str:
    cur = _CURRENCY.get((currency or "RUR").upper(), currency or "")
    if salary_from and salary_to:
        return f"{_num(salary_from)}–{_num(salary_to)} {cur}"
    if salary_from:
        return f"от {_num(salary_from)} {cur}"
    if salary_to:
        return f"до {_num(salary_to)} {cur}"
    return "не указана"


def escape_cut(text: str, n: int) -> str:
    """Экранирует и обрезает так, чтобы результат ≤ n и не рвал HTML-сущности."""
    out, size = [], 0
    for ch in text or "":
        e = escape(ch)
        if size + len(e) > n - 1:
            return "".join(out) + "…"
        out.append(e)
        size += len(e)
    return "".join(out)


def _cut(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


def render_card(offer: OfferData, score: int, reason: str, ai_used: bool) -> str:
    label = SOURCE_LABELS.get(offer.source, offer.source)
    place = []
    if offer.remote:
        place.append("удалённо")
    if offer.city:
        place.append(offer.city)
    head = f"<b>{escape(label)}</b> · <b>{score}/100</b>" + ("" if ai_used else " · <i>оценка без AI</i>")
    lines = [
        head,
        f"<b>{escape(_cut(offer.title, 300))}</b>"
        + (f" — {escape(_cut(offer.company, 150))}" if offer.company else ""),
        f"Зарплата: {format_salary(offer.salary_from, offer.salary_to, offer.currency)}"
        + (f" · {escape(' / '.join(place))}" if place else ""),
        f"Почему: {escape(_cut(reason or '', 400))}",
        escape(_cut(offer.url, 1000)),
    ]
    text = "\n".join(lines)
    return text[:LIMIT]
