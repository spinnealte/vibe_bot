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
    ApplyAction,
    ApplyCb,
    ApplyPriceCb,
    ApplySeatsCb,
    MyApplicationAction,
    MyApplicationCb,
    ScheduleAction,
    ScheduleCb,
    CabinetAction,
    CabinetCb,
    ConsentCb,
    EditControl,
    EditControlCb,
    EditField,
    EditFieldCb,
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


def cabinet_actions(marketing_on: bool) -> InlineKeyboardMarkup:
    marketing_text = texts.BTN_MARKETING_TURN_OFF if marketing_on else texts.BTN_MARKETING_TURN_ON
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=texts.BTN_EDIT_PROFILE,
                                  callback_data=CabinetCb(action=CabinetAction.EDIT_MENU).pack())],
            [InlineKeyboardButton(text=texts.BTN_MY_APPLICATIONS,
                                  callback_data=CabinetCb(action=CabinetAction.APPLICATIONS).pack())],
            [InlineKeyboardButton(text=marketing_text, callback_data=CabinetCb(action=CabinetAction.MARKETING).pack())],
        ]
    )


def edit_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for field in EditField:
        builder.button(text=texts.EDIT_FIELD_LABELS[field.value], callback_data=EditFieldCb(field=field))
    builder.adjust(2)
    builder.row(InlineKeyboardButton(text=texts.BTN_BACK_TO_CABINET,
                                     callback_data=CabinetCb(action=CabinetAction.BACK).pack()))
    return builder.as_markup()


def edit_controls(can_clear: bool) -> InlineKeyboardMarkup:
    row = [InlineKeyboardButton(text=texts.BTN_EDIT_CANCEL,
                                callback_data=EditControlCb(action=EditControl.CANCEL).pack())]
    if can_clear:
        row.insert(0, InlineKeyboardButton(text=texts.BTN_EDIT_CLEAR,
                                           callback_data=EditControlCb(action=EditControl.CLEAR).pack()))
    return InlineKeyboardMarkup(inline_keyboard=[row])


def _sch(action: ScheduleAction, item_id: int = 0, index: int = 0, page: int = 0) -> str:
    return ScheduleCb(action=action, item_id=item_id, index=index, page=page).pack()


def _pager(index: int, total: int, prev_data: str, next_data: str) -> list[InlineKeyboardButton]:
    """◀️ 2/5 ▶️ — стрелка на краю списка скрывается."""
    row = []
    if index > 0:
        row.append(InlineKeyboardButton(text=texts.BTN_PREV_PAGE, callback_data=prev_data))
    row.append(InlineKeyboardButton(text=f"{index + 1}/{total}", callback_data=_sch(ScheduleAction.NOOP)))
    if index + 1 < total:
        row.append(InlineKeyboardButton(text=texts.BTN_NEXT_PAGE, callback_data=next_data))
    return row


def course_carousel(index: int, total: int, program_id: int, session_id: int | None, apply_text: str | None,
                    notify: bool, has_program_text: bool) -> InlineKeyboardMarkup:
    """◀️ n/N ▶️ / Оставить заявку (или «Сообщить о наборе») / Программа · Назад."""
    rows = [_pager(index, total, _sch(ScheduleAction.SLIDE, index=index - 1), _sch(ScheduleAction.SLIDE, index=index + 1))]
    if apply_text and session_id:
        rows.append([InlineKeyboardButton(text=apply_text,
                                          callback_data=_sch(ScheduleAction.APPLY, session_id, index))])
    if notify:
        rows.append([InlineKeyboardButton(text=texts.BTN_NOTIFY_ME,
                                          callback_data=_sch(ScheduleAction.NOTIFY, program_id, index))])
    bottom = []
    if has_program_text:
        bottom.append(InlineKeyboardButton(text=texts.BTN_PROGRAM_TEXT,
                                           callback_data=_sch(ScheduleAction.PROGRAM, program_id, index)))
    bottom.append(InlineKeyboardButton(text=texts.BTN_CLOSE_SCHEDULE, callback_data=_sch(ScheduleAction.CLOSE)))
    rows.append(bottom)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def back_to_course(index: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=texts.BTN_BACK_TO_COURSE, callback_data=_sch(ScheduleAction.SLIDE, index=index)),
    ]])


def to_schedule(index: int = 0) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=texts.BTN_TO_SCHEDULE, callback_data=_sch(ScheduleAction.SLIDE, index=index)),
    ]])


def program_pages(program_id: int, index: int, page: int, pages: int) -> InlineKeyboardMarkup:
    rows = []
    if pages > 1:
        rows.append(_pager(page, pages, _sch(ScheduleAction.PROGRAM, program_id, index, page - 1),
                           _sch(ScheduleAction.PROGRAM, program_id, index, page + 1)))
    rows.append([InlineKeyboardButton(text=texts.BTN_BACK_TO_COURSE,
                                      callback_data=_sch(ScheduleAction.SLIDE, index=index))])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def apply_prices(prices: list[tuple[int, str]]) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=label, callback_data=ApplyPriceCb(price_id=pid).pack())] for pid, label in prices]
    rows.append([InlineKeyboardButton(text=texts.BTN_APPLY_CANCEL,
                                      callback_data=ApplyCb(action=ApplyAction.CANCEL).pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def apply_seats(choices: list[int]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for n in choices:
        label = texts.APPLY_SEATS_ONE if n == 1 else texts.APPLY_SEATS_MANY.format(n=n)
        builder.button(text=label, callback_data=ApplySeatsCb(seats=n))
    builder.adjust(1, 4)
    builder.row(InlineKeyboardButton(text=texts.BTN_APPLY_CANCEL,
                                     callback_data=ApplyCb(action=ApplyAction.CANCEL).pack()))
    return builder.as_markup()


def apply_comment() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=texts.BTN_NO_COMMENT, callback_data=ApplyCb(action=ApplyAction.NO_COMMENT).pack())],
        [InlineKeyboardButton(text=texts.BTN_APPLY_CANCEL, callback_data=ApplyCb(action=ApplyAction.CANCEL).pack())],
    ])


def apply_confirm() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=texts.BTN_APPLY_SEND, callback_data=ApplyCb(action=ApplyAction.SEND).pack()),
        InlineKeyboardButton(text=texts.BTN_APPLY_CANCEL, callback_data=ApplyCb(action=ApplyAction.CANCEL).pack()),
    ]])


def my_applications(cancellable: list[tuple[int, str]]) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(
        text=label, callback_data=MyApplicationCb(action=MyApplicationAction.ASK_CANCEL, enrollment_id=eid).pack())]
        for eid, label in cancellable]
    rows.append([InlineKeyboardButton(text=texts.BTN_BACK_TO_CABINET,
                                      callback_data=CabinetCb(action=CabinetAction.BACK).pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def confirm_cancel_application(enrollment_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=texts.BTN_CANCEL_APPLICATION_YES, callback_data=MyApplicationCb(
            action=MyApplicationAction.CANCEL, enrollment_id=enrollment_id).pack()),
        InlineKeyboardButton(text=texts.BTN_CANCEL_APPLICATION_NO, callback_data=MyApplicationCb(
            action=MyApplicationAction.KEEP, enrollment_id=enrollment_id).pack()),
    ]])


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
