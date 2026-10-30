"""Регрессия по ревью G3 (REVIEW итерация 1): R-01…R-05, R-07, R-08."""

import socket
from dataclasses import replace

import httpx
import pytest

from app.bot import scheduler
from app.core.cards import escape_cut
from app.core.types import OfferData
from app.db import init_db, make_engine, make_sessionmaker
from app.llm import client as llm
from app.services import offers, users

from .test_app import _user, settings  # noqa: F401
from .test_bot_flow import env, onboard  # noqa: F401
from .test_sources import RSS


@pytest.fixture
async def sm():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)
    yield make_sessionmaker(engine)
    await engine.dispose()


# R-01: ручной текст одного пользователя не попадает к другим и не блокирует настоящую вакансию
async def test_manual_text_is_private(sm, profile, offer):
    async with sm() as s:
        a = await _user(s, 1, profile)
        b = await _user(s, 2, profile)
        fake = replace(offer, source="manual", title="ФИШИНГ", description="перейди на evil.example")
        mine = await offers.upsert_one(s, fake, private_for=a.id)
        await offers.evaluate(s, a, mine, llm=None, daily_limit=0, manual=True)
        (real,) = await offers.ingest(s, [offer])
        assert real.id != mine.id and real.title == offer.title
        same = await offers.upsert_one(s, offer)
        assert same.id == real.id
        assert await offers.get_offer_for_user(s, b, mine.id) is None


# R-02: имя хоста, которое резолвится во внутренний адрес, отклоняется до запроса
async def test_llm_rejects_host_resolving_to_private_ip(monkeypatch):
    def fake_getaddrinfo(host, *a, **k):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.7", 443))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    def boom(request):
        raise AssertionError("запрос во внутреннюю сеть")

    async with httpx.AsyncClient(transport=httpx.MockTransport(boom)) as http:
        with pytest.raises(llm.LLMError) as e:
            await llm.chat(http, "https://evil.example.com/v1", "m", "k", [])
    assert e.value.kind == "config"


# R-03: между запросами к источникам есть пауза
async def test_sources_are_throttled(sm, settings, profile, monkeypatch):  # noqa: F811
    async with sm() as s:
        await _user(s, 1, profile)
        await _user(s, 2, {**profile, "stack": ["golang"]})
    sleeps = []

    async def fake_sleep(sec):
        sleeps.append(sec)

    monkeypatch.setattr(scheduler.asyncio, "sleep", fake_sleep)

    async def send(*a):
        pass

    from app.core.crypto import KeyCipher

    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, text=RSS))) as http:
        await scheduler.run_cycle(sm, http, settings, KeyCipher(settings.master_key.get_secret_value()), send)
    assert sum(1 for x in sleeps if x >= 2) >= 1


# R-04: после ошибки провайдера LLM для пользователя отключается до конца цикла
async def test_llm_failure_disables_llm_for_rest_of_cycle(sm, settings, profile):  # noqa: F811
    from app.core.crypto import KeyCipher

    cipher = KeyCipher(settings.master_key.get_secret_value())
    async with sm() as s:
        u = await _user(s, 1, profile)
        await users.save_ai_key(s, u, cipher, "custom", "m", "https://llm.example.com/v1", "sk-key-00000001")
    llm_calls = []

    def handler(request):
        if request.url.host == "llm.example.com":
            llm_calls.append(1)
            return httpx.Response(503)
        return httpx.Response(200, text=RSS)

    async def send(*a):
        pass

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        await scheduler.run_cycle(sm, http, settings, cipher, send)
    assert len(llm_calls) == 1  # 2 новых вакансии, но после первой ошибки LLM не дёргаем


# R-07: экранирование до обрезки — длина после экранирования в пределах лимита и без битых сущностей
def test_escape_cut():
    s = escape_cut("&" * 5000, 3500)
    assert len(s) <= 3500 and s.endswith(("&amp;", "…"))
    assert "&am…" not in s and not s.endswith("&")
    assert escape_cut("<b>", 100) == "&lt;b&gt;"


# R-08: гонка upsert — повторная вставка того же URL не падает
async def test_upsert_same_url_twice(sm, offer):
    async with sm() as s1, sm() as s2:
        r1 = await offers.upsert_one(s1, offer)
        r2 = await offers.upsert_one(s2, replace(offer, url=offer.url + "/"))
        assert r1.id == r2.id


def test_offerdata_import_used():
    assert OfferData


# R-05: «/ai off» удаляет ключ (как в SPEC REQ-007.5)
async def test_ai_off_with_space(env):  # noqa: F811
    h, sm_, settings_, _ = env
    await onboard(h)
    from app.core.crypto import KeyCipher

    async with sm_() as s:
        u = await users.get_user(s, 1)
        cipher = KeyCipher(settings_.master_key.get_secret_value())
        await users.save_ai_key(s, u, cipher, "groq", "m", "https://api.groq.com/openai/v1", "gsk_abcdefgh1")
    await h.text(1, "/ai off")
    assert "Ключ удалён" in h.last()
    async with sm_() as s:
        assert await users.get_ai_key(s, await users.get_user(s, 1)) is None
