from collections.abc import Iterable

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from stubbot.config import get_settings
from stubbot.services.admin_dictionaries import DictKind
from stubbot.tg import texts
from stubbot.tg.callbacks import (
    ApplyAction,
    ApplyCb,
    ApplyPriceCb,
    DictAction,
    DictCb,
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
    ConsentFormAction,
    ConsentFormCb,
    OrderAction,
    OrderCb,
    SelectDoneCb,
    SelectGroup,
    SkipOptionalCb,
    ToggleCb,
)


def main_menu(chat_id: int) -> ReplyKeyboardMarkup:
    """Главное меню. «💬 Менеджер» - когда задана ссылка на менеджера (MANAGER_URL); админам (ADMIN_IDS / owner
    из .env) - ещё «⚙️ Админка»: вторым рядом, а если он занят - отдельным. chat_id лички = id пользователя.

    Панель на виду, но не «всегда» (без is_persistent): человек может свернуть её значком рядом с полем ввода
    и тем же значком развернуть. Сам бот её не сворачивает: в режиме one_time_keyboard Telegram на iPhone после
    нажатия убирает и значок - панель не вернуть, пока бот не пришлёт её заново (проверено пользователем 14.10.2026)."""
    settings = get_settings()
    keyboard = [
        [KeyboardButton(text=texts.BTN_SCHEDULE), KeyboardButton(text=texts.BTN_CABINET)],
        [KeyboardButton(text=texts.BTN_ABOUT)],
    ]
    if settings.manager_url:
        keyboard[1].append(KeyboardButton(text=texts.BTN_MANAGER))
    if settings.is_admin(chat_id):
        admin = KeyboardButton(text=texts.BTN_ADMIN)
        if len(keyboard[1]) < 2:
            keyboard[1].append(admin)
        else:
            keyboard.append([admin])
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)


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


def consent_form(pd: bool, ads: bool, with_ads: bool = True) -> InlineKeyboardMarkup:
    """Согласия при регистрации - две отдельные галочки (нажатие ставит и снимает): 1 - политика и обработка ПД,
    обязательна; 2 - рекламные рассылки, по желанию. «Продолжить» без первой дальше не пускает
    (handlers/consent.py). Кнопки «Не согласен» нет: не поставил галочку - не согласился."""

    def box(checked: bool, label: str, action: ConsentFormAction) -> list[InlineKeyboardButton]:
        mark = texts.CONSENT_MARK_ON if checked else texts.CONSENT_MARK_OFF
        return [InlineKeyboardButton(text=mark + label, callback_data=ConsentFormCb(action=action).pack())]

    rows = [box(pd, texts.CONSENT_FORM_PD, ConsentFormAction.PD)]
    if with_ads:
        rows.append(box(ads, texts.CONSENT_FORM_ADS, ConsentFormAction.ADS))
    rows.append([InlineKeyboardButton(text=texts.CONSENT_FORM_CONTINUE,
                                      callback_data=ConsentFormCb(action=ConsentFormAction.CONTINUE).pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def marketing_consent() -> InlineKeyboardMarkup:
    kind = ConsentKind.MARKETING
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=texts.CONSENT_MARKETING_YES,
                                     callback_data=ConsentCb(kind=kind, accept=True).pack()),
                InlineKeyboardButton(text=texts.CONSENT_MARKETING_NO,
                                     callback_data=ConsentCb(kind=kind, accept=False).pack()),
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


def cabinet_actions(marketing_on: bool, applications: bool) -> InlineKeyboardMarkup:
    marketing_text = texts.BTN_MARKETING_TURN_OFF if marketing_on else texts.BTN_MARKETING_TURN_ON
    rows = [[InlineKeyboardButton(text=texts.BTN_EDIT_PROFILE,
                                  callback_data=CabinetCb(action=CabinetAction.EDIT_MENU).pack())]]
    if applications:
        rows.append([InlineKeyboardButton(text=texts.BTN_MY_APPLICATIONS,
                                          callback_data=CabinetCb(action=CabinetAction.APPLICATIONS).pack())])
    rows.append([InlineKeyboardButton(text=marketing_text,
                                      callback_data=CabinetCb(action=CabinetAction.MARKETING).pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)


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
    """◀️ 2/5 ▶️ - стрелка на краю списка скрывается."""
    row = []
    if index > 0:
        row.append(InlineKeyboardButton(text=texts.BTN_PREV_PAGE, callback_data=prev_data))
    row.append(InlineKeyboardButton(text=f"{index + 1}/{total}", callback_data=_sch(ScheduleAction.NOOP)))
    if index + 1 < total:
        row.append(InlineKeyboardButton(text=texts.BTN_NEXT_PAGE, callback_data=next_data))
    return row


def manager_button(url: str, label: str = texts.BTN_MANAGER) -> InlineKeyboardButton:
    """Кнопка-ссылка на чат менеджера. Telegram открывает чат сам, боту о нажатии не сообщает."""
    return InlineKeyboardButton(text=label, url=url)


def contact_manager(url: str, label: str = texts.BTN_MANAGER) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[manager_button(url, label)]])


def course_carousel(index: int, total: int, program_id: int, session_id: int, apply_text: str | None,
                    notify: bool, has_program_text: bool, manager_url: str | None = None,
                    admin: bool = False) -> InlineKeyboardMarkup:
    """◀️ n/N ▶️ / Оставить заявку (или «Сообщить о наборе») / Менеджер / Программа · Назад / ✏️ Править (админу)."""
    rows = [_pager(index, total, _sch(ScheduleAction.SLIDE, index=index - 1), _sch(ScheduleAction.SLIDE, index=index + 1))]
    if apply_text:
        rows.append([InlineKeyboardButton(text=apply_text,
                                          callback_data=_sch(ScheduleAction.APPLY, session_id, index))])
    if notify:
        rows.append([InlineKeyboardButton(text=texts.BTN_NOTIFY_ME,
                                          callback_data=_sch(ScheduleAction.NOTIFY, program_id, index))])
    if manager_url:
        rows.append([manager_button(manager_url)])
    bottom = []
    if has_program_text:
        bottom.append(InlineKeyboardButton(text=texts.BTN_PROGRAM_TEXT,
                                           callback_data=_sch(ScheduleAction.PROGRAM, program_id, index)))
    bottom.append(InlineKeyboardButton(text=texts.BTN_CLOSE_SCHEDULE, callback_data=_sch(ScheduleAction.CLOSE)))
    rows.append(bottom)
    if admin:
        # Ведёт в карточку этого проведения в админке (афиша правится в «Проведениях»); кнопку обрабатывает
        # админ-роутер (фильтр IsAdmin).
        rows.append([InlineKeyboardButton(
            text=texts.BTN_COURSE_EDIT,
            callback_data=DictCb(kind=DictKind.SESSIONS, action=DictAction.VIEW, item_id=session_id).pack(),
        )])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def back_to_course(index: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=texts.BTN_BACK_TO_COURSE, callback_data=_sch(ScheduleAction.SLIDE, index=index)),
    ]])


def to_schedule(index: int = 0) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=texts.BTN_TO_SCHEDULE, callback_data=_sch(ScheduleAction.SLIDE, index=index)),
    ]])


def program_pages(program_id: int, index: int, page: int, pages: int,
                  manager_url: str | None = None) -> InlineKeyboardMarkup:
    """Под программой курса: листание страниц, «Менеджер» (дочитал программу - момент решения), назад к курсу."""
    rows = []
    if pages > 1:
        rows.append(_pager(page, pages, _sch(ScheduleAction.PROGRAM, program_id, index, page - 1),
                           _sch(ScheduleAction.PROGRAM, program_id, index, page + 1)))
    if manager_url:
        rows.append([manager_button(manager_url)])
    rows.append([InlineKeyboardButton(text=texts.BTN_BACK_TO_COURSE,
                                      callback_data=_sch(ScheduleAction.SLIDE, index=index))])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def apply_prices(prices: list[tuple[int, str]]) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=label, callback_data=ApplyPriceCb(price_id=pid).pack())] for pid, label in prices]
    rows.append([InlineKeyboardButton(text=texts.BTN_APPLY_CANCEL,
                                      callback_data=ApplyCb(action=ApplyAction.CANCEL).pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def apply_input_cancel() -> InlineKeyboardMarkup:
    """Под вопросами про коллегу (ответ - текстом): только «Отмена»."""
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=texts.BTN_APPLY_CANCEL, callback_data=ApplyCb(action=ApplyAction.CANCEL).pack()),
    ]])


def apply_confirm(pair: bool = False, colleague_ok: bool = False) -> InlineKeyboardMarkup:
    """Проверка записи: для «Для двух коллег» - галочка «Коллега согласен» (без неё «Записаться» не пускает),
    потом «✅ Записаться» и «✖️ Отмена»."""
    rows = []
    if pair:
        mark = texts.CONSENT_MARK_ON if colleague_ok else texts.CONSENT_MARK_OFF
        rows.append([InlineKeyboardButton(text=mark + texts.BTN_APPLY_COLLEAGUE_OK,
                                          callback_data=ApplyCb(action=ApplyAction.COLLEAGUE_OK).pack())])
    rows.append([InlineKeyboardButton(text=texts.BTN_APPLY_SEND, callback_data=ApplyCb(action=ApplyAction.SEND).pack())])
    rows.append([InlineKeyboardButton(text=texts.BTN_APPLY_CANCEL,
                                      callback_data=ApplyCb(action=ApplyAction.CANCEL).pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def payer_choice(order_id: int) -> InlineKeyboardMarkup:
    """Под «Вы записаны»: как будет оплачивать плательщик заказа."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=texts.BTN_PAY_INDIVIDUAL,
                              callback_data=OrderCb(action=OrderAction.INDIVIDUAL, order_id=order_id).pack())],
        [InlineKeyboardButton(text=texts.BTN_PAY_ORGANIZATION,
                              callback_data=OrderCb(action=OrderAction.ORGANIZATION, order_id=order_id).pack())],
    ])


def _myapp(action: MyApplicationAction, enrollment_id: int = 0, index: int = 0) -> str:
    return MyApplicationCb(action=action, enrollment_id=enrollment_id, index=index).pack()


def back_to_cabinet() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=texts.BTN_BACK_TO_CABINET, callback_data=CabinetCb(action=CabinetAction.BACK).pack()),
    ]])


def my_application_card(enrollment_id: int, index: int, total: int, cancellable: bool) -> InlineKeyboardMarkup:
    """◀️ n/N ▶️ / ✖️ Отменить заявку / ⬅️ В кабинет."""
    rows = []
    if total > 1:
        rows.append(_pager(index, total, _myapp(MyApplicationAction.VIEW, index=index - 1),
                           _myapp(MyApplicationAction.VIEW, index=index + 1)))
    if cancellable:
        rows.append([InlineKeyboardButton(text=texts.BTN_CANCEL_APPLICATION,
                                          callback_data=_myapp(MyApplicationAction.ASK_CANCEL, enrollment_id, index))])
    rows.append(back_to_cabinet().inline_keyboard[0])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def confirm_cancel_application(enrollment_id: int, index: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=texts.BTN_CANCEL_APPLICATION_YES,
                             callback_data=_myapp(MyApplicationAction.CANCEL, enrollment_id, index)),
        InlineKeyboardButton(text=texts.BTN_CANCEL_APPLICATION_NO,
                             callback_data=_myapp(MyApplicationAction.KEEP, enrollment_id, index)),
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
