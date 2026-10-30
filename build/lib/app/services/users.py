"""Пользователи: регистрация, согласие, профиль, фильтры, AI-ключ, удаление (REQ-001/002/003/007/014)."""

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.crypto import KeyCipher
from ..models import AIKey, Delivery, LLMUsage, User, utcnow

SEARCH_TYPES = {"internship", "job", "freelance"}
LEVELS = {"intern", "junior", "middle"}
WORK_FORMATS = {"remote", "office", "hybrid", "any"}
TERM_FIELDS = {"keywords", "stop_words"}
MAX_TERMS = 30


async def get_user(s: AsyncSession, tg_id: int) -> User | None:
    return await s.scalar(select(User).where(User.tg_id == tg_id))


async def register(s: AsyncSession, tg_id: int, username: str | None) -> User:
    user = await get_user(s, tg_id)
    if user is None:
        user = User(tg_id=tg_id, username=(username or "")[:64] or None, profile={}, filters={})
        s.add(user)
        await s.commit()
    return user


async def give_consent(s: AsyncSession, user: User) -> None:
    user.consent_at = utcnow()
    await s.commit()


def is_active(user: User | None) -> bool:
    return bool(user and user.consent_at)


def check_access(tg_id: int, allowlist: list[int]) -> bool:
    return not allowlist or tg_id in allowlist


def _clean_list(items, max_items: int, max_len: int = 100) -> list[str]:
    if not isinstance(items, list) or len(items) > max_items:
        raise ValueError("слишком много значений")
    out: list[str] = []
    for it in items:
        v = str(it).strip().lower()
        if not v or len(v) > max_len:
            raise ValueError("пустое или слишком длинное значение")
        if v not in out:
            out.append(v)
    return out


def validate_profile(p: dict) -> dict:
    types = p.get("search_types") or []
    if not types or not set(types) <= SEARCH_TYPES:
        raise ValueError("тип поиска: internship, job, freelance")
    level = p.get("level")
    if level not in LEVELS:
        raise ValueError("уровень: intern, junior, middle")
    fmt = p.get("work_format") or "any"
    if fmt not in WORK_FORMATS:
        raise ValueError("формат: remote, office, hybrid, any")
    about = p.get("about") or ""
    if len(about) > 1000:
        raise ValueError("«о себе» — не больше 1000 символов")
    min_salary = p.get("min_salary")
    if min_salary is not None and (not isinstance(min_salary, int) or min_salary < 0):
        raise ValueError("зарплата — целое неотрицательное число")
    city = (p.get("city") or "").strip()[:100] or None
    return {
        "search_types": list(dict.fromkeys(types)),
        "stack": _clean_list(p.get("stack") or [], 20),
        "level": level,
        "work_format": fmt,
        "city": city,
        "min_salary": min_salary,
        "about": about.strip(),
    }


def default_filters(profile: dict) -> dict:
    return {
        "keywords": list(profile["stack"]),
        "stop_words": [],
        "min_salary": profile["min_salary"],
        "work_format": profile["work_format"],
        "city": profile["city"],
        "threshold": 60,
    }


async def update_profile(s: AsyncSession, user: User, profile: dict) -> None:
    clean = validate_profile(profile)
    user.profile = clean
    if not user.filters:
        user.filters = default_filters(clean)
    user.is_onboarded = True
    await s.commit()


def _filters(user: User) -> dict:
    return dict(user.filters or {})


async def add_term(s: AsyncSession, user: User, field: str, term: str) -> None:
    if field not in TERM_FIELDS:
        raise ValueError("неизвестное поле")
    (value,) = _clean_list([term], 1)
    f = _filters(user)
    terms = list(f.get(field) or [])
    if value not in terms:
        if len(terms) >= MAX_TERMS:
            raise ValueError(f"не больше {MAX_TERMS} значений")
        terms.append(value)
    f[field] = terms
    user.filters = f
    await s.commit()


async def remove_term(s: AsyncSession, user: User, field: str, term: str) -> None:
    if field not in TERM_FIELDS:
        raise ValueError("неизвестное поле")
    f = _filters(user)
    f[field] = [t for t in f.get(field) or [] if t != term.strip().lower()]
    user.filters = f
    await s.commit()


async def set_threshold(s: AsyncSession, user: User, value: int) -> None:
    if not isinstance(value, int) or not 0 <= value <= 100:
        raise ValueError("порог — от 0 до 100")
    f = _filters(user)
    f["threshold"] = value
    user.filters = f
    await s.commit()


async def set_filter(s: AsyncSession, user: User, field: str, value) -> None:
    f = _filters(user)
    if field == "min_salary":
        if value is not None and (not isinstance(value, int) or value < 0):
            raise ValueError("зарплата — целое неотрицательное число")
    elif field == "work_format":
        if value not in WORK_FORMATS:
            raise ValueError("формат: remote, office, hybrid, any")
    elif field == "city":
        value = (value or "").strip()[:100] or None
    else:
        raise ValueError("неизвестное поле")
    f[field] = value
    user.filters = f
    await s.commit()


async def get_ai_key(s: AsyncSession, user: User) -> AIKey | None:
    return await s.scalar(select(AIKey).where(AIKey.user_id == user.id))


async def save_ai_key(
    s: AsyncSession, user: User, cipher: KeyCipher, provider: str, model: str, base_url: str, key: str
) -> AIKey:
    row = await get_ai_key(s, user)
    if row is None:
        row = AIKey(user_id=user.id)
        s.add(row)
    row.provider, row.model, row.base_url = provider, model, base_url
    row.key_encrypted = cipher.encrypt(key)
    row.key_last4 = key[-4:]
    await s.commit()
    return row


async def delete_ai_key(s: AsyncSession, user: User) -> None:
    await s.execute(delete(AIKey).where(AIKey.user_id == user.id))
    await s.commit()


async def delete_user(s: AsyncSession, user: User) -> None:
    for model in (AIKey, Delivery, LLMUsage):
        await s.execute(delete(model).where(model.user_id == user.id))
    await s.execute(delete(User).where(User.id == user.id))
    await s.commit()
