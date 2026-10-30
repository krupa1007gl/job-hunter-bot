"""T-004: бот (разбор ввода, кнопки), планировщик, служебный API. Тесты до кода."""

import json
import re
from pathlib import Path

import httpx
import pytest

from app.api import create_app
from app.bot import keyboards, parsing
from app.bot.scheduler import build_queries, run_cycle
from app.config import Settings
from app.core.crypto import KeyCipher, generate_master_key
from app.db import init_db, make_engine, make_sessionmaker
from app.services import users

from .test_sources import RSS

# ---------- разбор ввода ----------


def test_parse_list():
    assert parsing.parse_list("Python, FastAPI;  SQL\nDocker, python") == ["python", "fastapi", "sql", "docker"]
    assert parsing.parse_list("  ") == []


@pytest.mark.parametrize("text,val", [("100 000", 100000), ("80000", 80000), ("нет", None), ("-", None),
                                      ("0", None)])
def test_parse_salary(text, val):
    assert parsing.parse_salary(text) == val


@pytest.mark.parametrize("text", ["abc", "-5", "1e9", "99999999999"])
def test_parse_salary_bad(text):
    with pytest.raises(ValueError):
        parsing.parse_salary(text)


@pytest.mark.parametrize("text,val", [("70", 70), (" 0 ", 0), ("100", 100)])
def test_parse_threshold(text, val):
    assert parsing.parse_threshold(text) == val


@pytest.mark.parametrize("text", ["101", "-1", "abc", ""])
def test_parse_threshold_bad(text):
    with pytest.raises(ValueError):
        parsing.parse_threshold(text)


def test_extract_url():
    assert parsing.extract_url("глянь https://hh.ru/vacancy/1 плиз") == "https://hh.ru/vacancy/1"
    assert parsing.extract_url("без ссылки") is None


# ---------- кнопки карточки (REQ-010) ----------


def test_card_keyboard():
    kb = keyboards.card_kb(42)
    buttons = [b for row in kb.inline_keyboard for b in row]
    assert len(buttons) == 4
    assert {keyboards.parse_cb(b.callback_data)[0] for b in buttons} == {"more", "draft", "hide", "fav"}
    assert all(len(b.callback_data.encode()) <= 64 for b in buttons)


@pytest.mark.parametrize("data", ["o:draft:abc", "o:hack:1", "x:draft:1", "o:draft:-1", "o:draft", "", "o:draft:1:2"])
def test_parse_cb_rejects(data):
    assert keyboards.parse_cb(data) is None


def test_parse_cb_ok():
    assert keyboards.parse_cb("o:fav:17") == ("fav", 17)


# ---------- настройки ----------


def test_settings_allowlist(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "1:x")
    monkeypatch.setenv("MASTER_KEY", generate_master_key())
    monkeypatch.setenv("ALLOWLIST", "1, 2,3")
    assert Settings().allowlist_ids == [1, 2, 3]
    monkeypatch.setenv("ALLOWLIST", "")
    assert Settings().allowlist_ids == []


# ---------- служебный API (REQ-016) ----------


@pytest.fixture
async def sm():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)
    yield make_sessionmaker(engine)
    await engine.dispose()


def _api(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


async def test_health_and_internal(sm):
    async with _api(create_app(sm, internal_token="s3cret")) as client:
        r = await client.get("/health")
        assert r.status_code == 200 and r.json() == {"status": "ok"}
        assert (await client.get("/internal/stats")).status_code == 401
        assert (await client.get("/internal/stats", headers={"X-Internal-Token": "wrong"})).status_code == 401
        r = await client.get("/internal/stats", headers={"X-Internal-Token": "s3cret"})
        assert r.status_code == 200 and "users" in r.json()


async def test_internal_disabled_without_token(sm):
    async with _api(create_app(sm, internal_token="")) as client:
        assert (await client.get("/internal/stats", headers={"X-Internal-Token": ""})).status_code == 401


async def test_health_503_when_db_down():
    class Broken:
        def __call__(self):
            raise RuntimeError("db down")

    async with _api(create_app(Broken(), internal_token="x")) as client:
        assert (await client.get("/health")).status_code == 503


# ---------- планировщик (REQ-005/006/009) ----------


def test_build_queries():
    u1 = {"keywords": ["python", "fastapi"]}
    u2 = {"keywords": ["fastapi", "python"]}
    u3 = {"keywords": []}
    qs = build_queries([u1, u2, u3], stacks=[[], [], ["go"]])
    assert qs == {"fastapi python", "go"}


async def _user(s, tg, profile, consent=True):
    u = await users.register(s, tg, f"u{tg}")
    if consent:
        await users.give_consent(s, u)
        await users.update_profile(s, u, profile)
    return u


@pytest.fixture
def settings(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "1:x")
    monkeypatch.setenv("MASTER_KEY", generate_master_key())
    monkeypatch.setenv("HH_TOKEN", "")
    return Settings()


async def test_cycle_sends_cards_once(sm, settings, profile):
    async with sm() as s:
        await _user(s, 1, profile)
        await _user(s, 2, {**profile, "stack": ["golang"]})
        await _user(s, 3, profile, consent=False)
    sent = []

    async def send(tg_id, text, offer_id):
        sent.append((tg_id, offer_id, text))

    def handler(request):
        assert request.url.host == "career.habr.com"
        return httpx.Response(200, text=RSS)

    cipher = KeyCipher(settings.master_key.get_secret_value())
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        stats = await run_cycle(sm, http, settings, cipher, send)
        assert {tg for tg, _, _ in sent} == {1}
        assert all("/100" in t for _, _, t in sent)
        assert stats["new_offers"] == 2 and stats["errors"] == 0
        sent.clear()
        stats = await run_cycle(sm, http, settings, cipher, send)
        assert sent == [] and stats["new_offers"] == 0


async def test_cycle_source_error_is_isolated(sm, settings, profile):
    async with sm() as s:
        await _user(s, 1, profile)

    async def send(*a):
        raise AssertionError("нечего слать")

    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500))) as http:
        stats = await run_cycle(sm, http, settings, KeyCipher(settings.master_key.get_secret_value()), send)
    assert stats["errors"] >= 1


async def test_cycle_uses_user_llm_key(sm, settings, profile):
    cipher = KeyCipher(settings.master_key.get_secret_value())
    async with sm() as s:
        u = await _user(s, 1, profile)
        await users.save_ai_key(s, u, cipher, "custom", "m", "https://llm.example.com/v1", "sk-user-key-0001")
    seen = []

    def handler(request):
        if request.url.host == "llm.example.com":
            seen.append(request.headers["authorization"])
            return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(
                {"score": 88, "reason": "отлично подходит"})}}]})
        return httpx.Response(200, text=RSS)

    sent = []

    async def send(tg_id, text, offer_id):
        sent.append(text)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        await run_cycle(sm, http, settings, cipher, send)
    assert seen and seen[0] == "Bearer sk-user-key-0001"
    assert any("88/100" in t for t in sent)


async def test_cycle_send_failure_does_not_stop(sm, settings, profile):
    async with sm() as s:
        await _user(s, 1, profile)
        await _user(s, 2, profile)
    got = []

    async def send(tg_id, text, offer_id):
        if tg_id == 1:
            raise RuntimeError("bot blocked by user")
        got.append(tg_id)

    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, text=RSS))) as http:
        await run_cycle(sm, http, settings, KeyCipher(settings.master_key.get_secret_value()), send)
    assert got and set(got) == {2}


# ---------- REQ-011 п. 3: бот ничего не отправляет на площадки ----------


def test_no_apply_methods_in_code():
    code = "\n".join(p.read_text(encoding="utf-8") for p in Path("app").rglob("*.py"))
    assert not re.search(r"negotiations|/apply|send_application|respond_to_vacancy", code)
    assert ".post(" not in Path("app/sources/hh.py").read_text(encoding="utf-8")
