from collections.abc import Iterable

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from stubbot.tg import texts
from stubbot.tg.callbacks import (
    ConsentCb,
    ConsentKind,
    NameAction,
    NameCb,
    SelectDoneCb,
    SelectGroup,
    SkipOptionalCb,
    ToggleCb,
)


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=texts.BTN_SCHEDULE), KeyboardButton(text=texts.BTN_CABINET)],
            [KeyboardButton(text=texts.BTN_ABOUT)],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def registration_nav() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=texts.BTN_BACK), KeyboardButton(text=texts.BTN_CANCEL)]],
        resize_keyboard=True,
        is_persistent=True,
    )


def share_phone() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=texts.BTN_SHARE_PHONE, request_contact=True)],
            [KeyboardButton(text=texts.BTN_CANCEL)],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def consent(kind: ConsentKind) -> InlineKeyboardMarkup:
    accept, decline = (
        (texts.CONSENT_ACCEPT, texts.CONSENT_DECLINE)
        if kind is ConsentKind.PD
        else (texts.CONSENT_MARKETING_YES, texts.CONSENT_MARKETING_NO)
    )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=accept, callback_data=ConsentCb(kind=kind, accept=True).pack()),
                InlineKeyboardButton(text=decline, callback_data=ConsentCb(kind=kind, accept=False).pack()),
            ]
        ]
    )


def skip_optional(field: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=texts.BTN_SKIP, callback_data=SkipOptionalCb(field=field).pack())]]
    )


def confirm_name() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=texts.REG_NAME_OK, callback_data=NameCb(action=NameAction.CONFIRM).pack()),
                InlineKeyboardButton(text=texts.REG_NAME_FIX, callback_data=NameCb(action=NameAction.FIX).pack()),
            ]
        ]
    )


def multiselect(group: SelectGroup, options: Iterable[tuple[int, str]], selected: Iterable[int]) -> InlineKeyboardMarkup:
    """Общий мультивыбор: ✅ у отмеченных, по два в ряд, внизу «Готово». Используется для любых справочников."""
    chosen = set(selected)
    builder = InlineKeyboardBuilder()
    for item_id, title in options:
        mark = "✅ " if item_id in chosen else ""
        builder.button(text=f"{mark}{title}", callback_data=ToggleCb(group=group, item_id=item_id))
    builder.adjust(2)
    builder.row(InlineKeyboardButton(text=texts.REG_DONE_BUTTON, callback_data=SelectDoneCb(group=group).pack()))
    return builder.as_markup()
