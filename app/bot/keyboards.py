from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from ..llm.providers import PRESETS

ACTIONS = {"more", "draft", "hide", "fav"}


def _btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def card_kb(offer_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn("Подробнее", f"o:more:{offer_id}"), _btn("Черновик отклика", f"o:draft:{offer_id}")],
            [_btn("Скрыть", f"o:hide:{offer_id}"), _btn("В избранное", f"o:fav:{offer_id}")],
        ]
    )


def parse_cb(data: str | None) -> tuple[str, int] | None:
    parts = (data or "").split(":")
    if len(parts) != 3 or parts[0] != "o" or parts[1] not in ACTIONS or not parts[2].isdigit():
        return None
    return parts[1], int(parts[2])


def choice_kb(prefix: str, options: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[_btn(title, f"{prefix}:{value}")] for value, title in options])


def consent_kb() -> InlineKeyboardMarkup:
    return choice_kb("consent", [("yes", "Согласен")])


def providers_kb() -> InlineKeyboardMarkup:
    return choice_kb("aip", [(p.name, p.title) for p in PRESETS.values()])


def delete_kb() -> InlineKeyboardMarkup:
    return choice_kb("del", [("yes", "Да, удалить всё"), ("no", "Отмена")])
