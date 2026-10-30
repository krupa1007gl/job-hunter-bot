"""T-003: источники и LLM-клиент. Без сети: httpx.MockTransport."""

import json

import httpx
import pytest

from app.llm import client as llm
from app.sources import habr, hh, page

RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Хабр Карьера</title>
<item>
  <title>Python-разработчик (стажёр)</title>
  <link>https://career.habr.com/vacancies/1000123456?utm_source=rss</link>
  <guid>https://career.habr.com/vacancies/1000123456</guid>
  <author>Пример</author>
  <description><![CDATA[<p>Python, <b>FastAPI</b>. Можно удалённо.</p>]]></description>
  <pubDate>Wed, 28 Oct 2026 10:00:00 +0300</pubDate>
</item>
<item>
  <title>Backend Junior</title>
  <link>https://career.habr.com/vacancies/1000654321</link>
  <description>Go и Python</description>
</item>
</channel></rss>"""

HH_JSON = {
    "items": [
        {
            "id": "777",
            "name": "Стажёр Python",
            "alternate_url": "https://hh.ru/vacancy/777",
            "employer": {"name": "Компания"},
            "salary": {"from": 50000, "to": None, "currency": "RUR"},
            "area": {"name": "Москва"},
            "schedule": {"id": "remote"},
            "snippet": {"requirement": "Знание <highlighttext>Python</highlighttext>", "responsibility": "Писать API"},
            "published_at": "2026-10-28T10:00:00+0300",
        }
    ]
}

PAGE = """<html><head><title>Вакансия Python-разработчик — Хабр Карьера</title>
<meta name="description" content="Ищем Python-разработчика &amp; стажёра">
<script>alert(1)</script></head><body><h1>Python-разработчик</h1><p>Описание вакансии</p></body></html>"""


def mock_client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# ---------- REQ-005: RSS Хабр Карьеры ----------


def test_rss_url_encodes_query():
    url = habr.build_rss_url("python junior & co")
    assert url.startswith("https://career.habr.com/vacancies/rss?")
    assert "q=python+junior+%26+co" in url and "type=all" in url


def test_parse_rss():
    items = habr.parse_rss(RSS)
    assert len(items) == 2
    first = items[0]
    assert first.source == "habr" and first.title == "Python-разработчик (стажёр)"
    assert first.url == "https://career.habr.com/vacancies/1000123456"
    assert "<" not in first.description and "FastAPI" in first.description
    assert first.company == "Пример" and first.published_at is not None
    assert items[1].external_id == "https://career.habr.com/vacancies/1000654321"


async def test_habr_fetch_sends_user_agent_and_handles_errors():
    seen = {}

    def ok(request):
        seen["ua"] = request.headers.get("user-agent")
        return httpx.Response(200, text=RSS)

    async with mock_client(ok) as http:
        items = await habr.fetch(http, "python", user_agent="job-hunter-bot (+https://github.com/x)")
    assert len(items) == 2 and "job-hunter-bot" in seen["ua"]

    async with mock_client(lambda r: httpx.Response(503)) as http:
        with pytest.raises(habr.SourceError):
            await habr.fetch(http, "python", user_agent="ua")


def test_parse_rss_garbage():
    assert habr.parse_rss("<html>не rss</html>") == []


# ---------- REQ-018: hh.ru только по токену ----------


async def test_hh_without_token_makes_no_requests():
    def boom(request):
        raise AssertionError("запрос без токена")

    async with mock_client(boom) as http:
        c = hh.HHClient(http, token="", user_agent="ua")
        assert not c.enabled
        assert await c.search("python") == []


async def test_hh_search_with_token():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("authorization")
        seen["ua"] = request.headers.get("hh-user-agent")
        seen["url"] = str(request.url)
        return httpx.Response(200, json=HH_JSON)

    async with mock_client(handler) as http:
        items = await hh.HHClient(http, token="T0K", user_agent="job-hunter-bot/1.0 (x@y.z)").search("python")
    assert seen["auth"] == "Bearer T0K" and seen["ua"].startswith("job-hunter-bot")
    assert seen["url"].startswith("https://api.hh.ru/vacancies")
    (v,) = items
    assert (v.source, v.external_id, v.url, v.salary_from, v.remote, v.city) == (
        "hh", "777", "https://hh.ru/vacancy/777", 50000, True, "Москва")
    assert "highlighttext" not in v.description and "Python" in v.description


async def test_hh_auth_error():
    async with mock_client(lambda r: httpx.Response(403, json={"errors": []})) as http:
        with pytest.raises(hh.SourceError):
            await hh.HHClient(http, token="bad", user_agent="ua").search("python")


# ---------- REQ-013: страница по ссылке пользователя ----------


def _site(robots: str, html: str = PAGE, status: int = 200):
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=robots)
        return httpx.Response(status, text=html, headers={"content-type": "text/html; charset=utf-8"})

    return handler, calls


async def test_page_fetch_ok():
    handler, calls = _site("User-agent: *\nDisallow: /admin\n")
    async with mock_client(handler) as http:
        o = await page.fetch_page(http, "https://career.habr.com/vacancies/42?utm_source=x", user_agent="ua")
    assert o.source == "habr" and "Python-разработчик" in o.title
    assert "alert" not in o.description and "Описание вакансии" in o.description
    assert o.url == "https://career.habr.com/vacancies/42"
    assert calls[0] == "/robots.txt"


async def test_page_respects_robots():
    handler, calls = _site("User-agent: *\nDisallow: /vacancies\n")
    async with mock_client(handler) as http:
        with pytest.raises(page.PageBlocked):
            await page.fetch_page(http, "https://career.habr.com/vacancies/42", user_agent="ua")
    assert "/vacancies/42" not in calls


async def test_page_rejects_unknown_site():
    def boom(request):
        raise AssertionError("не должно быть запросов")

    async with mock_client(boom) as http:
        with pytest.raises(page.PageError):
            await page.fetch_page(http, "https://evil.example.com/x", user_agent="ua")


async def test_page_http_error():
    handler, _ = _site("", status=404)
    async with mock_client(handler) as http:
        with pytest.raises(page.PageError):
            await page.fetch_page(http, "https://kwork.ru/projects/1", user_agent="ua")


# ---------- REQ-007/011: LLM-клиент ----------


async def test_llm_chat_ok():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "привет"}}]})

    async with mock_client(handler) as http:
        msgs = [{"role": "user", "content": "x"}]
        out = await llm.chat(http, "https://api.groq.com/openai/v1", "m1", "gsk_KEY", msgs)
    assert out == "привет"
    assert seen["url"] == "https://api.groq.com/openai/v1/chat/completions"
    assert seen["auth"] == "Bearer gsk_KEY" and seen["body"]["model"] == "m1"


@pytest.mark.parametrize(
    "status,kind", [(401, "auth"), (403, "auth"), (429, "rate_limit"), (500, "unavailable"), (404, "unavailable")]
)
async def test_llm_errors_classified_and_key_not_leaked(status, kind):
    async with mock_client(lambda r: httpx.Response(status, text="error for key gsk_SECRETKEY")) as http:
        with pytest.raises(llm.LLMError) as e:
            await llm.chat(http, "https://api.groq.com/openai/v1", "m", "gsk_SECRETKEY", [])
    assert e.value.kind == kind
    assert "SECRETKEY" not in str(e.value) and "SECRETKEY" not in repr(e.value)


async def test_llm_network_error_and_bad_body():
    def down(request):
        raise httpx.ConnectError("refused")

    async with mock_client(down) as http:
        with pytest.raises(llm.LLMError) as e:
            await llm.chat(http, "https://x.example.com/v1", "m", "k", [])
    assert e.value.kind == "unavailable"
    async with mock_client(lambda r: httpx.Response(200, json={"nope": 1})) as http:
        with pytest.raises(llm.LLMError) as e:
            await llm.chat(http, "https://x.example.com/v1", "m", "k", [])
    assert e.value.kind == "bad_response"


async def test_llm_rejects_unsafe_base_url():
    def boom(request):
        raise AssertionError("запрос во внутреннюю сеть")

    async with mock_client(boom) as http:
        with pytest.raises(llm.LLMError) as e:
            await llm.chat(http, "https://169.254.169.254/v1", "m", "k", [])
    assert e.value.kind == "config"
