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


def schedule_list(buttons: list[tuple[int, str]], page: int, pages: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for session_id, label in buttons:
        builder.row(InlineKeyboardButton(
            text=label, callback_data=ScheduleCb(action=ScheduleAction.SESSION, item_id=session_id, page=page).pack()))
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text=texts.BTN_PREV_PAGE,
                                        callback_data=ScheduleCb(action=ScheduleAction.PAGE, page=page - 1).pack()))
    if page + 1 < pages:
        nav.append(InlineKeyboardButton(text=texts.BTN_NEXT_PAGE,
                                        callback_data=ScheduleCb(action=ScheduleAction.PAGE, page=page + 1).pack()))
    if nav:
        builder.row(*nav)
    builder.row(InlineKeyboardButton(text=texts.BTN_ALL_PROGRAMS,
                                     callback_data=ScheduleCb(action=ScheduleAction.PROGRAMS).pack()))
    return builder.as_markup()


def session_card(session_id: int, program_id: int, page: int, apply_text: str | None, notify: bool,
                 has_program_text: bool) -> InlineKeyboardMarkup:
    rows = []
    if apply_text:
        rows.append([InlineKeyboardButton(
            text=apply_text, callback_data=ScheduleCb(action=ScheduleAction.APPLY, item_id=session_id).pack())])
    if notify:
        rows.append([InlineKeyboardButton(
            text=texts.BTN_NOTIFY_ME, callback_data=ScheduleCb(action=ScheduleAction.NOTIFY, item_id=program_id).pack())])
    if has_program_text:
        rows.append([InlineKeyboardButton(
            text=texts.BTN_PROGRAM_TEXT,
            callback_data=ScheduleCb(action=ScheduleAction.PROGRAM_TEXT, item_id=program_id).pack())])
    rows.append([InlineKeyboardButton(
        text=texts.BTN_TO_SCHEDULE, callback_data=ScheduleCb(action=ScheduleAction.PAGE, page=page).pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def programs_list(programs: list[tuple[int, str]]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for program_id, title in programs:
        builder.row(InlineKeyboardButton(
            text=f"🦷 {title}", callback_data=ScheduleCb(action=ScheduleAction.PROGRAM, item_id=program_id).pack()))
    builder.row(InlineKeyboardButton(text=texts.BTN_TO_SCHEDULE,
                                     callback_data=ScheduleCb(action=ScheduleAction.PAGE).pack()))
    return builder.as_markup()


def program_card(program_id: int, sessions: list[tuple[int, str]], has_program_text: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for session_id, label in sessions:
        builder.row(InlineKeyboardButton(
            text=label, callback_data=ScheduleCb(action=ScheduleAction.SESSION, item_id=session_id).pack()))
    if not sessions:
        builder.row(InlineKeyboardButton(
            text=texts.BTN_NOTIFY_ME, callback_data=ScheduleCb(action=ScheduleAction.NOTIFY, item_id=program_id).pack()))
    if has_program_text:
        builder.row(InlineKeyboardButton(
            text=texts.BTN_PROGRAM_TEXT,
            callback_data=ScheduleCb(action=ScheduleAction.PROGRAM_TEXT, item_id=program_id).pack()))
    builder.row(InlineKeyboardButton(text=texts.BTN_TO_PROGRAMS,
                                     callback_data=ScheduleCb(action=ScheduleAction.PROGRAMS).pack()))
    return builder.as_markup()


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
