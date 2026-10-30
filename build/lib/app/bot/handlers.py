"""Диалоги бота. Логики здесь минимум — всё через services (тот же слой возьмёт MCP-коннектор)."""

import logging
from html import escape

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from ..core import prompts
from ..core.cards import render_card
from ..core.crypto import mask_key
from ..core.types import OfferData
from ..core.urls import detect_source, normalize_url
from ..llm import client as llm_client
from ..llm.providers import PRESETS, validate_base_url
from ..services import offers, users
from ..services.ai import make_llm
from ..sources import page
from . import keyboards as kb
from .parsing import extract_url, parse_list, parse_salary, parse_threshold

log = logging.getLogger(__name__)
router = Router()

TYPES = {"стажировка": "internship", "работа": "job", "фриланс": "freelance"}
LEVELS = [("intern", "Стажёр"), ("junior", "Junior"), ("middle", "Middle")]
FORMATS = [("remote", "Удалённо"), ("office", "Офис"), ("hybrid", "Гибрид"), ("any", "Не важно")]

WELCOME = (
    "Привет! Я «Охотник за вакансиями»: собираю стажировки, вакансии и фриланс-заказы, "
    "отбираю подходящие и помогаю написать отклик. Отклики я никуда не отправляю — только готовлю черновик.\n\n"
    "Чтобы начать, нужно согласие на обработку данных: Telegram ID, профиль и фильтры. "
    "Профиль и тексты вакансий передаются AI-провайдеру, ключ которого ты подключишь сам. "
    "Подробнее — /privacy. Удалить всё можно командой /delete_me."
)
PRIVACY = (
    "<b>Что храним:</b> Telegram ID и username, профиль, фильтры, отметки по вакансиям, "
    "AI-ключ (только в зашифрованном виде).\n"
    "<b>Кому передаём:</b> профиль и текст вакансии — выбранному тобой AI-провайдеру для оценки и черновика.\n"
    "<b>Не пиши в профиль</b> паспортные данные, телефон и адрес.\n"
    "<b>Удаление:</b> /delete_me удаляет все твои данные."
)
HELP = (
    "/profile — профиль, /profile_edit — заполнить заново\n"
    "/filters — фильтры\n"
    "/kw add|del слово — ключевые слова\n"
    "/stop add|del слово — слова-исключения\n"
    "/salary 80000 | нет — минимальная зарплата\n"
    "/format remote|office|hybrid|any\n"
    "/city Москва | нет\n"
    "/threshold 60 — порог балла (0–100)\n"
    "/ai — подключить свой AI-ключ, /ai_off — отключить\n"
    "/privacy — данные, /delete_me — удалить аккаунт\n\n"
    "Пришли ссылку на вакансию hh.ru, Хабр Карьеры, Kwork или FL.ru — оценю её и помогу с откликом."
)


class Onboarding(StatesGroup):
    types = State()
    stack = State()
    level = State()
    work_format = State()
    city = State()
    salary = State()
    about = State()


class AISetup(StatesGroup):
    base_url = State()
    model = State()
    key = State()


class Manual(StatesGroup):
    text = State()


def _need_user(user) -> bool:
    return users.is_active(user)


# ---------- регистрация ----------


@router.message(CommandStart())
async def start(message: Message, session, user, state: FSMContext):
    await state.clear()
    if user is None:
        user = await users.register(session, message.from_user.id, message.from_user.username)
    if not users.is_active(user):
        await message.answer(WELCOME, reply_markup=kb.consent_kb())
        return
    if not user.is_onboarded:
        await _ask_types(message, state)
        return
    await message.answer("С возвращением! Я пришлю новые предложения, как только найду.\n\n" + HELP)


@router.callback_query(F.data == "consent:yes")
async def consent(cb: CallbackQuery, session, user, state: FSMContext):
    if user is None:
        user = await users.register(session, cb.from_user.id, cb.from_user.username)
    await users.give_consent(session, user)
    await cb.answer("Спасибо!")
    await _ask_types(cb.message, state)


@router.message(Command("help"))
async def help_cmd(message: Message):
    await message.answer(HELP)


@router.message(Command("privacy"))
async def privacy(message: Message):
    await message.answer(PRIVACY)


# ---------- онбординг (REQ-002) ----------


async def _ask_types(message: Message, state: FSMContext):
    await state.set_state(Onboarding.types)
    await message.answer("Что ищем? Напиши через запятую: стажировка, работа, фриланс.")


@router.message(Command("profile_edit"))
async def profile_edit(message: Message, user, state: FSMContext):
    if not _need_user(user):
        return await message.answer("Сначала /start")
    await _ask_types(message, state)


@router.message(StateFilter(Onboarding.types), F.text)
async def ob_types(message: Message, state: FSMContext):
    chosen = [TYPES[w] for w in parse_list(message.text) if w in TYPES]
    if not chosen:
        return await message.answer("Не понял. Варианты: стажировка, работа, фриланс.")
    await state.update_data(search_types=chosen)
    await state.set_state(Onboarding.stack)
    await message.answer("Твой стек через запятую. Например: Python, FastAPI, PostgreSQL, Docker")


@router.message(StateFilter(Onboarding.stack), F.text)
async def ob_stack(message: Message, state: FSMContext):
    stack = parse_list(message.text)
    if not stack or len(stack) > 20 or any(len(s) > 100 for s in stack):
        return await message.answer("Нужно от 1 до 20 технологий через запятую.")
    await state.update_data(stack=stack)
    await state.set_state(Onboarding.level)
    await message.answer("Твой уровень?", reply_markup=kb.choice_kb("lvl", LEVELS))


@router.callback_query(StateFilter(Onboarding.level), F.data.startswith("lvl:"))
async def ob_level(cb: CallbackQuery, state: FSMContext):
    level = cb.data.split(":", 1)[1]
    if level not in dict(LEVELS):
        return await cb.answer()
    await state.update_data(level=level)
    await state.set_state(Onboarding.work_format)
    await cb.answer()
    await cb.message.answer("Формат работы?", reply_markup=kb.choice_kb("fmt", FORMATS))


@router.callback_query(StateFilter(Onboarding.work_format), F.data.startswith("fmt:"))
async def ob_format(cb: CallbackQuery, state: FSMContext):
    fmt = cb.data.split(":", 1)[1]
    if fmt not in dict(FORMATS):
        return await cb.answer()
    await state.update_data(work_format=fmt)
    await state.set_state(Onboarding.city)
    await cb.answer()
    await cb.message.answer("Город (или «нет»):")


@router.message(StateFilter(Onboarding.city), F.text)
async def ob_city(message: Message, state: FSMContext):
    city = message.text.strip()
    await state.update_data(city=None if city.lower() in {"нет", "-"} else city[:100])
    await state.set_state(Onboarding.salary)
    await message.answer("Минимальная зарплата или бюджет в рублях (или «нет»):")


@router.message(StateFilter(Onboarding.salary), F.text)
async def ob_salary(message: Message, state: FSMContext):
    try:
        salary = parse_salary(message.text)
    except ValueError as e:
        return await message.answer(str(e))
    await state.update_data(min_salary=salary)
    await state.set_state(Onboarding.about)
    await message.answer("Пару предложений о себе для откликов (до 1000 символов, или «нет»):")


@router.message(StateFilter(Onboarding.about), F.text)
async def ob_about(message: Message, session, user, state: FSMContext):
    about = message.text.strip()
    data = await state.get_data()
    data["about"] = "" if about.lower() in {"нет", "-"} else about
    try:
        await users.update_profile(session, user, data)
    except ValueError as e:
        return await message.answer(f"Не получилось сохранить: {escape(str(e))}")
    await state.clear()
    await message.answer(
        "Готово! Профиль сохранён, фильтры настроены по нему. Я пришлю подходящие предложения.\n\n"
        "Чтобы оценки и черновики писал AI, подключи свой ключ: /ai (без ключа оцениваю по ключевым словам).\n\n" + HELP
    )


# ---------- профиль и фильтры (REQ-003) ----------


@router.message(Command("profile"))
async def profile_cmd(message: Message, user):
    if not _need_user(user) or not user.is_onboarded:
        return await message.answer("Профиль ещё не заполнен — /start")
    p = user.profile
    await message.answer(
        f"<b>Ищу:</b> {escape(', '.join(p['search_types']))}\n<b>Стек:</b> {escape(', '.join(p['stack']))}\n"
        f"<b>Уровень:</b> {escape(p['level'])} · <b>формат:</b> {escape(p['work_format'])}\n"
        f"<b>Город:</b> {escape(p['city'] or '—')} · <b>от:</b> {p['min_salary'] or '—'}\n"
        f"<b>О себе:</b> {escape(p['about'] or '—')}\n\n/profile_edit — изменить"
    )


@router.message(Command("filters"))
async def filters_cmd(message: Message, user):
    if not _need_user(user) or not user.is_onboarded:
        return await message.answer("Сначала заполни профиль — /start")
    f = user.filters
    await message.answer(
        f"<b>Ключевые слова:</b> {escape(', '.join(f.get('keywords') or []) or '—')}\n"
        f"<b>Исключения:</b> {escape(', '.join(f.get('stop_words') or []) or '—')}\n"
        f"<b>Зарплата от:</b> {f.get('min_salary') or '—'} · <b>формат:</b> {escape(f.get('work_format') or 'any')}"
        f" · <b>город:</b> {escape(f.get('city') or '—')}\n<b>Порог:</b> {f.get('threshold', 60)}\n\n" + HELP
    )


@router.message(Command("kw", "stop"))
async def terms_cmd(message: Message, command: CommandObject, session, user):
    if not _need_user(user) or not user.is_onboarded:
        return await message.answer("Сначала /start")
    field = "keywords" if command.command == "kw" else "stop_words"
    parts = (command.args or "").split(maxsplit=1)
    if len(parts) != 2 or parts[0] not in {"add", "del"}:
        return await message.answer(f"Пример: /{command.command} add python")
    try:
        if parts[0] == "add":
            await users.add_term(session, user, field, parts[1])
        else:
            await users.remove_term(session, user, field, parts[1])
    except ValueError as e:
        return await message.answer(escape(str(e)))
    await message.answer("Сохранил. /filters — посмотреть")


@router.message(Command("salary", "format", "city", "threshold"))
async def filter_value_cmd(message: Message, command: CommandObject, session, user):
    if not _need_user(user) or not user.is_onboarded:
        return await message.answer("Сначала /start")
    arg = (command.args or "").strip()
    try:
        if command.command == "salary":
            await users.set_filter(session, user, "min_salary", parse_salary(arg))
        elif command.command == "format":
            await users.set_filter(session, user, "work_format", arg.lower())
        elif command.command == "city":
            await users.set_filter(session, user, "city", None if arg.lower() in {"нет", "-", ""} else arg)
        else:
            await users.set_threshold(session, user, parse_threshold(arg))
    except ValueError as e:
        return await message.answer(escape(str(e)))
    await message.answer("Сохранил. /filters — посмотреть")


# ---------- AI-ключ (REQ-007, REQ-008) ----------


@router.message(Command("ai"))
async def ai_cmd(message: Message, session, user, state: FSMContext):
    if not _need_user(user):
        return await message.answer("Сначала /start")
    await state.clear()
    key = await users.get_ai_key(session, user)
    status = (
        f"Подключено: {escape(key.provider)} · {escape(key.model)} · ключ …{escape(key.key_last4)}"
        if key
        else "AI не подключён — оцениваю по ключевым словам."
    )
    await message.answer(
        status + "\n\nВыбери провайдера, чтобы подключить свой ключ. Нужен API-ключ с бесплатным или своим тарифом.",
        reply_markup=kb.providers_kb(),
    )


@router.callback_query(F.data.startswith("aip:"))
async def ai_provider(cb: CallbackQuery, user, state: FSMContext):
    name = cb.data.split(":", 1)[1]
    if not _need_user(user) or name not in PRESETS:
        return await cb.answer()
    preset = PRESETS[name]
    await state.set_state(AISetup.base_url if name == "custom" else AISetup.model)
    await state.update_data(provider=name, base_url=preset.base_url, default_model=preset.default_model)
    await cb.answer()
    if name == "custom":
        return await cb.message.answer("Пришли base_url OpenAI-совместимого API (https://…/v1):")
    await cb.message.answer(
        f"{escape(preset.hint)}\n\nМодель по умолчанию: <code>{escape(preset.default_model)}</code>\n"
        "Пришли название модели или «-», чтобы оставить по умолчанию."
    )


@router.message(StateFilter(AISetup.base_url), F.text)
async def ai_base_url(message: Message, state: FSMContext):
    try:
        url = validate_base_url(message.text)
    except ValueError as e:
        return await message.answer(f"Адрес не подходит: {escape(str(e))}")
    await state.update_data(base_url=url)
    await state.set_state(AISetup.model)
    await message.answer("Название модели:")


@router.message(StateFilter(AISetup.model), F.text)
async def ai_model(message: Message, state: FSMContext):
    data = await state.get_data()
    model = message.text.strip()
    if model in {"-", ""}:
        model = data.get("default_model") or ""
    if not model or "<" in model or len(model) > 200:
        return await message.answer("Укажи название модели (для YandexGPT: gpt://<folder_id>/yandexgpt-lite/latest).")
    await state.update_data(model=model)
    await state.set_state(AISetup.key)
    await message.answer("Пришли API-ключ. Сообщение с ключом я сразу удалю из чата, ключ сохраню зашифрованным.")


@router.message(StateFilter(AISetup.key), F.text)
async def ai_key(message: Message, session, user, settings, cipher, http, state: FSMContext):
    key = message.text.strip()
    try:
        await message.delete()
    except Exception:  # нет прав на удаление — предупредим
        await message.answer("Не смог удалить сообщение с ключом — удали его вручную.")
    data = await state.get_data()
    await state.clear()
    if len(key) < 8 or len(key) > 500 or " " in key:
        return await message.answer("Это не похоже на ключ. Попробуй ещё раз: /ai")
    wait = await message.answer("Проверяю ключ…")
    try:
        await llm_client.check_key(http, data["base_url"], data["model"], key)
    except llm_client.LLMError as e:
        return await wait.edit_text(f"Ключ не сохранён: {e.user_message}. Попробуй другой ключ или провайдера: /ai")
    await users.save_ai_key(session, user, cipher, data["provider"], data["model"], data["base_url"], key)
    await wait.edit_text(f"Готово, AI подключён ({escape(data['provider'])}). Ключ: {escape(mask_key(key))}")


@router.message(Command("ai_off"))
async def ai_off(message: Message, session, user):
    if not _need_user(user):
        return await message.answer("Сначала /start")
    await users.delete_ai_key(session, user)
    await message.answer("Ключ удалён. Оцениваю по ключевым словам.")


# ---------- удаление аккаунта (REQ-014) ----------


@router.message(Command("delete_me"))
async def delete_me(message: Message, user):
    if user is None:
        return await message.answer("Данных о тебе нет.")
    await message.answer("Удалить профиль, ключ, фильтры и историю? Это необратимо.", reply_markup=kb.delete_kb())


@router.callback_query(F.data.startswith("del:"))
async def delete_confirm(cb: CallbackQuery, session, user, state: FSMContext):
    await cb.answer()
    if cb.data == "del:yes" and user is not None:
        await users.delete_user(session, user)
        await state.clear()
        return await cb.message.edit_text("Все данные удалены. Чтобы начать заново — /start")
    await cb.message.edit_text("Отменено.")


# ---------- кнопки карточки (REQ-010, REQ-011) ----------


@router.callback_query(F.data.startswith("o:"))
async def card_action(cb: CallbackQuery, session, user, settings, cipher, http):
    parsed = kb.parse_cb(cb.data)
    if parsed is None or not _need_user(user):
        return await cb.answer()
    action, offer_id = parsed
    offer = await offers.get_offer_for_user(session, user, offer_id)  # изоляция: только свои
    if offer is None:
        return await cb.answer("Предложение не найдено", show_alert=True)
    if action == "hide":
        await offers.mark(session, user, offer_id, "hidden")
        await cb.answer("Скрыто")
        try:
            await cb.message.delete()
        except Exception:
            log.debug("cannot delete card message")
        return
    if action == "fav":
        await offers.mark(session, user, offer_id, "favorite")
        return await cb.answer("В избранном")
    if action == "more":
        await cb.answer()
        text = offer.description[:3500] or "Описание не найдено — открой ссылку."
        return await cb.message.answer(f"<b>{escape(offer.title)}</b>\n\n{escape(text)}")
    key = await users.get_ai_key(session, user)
    if key is None:
        return await cb.answer("Для черновика подключи свой AI-ключ: /ai", show_alert=True)
    await cb.answer()
    wait = await cb.message.answer("Пишу черновик…")
    llm = make_llm(http, cipher, key, max_tokens=700)
    try:
        draft = await llm(prompts.build_draft_messages(offers.to_data(offer), user.profile or {}))
    except llm_client.LLMError as e:
        return await wait.edit_text(f"Не получилось: {e.user_message}")
    await wait.edit_text(f"<b>Черновик отклика</b> (проверь и отправь сам):\n\n{escape(draft[:3800])}")


# ---------- ручная ссылка и текст (REQ-013) ----------


async def _evaluate_and_send(message: Message, session, user, settings, cipher, http, data: OfferData):
    row = await offers.upsert_one(session, data)
    llm = make_llm(http, cipher, await users.get_ai_key(session, user))
    d = await offers.evaluate(session, user, row, llm=llm, daily_limit=settings.llm_daily_limit, manual=True)
    if d is None:
        return await message.answer("Это предложение я уже присылал — ищи его выше в чате.")
    await message.answer(
        render_card(offers.to_data(row), d.score, d.reason, d.ai_used), reply_markup=kb.card_kb(row.id)
    )


@router.message(StateFilter(Manual.text), F.text)
async def manual_text(message: Message, session, user, settings, cipher, http, state: FSMContext):
    data = await state.get_data()
    await state.clear()
    text = message.text.strip()[:8000]
    title = text.splitlines()[0][:300] if text else "Без названия"
    offer = OfferData(source="manual", external_id=data["url"], url=data["url"], title=title, description=text)
    await _evaluate_and_send(message, session, user, settings, cipher, http, offer)


@router.message(F.text)
async def any_text(message: Message, session, user, settings, cipher, http, state: FSMContext):
    if not _need_user(user) or not user.is_onboarded:
        return await message.answer("Начнём с /start")
    url = extract_url(message.text)
    if not url:
        return await message.answer("Пришли ссылку на вакансию или заказ, либо /help")
    if detect_source(url) is None:
        return await message.answer("Поддерживаю ссылки hh.ru, Хабр Карьеры, Kwork и FL.ru.")
    wait = await message.answer("Смотрю ссылку…")
    try:
        data = await page.fetch_page(http, url, settings.user_agent)
    except page.PageError as e:
        await state.set_state(Manual.text)
        await state.update_data(url=normalize_url(url))
        return await wait.edit_text(f"Не смог прочитать страницу ({escape(str(e))}). Пришли текст вакансии сообщением.")
    await wait.delete()
    await _evaluate_and_send(message, session, user, settings, cipher, http, data)
