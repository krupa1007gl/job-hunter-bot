"""T-002: модели и сервисы. Тесты написаны до кода."""

from dataclasses import replace
from datetime import date, timedelta

import pytest
from sqlalchemy import func, select

from app import models
from app.core.crypto import KeyCipher, generate_master_key
from app.db import init_db, make_engine, make_sessionmaker
from app.services import llm_usage, offers, users


@pytest.fixture
async def session():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)
    sm = make_sessionmaker(engine)
    async with sm() as s:
        yield s
    await engine.dispose()


@pytest.fixture
def cipher():
    return KeyCipher(generate_master_key())


async def _ready_user(session, profile, tg_id=1):
    u = await users.register(session, tg_id, f"user{tg_id}")
    await users.give_consent(session, u)
    await users.update_profile(session, u, profile)
    return u


# ---------- REQ-001: регистрация, согласие, allowlist ----------


async def test_register_without_consent_is_inactive(session):
    u = await users.register(session, 10, "gleb")
    assert u.consent_at is None and not users.is_active(u)
    again = await users.register(session, 10, "gleb")
    assert again.id == u.id
    await users.give_consent(session, u)
    assert users.is_active(await users.get_user(session, 10))


@pytest.mark.parametrize("allow,tg,ok", [([], 5, True), ([1, 2], 2, True), ([1, 2], 5, False)])
def test_allowlist(allow, tg, ok):
    assert users.check_access(tg, allow) is ok


# ---------- REQ-002/003: профиль и фильтры ----------


async def test_profile_sets_default_filters(session, profile):
    u = await _ready_user(session, profile)
    assert u.is_onboarded
    assert u.filters["keywords"] == ["python", "fastapi"]
    assert u.filters["threshold"] == 60
    assert u.filters["city"] == "Москва"


@pytest.mark.parametrize(
    "patch",
    [{"search_types": []}, {"search_types": ["casino"]}, {"level": "god"}, {"work_format": "moon"},
     {"about": "x" * 1001}, {"stack": ["a"] * 21}, {"min_salary": -5}],
)
async def test_profile_validation(session, profile, patch):
    u = await users.register(session, 1, "u")
    await users.give_consent(session, u)
    with pytest.raises(ValueError):
        await users.update_profile(session, u, {**profile, **patch})
    assert u.profile in (None, {})


async def test_filter_terms_and_threshold(session, profile):
    u = await _ready_user(session, profile)
    await users.add_term(session, u, "stop_words", "  Senior ")
    await users.add_term(session, u, "stop_words", "senior")
    assert u.filters["stop_words"].count("senior") == 1
    await users.remove_term(session, u, "keywords", "fastapi")
    assert "fastapi" not in u.filters["keywords"]
    await users.set_threshold(session, u, 75)
    assert u.filters["threshold"] == 75
    for bad in (-1, 101):
        with pytest.raises(ValueError):
            await users.set_threshold(session, u, bad)
    with pytest.raises(ValueError):
        await users.add_term(session, u, "profile", "x")
    with pytest.raises(ValueError):
        await users.add_term(session, u, "keywords", "x" * 101)


# ---------- REQ-007: ключ хранится зашифрованным ----------


async def test_ai_key_stored_encrypted(session, profile, cipher):
    u = await _ready_user(session, profile)
    k = await users.save_ai_key(session, u, cipher, "groq", "llama", "https://api.groq.com/openai/v1",
                                "gsk_supersecret_1234")
    assert "supersecret" not in k.key_encrypted and k.key_last4 == "1234"
    assert "supersecret" not in repr(k)
    assert cipher.decrypt(k.key_encrypted) == "gsk_supersecret_1234"
    url = "https://api.groq.com/openai/v1"
    k2 = await users.save_ai_key(session, u, cipher, "groq", "llama", url, "gsk_other_9999")
    assert k2.id == k.id  # один ключ на пользователя
    await users.delete_ai_key(session, u)
    assert await users.get_ai_key(session, u) is None


# ---------- REQ-006: дедупликация ----------


async def test_ingest_dedup(session, offer):
    dup = replace(offer, url=offer.url + "?utm_source=tg", external_id="other")
    new = await offers.ingest(session, [offer, dup])
    assert len(new) == 1
    assert await offers.ingest(session, [offer]) == []
    count = await session.scalar(select(func.count()).select_from(models.Offer))
    assert count == 1


# ---------- REQ-004/009/011: оценка ----------


async def test_evaluate_filtered_does_not_call_llm(session, profile, offer):
    u = await _ready_user(session, profile)
    await users.add_term(session, u, "stop_words", "стажёр")
    (row,) = await offers.ingest(session, [offer])
    called = []

    async def llm(msgs):
        called.append(msgs)
        return '{"score": 99, "reason": "x"}'

    d = await offers.evaluate(session, u, row, llm=llm, daily_limit=10)
    assert d.status == "filtered" and not called


async def test_evaluate_with_llm(session, profile, offer):
    u = await _ready_user(session, profile)
    (row,) = await offers.ingest(session, [offer])

    async def llm(msgs):
        return '{"score": 81, "reason": "подходит"}'

    d = await offers.evaluate(session, u, row, llm=llm, daily_limit=10)
    assert (d.status, d.score, d.reason, d.ai_used) == ("sent", 81, "подходит", True)
    assert await offers.evaluate(session, u, row, llm=llm, daily_limit=10) is None  # повторно не шлём


async def test_evaluate_bad_llm_retries_once_then_fallback(session, profile, offer):
    u = await _ready_user(session, profile)
    (row,) = await offers.ingest(session, [offer])
    calls = []

    async def llm(msgs):
        calls.append(1)
        return "не json"

    d = await offers.evaluate(session, u, row, llm=llm, daily_limit=10)
    assert len(calls) == 2 and d.ai_used is False and d.score is not None


async def test_evaluate_llm_exception_fallback(session, profile, offer):
    u = await _ready_user(session, profile)
    (row,) = await offers.ingest(session, [offer])

    async def llm(msgs):
        raise RuntimeError("down")

    d = await offers.evaluate(session, u, row, llm=llm, daily_limit=10)
    assert d.ai_used is False


async def test_below_threshold_skipped_manual_always_sent(session, profile, offer):
    u = await _ready_user(session, profile)
    await users.set_threshold(session, u, 95)
    o2 = replace(offer, url="https://career.habr.com/vacancies/999", external_id="999")
    r1, r2 = await offers.ingest(session, [offer, o2])

    async def llm(msgs):
        return '{"score": 50, "reason": "так себе"}'

    assert (await offers.evaluate(session, u, r1, llm=llm, daily_limit=10)).status == "skipped"
    await users.add_term(session, u, "stop_words", "python")
    d = await offers.evaluate(session, u, r2, llm=llm, daily_limit=10, manual=True)
    assert d.status == "sent" and d.score == 50


async def test_daily_limit(session, profile, offer):
    u = await _ready_user(session, profile)
    assert await llm_usage.try_consume(session, u.id, 2)
    assert await llm_usage.try_consume(session, u.id, 2)
    assert not await llm_usage.try_consume(session, u.id, 2)
    assert await llm_usage.try_consume(session, u.id, 2, today=date.today() + timedelta(days=1))
    (row,) = await offers.ingest(session, [offer])
    calls = []

    async def llm(msgs):
        calls.append(1)
        return '{"score": 90, "reason": "x"}'

    d = await offers.evaluate(session, u, row, llm=llm, daily_limit=2)
    assert not calls and d.ai_used is False


# ---------- REQ-014: изоляция и удаление ----------


async def test_isolation_between_users(session, profile, offer):
    a = await _ready_user(session, profile, tg_id=1)
    b = await _ready_user(session, profile, tg_id=2)
    (row,) = await offers.ingest(session, [offer])
    await offers.evaluate(session, a, row, llm=None, daily_limit=0, manual=True)
    assert await offers.get_offer_for_user(session, a, row.id) is not None
    assert await offers.get_offer_for_user(session, b, row.id) is None
    assert await offers.mark(session, b, row.id, "hidden") is False
    assert await offers.mark(session, a, row.id, "hidden") is True
    with pytest.raises(ValueError):
        await offers.mark(session, a, row.id, "sent")


async def test_delete_user_removes_everything(session, profile, offer, cipher):
    u = await _ready_user(session, profile, tg_id=7)
    await users.save_ai_key(session, u, cipher, "groq", "m", "https://api.groq.com/openai/v1", "gsk_abcdefgh")
    (row,) = await offers.ingest(session, [offer])
    await offers.evaluate(session, u, row, llm=None, daily_limit=0, manual=True)
    await llm_usage.try_consume(session, u.id, 5)
    uid = u.id
    await users.delete_user(session, u)
    assert await users.get_user(session, 7) is None
    for model in (models.AIKey, models.Delivery, models.LLMUsage):
        n = await session.scalar(select(func.count()).select_from(model).where(model.user_id == uid))
        assert n == 0


# ---------- хранение 90 дней ----------


async def test_cleanup_keeps_favorites(session, profile, offer):
    u = await _ready_user(session, profile)
    o2 = replace(offer, url="https://career.habr.com/vacancies/2", external_id="2")
    r1, r2 = await offers.ingest(session, [offer, o2])
    await offers.evaluate(session, u, r1, llm=None, daily_limit=0, manual=True)
    await offers.mark(session, u, r1.id, "favorite")
    removed = await offers.cleanup_old(session, days=0)
    assert removed == 1
    assert await offers.get_offer_for_user(session, u, r1.id) is not None
