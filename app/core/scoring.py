"""Запасная оценка без AI (REQ-009): по совпадению ключевых слов, стека и уровня."""

from .types import OfferData

_LEVEL_WORDS = {
    "intern": ("стаж", "intern", "trainee", "без опыта"),
    "junior": ("junior", "джун", "младш", "начинающ"),
}


def keyword_score(offer: OfferData, profile: dict, filters: dict) -> int:
    title = offer.title.lower()
    body = offer.description.lower()
    terms = {t.strip().lower() for t in (filters.get("keywords") or []) + (profile.get("stack") or []) if t}
    if not terms:
        base = 40
    else:
        hits_title = sum(1 for t in terms if t in title)
        hits_body = sum(1 for t in terms if t in body and t not in title)
        base = min(70, hits_title * 25 + hits_body * 12)

    level = profile.get("level")
    words = _LEVEL_WORDS.get(level, ())
    if level == "intern":
        words = words + _LEVEL_WORDS["junior"]
    if any(w in title or w in body for w in words):
        base += 20
    if offer.remote and (profile.get("work_format") in ("remote", "any", None)):
        base += 5
    if base and (offer.salary_from or offer.salary_to):
        base += 5
    return max(0, min(100, base))
