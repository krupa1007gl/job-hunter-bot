"""Фильтры до оценки (REQ-004): отсекают предложение без вызова LLM."""

from .types import OfferData


def _text(offer: OfferData) -> str:
    return f"{offer.title}\n{offer.description}".lower()


def _norm_terms(terms) -> list[str]:
    return [t.strip().lower() for t in (terms or []) if t and t.strip()]


def passes_filters(offer: OfferData, filters: dict, stack: list[str] | None) -> tuple[bool, str]:
    text = _text(offer)

    for word in _norm_terms(filters.get("stop_words")):
        if word in text:
            return False, f"stop_word:{word}"

    min_salary = filters.get("min_salary")
    known = [s for s in (offer.salary_from, offer.salary_to) if s]
    if min_salary and known and max(known) < min_salary:
        return False, "salary"

    fmt = filters.get("work_format") or "any"
    if fmt == "remote" and offer.remote is False:
        return False, "format"

    city = (filters.get("city") or "").strip().lower()
    if city and fmt != "remote" and offer.remote is not True and offer.city and offer.city.lower() != city:
        return False, "city"

    terms = set(_norm_terms(filters.get("keywords"))) | set(_norm_terms(stack))
    if terms and not any(t in text for t in terms):
        return False, "no_keyword"

    return True, "ok"
