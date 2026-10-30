"""Сквозные сценарии бота: апдейты идут через настоящий Dispatcher, Telegram API подменён."""

import itertools
import json
from datetime import UTC, datetime

import httpx
import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.methods import AnswerCallbackQuery, DeleteMessage, EditMessageText, SendMessage, TelegramMethod
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from app.bot.handlers import router
from app.bot.middlewares import AppMiddleware
from app.config import Settings
from app.core.crypto import KeyCipher, generate_master_key
from app.db import init_db, make_engine, make_sessionmaker
from app.models import AIKey
from app.services import users

from .test_sources import PAGE


class FakeSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls: list[TelegramMethod] = []
        self._ids = itertools.count(1000)

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        if isinstance(method, SendMessage | EditMessageText):
            return Message(
                message_id=next(self._ids),
                date=datetime.now(UTC),
                chat=Chat(id=method.chat_id or 1, type="private"),
                text=method.text,
            ).as_(bot)
        if isinstance(method, DeleteMessage | AnswerCallbackQuery):
            return True
        return True

    async def close(self):
        pass

    async def stream_content(self, *a, **k):  # pragma: no cover
        yield b""

    def texts(self) -> list[str]:
        return [c.text for c in self.calls if isinstance(c, SendMessage | EditMessageText)]


class Harness:
    def __init__(self, sm, settings, http):
        self.session = FakeSession()
        self.bot = Bot("42:TEST", session=self.session, default=DefaultBotProperties(parse_mode="HTML"))
        self.dp = Dispatcher()
        mw = AppMiddleware(sm, settings, KeyCipher(settings.master_key.get_secret_value()), http)
        self.dp.message.outer_middleware(mw)
        self.dp.callback_query.outer_middleware(mw)
        router._parent_router = None  # глобальный router переиспользуется между тестами
        self.dp.include_router(router)
        self._uid = itertools.count(1)

    def _user(self, tg):
        return User(id=tg, is_bot=False, first_name="T", username=f"u{tg}")

    async def text(self, tg: int, text: str, mid: int = 1):
        msg = Message(
            message_id=mid,
            date=datetime.now(UTC),
            chat=Chat(id=tg, type="private"),
            from_user=self._user(tg),
            text=text,
        )
        await self.dp.feed_update(self.bot, Update(update_id=next(self._uid), message=msg))

    async def press(self, tg: int, data: str):
        msg = Message(message_id=500, date=datetime.now(UTC), chat=Chat(id=tg, type="private"), text="x")
        cb = CallbackQuery(id=str(next(self._uid)), from_user=self._user(tg), chat_instance="c", data=data, message=msg)
        await self.dp.feed_update(self.bot, Update(update_id=next(self._uid), callback_query=cb))

    def last(self) -> str:
        return self.session.texts()[-1]


@pytest.fixture
async def env(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "42:TEST")
    monkeypatch.setenv("MASTER_KEY", generate_master_key())
    monkeypatch.setenv("ALLOWLIST", "")
    settings = Settings()
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)
    sm = make_sessionmaker(engine)
    llm_replies = {"text": json.dumps({"score": 77, "reason": "подходит"})}

    def handler(request):
        if request.url.host == "llm.example.com":
            return httpx.Response(200, json={"choices": [{"message": {"content": llm_replies["text"]}}]})
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /secret\n")
        if request.url.path.startswith("/secret"):
            raise AssertionError("robots.txt нарушен")
        return httpx.Response(200, text=PAGE, headers={"content-type": "text/html"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        yield Harness(sm, settings, http), sm, settings, llm_replies
    await engine.dispose()


async def onboard(h, tg=1):
    await h.text(tg, "/start")
    await h.press(tg, "consent:yes")
    await h.text(tg, "стажировка, фриланс")
    await h.text(tg, "Python, FastAPI")
    await h.press(tg, "lvl:intern")
    await h.press(tg, "fmt:any")
    await h.text(tg, "Москва")
    await h.text(tg, "нет")
    await h.text(tg, "Студент, пишу на Python")


async def test_onboarding_flow(env):
    h, sm, *_ = env
    await h.text(1, "/start")
    assert "согласие" in h.last()
    await h.text(1, "https://hh.ru/vacancy/1")  # до согласия ничего не делаем
    assert "/start" in h.last()
    await onboard(h)
    assert "Профиль сохранён" in h.last()
    async with sm() as s:
        u = await users.get_user(s, 1)
        assert u.profile["stack"] == ["python", "fastapi"] and u.profile["search_types"] == ["internship", "freelance"]
    await h.text(1, "/profile")
    assert "python, fastapi" in h.last()


async def test_onboarding_rejects_bad_input(env):
    h, *_ = env
    await h.text(1, "/start")
    await h.press(1, "consent:yes")
    await h.text(1, "казино")
    assert "Не понял" in h.last()


async def test_filters_commands(env):
    h, sm, *_ = env
    await onboard(h)
    await h.text(1, "/stop add senior")
    await h.text(1, "/threshold 75")
    await h.text(1, "/threshold 500")
    assert "0 до 100" in h.last()
    await h.text(1, "/filters")
    assert "senior" in h.last() and "75" in h.last()


async def test_ai_key_flow_deletes_message_and_encrypts(env):
    h, sm, settings, _ = env
    await onboard(h)
    await h.text(1, "/ai")
    await h.press(1, "aip:custom")
    await h.text(1, "http://localhost/v1")
    assert "не подходит" in h.last()
    await h.text(1, "https://llm.example.com/v1")
    await h.text(1, "my-model")
    await h.text(1, "sk-very-secret-key-9876", mid=77)
    assert any(isinstance(c, DeleteMessage) and c.message_id == 77 for c in h.session.calls)
    assert "AI подключён" in h.last()
    assert all("very-secret" not in t for t in h.session.texts())
    async with sm() as s:
        u = await users.get_user(s, 1)
        k = await users.get_ai_key(s, u)
        assert isinstance(k, AIKey) and "very-secret" not in k.key_encrypted


async def test_manual_link_card_and_buttons(env):
    h, sm, settings, llm = env
    await onboard(h)
    await h.text(1, "посмотри https://career.habr.com/vacancies/42")
    card = h.last()
    assert "/100" in card and "оценка без AI" in card
    sends = [c for c in h.session.calls if isinstance(c, SendMessage) and c.reply_markup]
    offer_cb = sends[-1].reply_markup.inline_keyboard[0][1].callback_data  # «Черновик отклика»
    await h.press(1, offer_cb)
    assert any(isinstance(c, AnswerCallbackQuery) and c.text and "/ai" in c.text for c in h.session.calls)
    # чужой пользователь не может нажать кнопку с этим offer_id
    await onboard(h, tg=2)
    before = len(h.session.texts())
    await h.press(2, offer_cb.replace("draft", "more"))
    assert len(h.session.texts()) == before
    assert any(isinstance(c, AnswerCallbackQuery) and c.text == "Предложение не найдено" for c in h.session.calls)


async def test_robots_blocked_link_asks_for_text(env):
    h, *_ = env
    await onboard(h)
    await h.text(1, "https://kwork.ru/secret/1")
    assert "Пришли текст" in h.last()
    await h.text(1, "Нужен бот на Python для магазина. Бюджет 20000")
    assert "/100" in h.last()


async def test_draft_with_key_and_injection(env):
    h, sm, settings, llm = env
    await onboard(h)
    cipher = KeyCipher(settings.master_key.get_secret_value())
    async with sm() as s:
        u = await users.get_user(s, 1)
        await users.save_ai_key(s, u, cipher, "custom", "m", "https://llm.example.com/v1", "sk-key-12345678")
    await h.text(1, "https://career.habr.com/vacancies/42")
    assert "77/100" in h.last()
    llm["text"] = "<script>alert(1)</script> Здравствуйте!"
    await h.press(1, "o:draft:1")
    assert "Черновик отклика" in h.last() and "<script>" not in h.last() and "&lt;script&gt;" in h.last()


async def test_delete_me(env):
    h, sm, *_ = env
    await onboard(h)
    await h.text(1, "/delete_me")
    await h.press(1, "del:yes")
    assert "удалены" in h.last()
    async with sm() as s:
        assert await users.get_user(s, 1) is None


async def test_allowlist_blocks(env, monkeypatch):
    h, sm, settings, _ = env
    monkeypatch.setenv("ALLOWLIST", "999")
    h2 = Harness(sm, Settings(), None)
    await h2.text(1, "/start")
    assert "закрытой бете" in h2.last()


async def test_rate_limit(env, monkeypatch):
    h, sm, settings, _ = env
    monkeypatch.setenv("RATE_LIMIT_PER_MIN", "3")
    h2 = Harness(sm, Settings(), None)
    for _ in range(4):
        await h2.text(5, "/help")
    assert "Слишком часто" in h2.last()
