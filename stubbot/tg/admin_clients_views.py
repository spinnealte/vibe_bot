"""Экраны раздела «👥 Клиенты» (полная версия, этап 1, шаг 4.2): поиск, карточка клиента, участники проведения,
листы ожидания курсов, правка аванса. Текст + клавиатура, как в admin_views.py; строки экранируются (safe).

Карточка клиента помнит, откуда её открыли (value кнопки: BACK_*), - туда ведёт «⬅️ Назад».
"""

from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from stubbot.db.models import Client, Program
from stubbot.services.admin_clients import SEARCH_LIMIT, AdminClientCard, AdvanceInput, SearchResult, SessionParticipants
from stubbot.services.admin_dictionaries import DictKind
from stubbot.tg import texts
from stubbot.tg.admin_orders_views import BACK_ORDER, PICK_ORDER
from stubbot.tg.admin_views import FROM_COURSE
from stubbot.tg.callbacks import (
    AdminAction,
    AdminCb,
    ClientAdminAction,
    ClientAdminCb,
    DictAction,
    DictCb,
    OrderAdminAction,
    OrderAdminCb,
)
from stubbot.tg.formatting import safe, safe_join
from stubbot.tg.picker import PickList
from stubbot.tg.render import dates_text, session_title, short
from stubbot.utils.dates import format_date, format_range_with_year
from stubbot.utils.money import format_rub
from stubbot.utils.names import short_name

Screen = tuple[str, InlineKeyboardMarkup]

# Откуда открыта карточка клиента - value кнопки «Открыть»: буква и id.
BACK_FIND = "f"  # из поиска
BACK_PARTICIPANTS = "p"  # из участников проведения: p<id проведения>
BACK_CANCELLED = "x"  # из отменивших запись: x<id проведения>
BACK_WAITLIST = "w"  # из листа ожидания курса: w<id курса>
# BACK_ORDER (из карточки заказа: o<id заказа>) - в admin_orders_views; FROM_COURSE (лист ожидания открыт из
# карточки курса - назад к ней) - в admin_views: там эти кнопки и рисуются.
MAX_ENROLLMENTS = 10  # записей в карточке клиента
MAX_ORDERS = 10  # заказов (по номерам) в карточке клиента
# Виды пронумерованных списков (tg/picker.py): номер открывает клиента или лист ожидания курса.
PICK_CLIENT = "client"
PICK_WAITLIST = "course_waitlist"


def _cb(action: ClientAdminAction, item_id: int = 0, value: str = "") -> str:
    return ClientAdminCb(action=action, item_id=item_id, value=value).pack()


def _button(text: str, action: ClientAdminAction, item_id: int = 0, value: str = "") -> list[InlineKeyboardButton]:
    return [InlineKeyboardButton(text=text, callback_data=_cb(action, item_id, value))]


def _markup(*rows: list[InlineKeyboardButton]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=list(rows))


def _short(client: Client) -> str:
    return short_name(client.full_name) if client.full_name else texts.NOT_SET


def _local_day(moment: datetime, timezone: str) -> str:
    day = moment.astimezone(ZoneInfo(timezone)).date()
    return format_range_with_year(day, day)


def _to_menu() -> list[InlineKeyboardButton]:
    return _button(texts.BTN_ADMIN_TO_CLIENTS, ClientAdminAction.MENU)


def back_button(back: str) -> list[InlineKeyboardButton]:
    """«⬅️ Назад» из карточки клиента - туда, откуда её открыли."""
    kind, raw_id = back[:1], back[1:]
    item_id = int(raw_id) if raw_id.isdigit() else 0
    label = texts.BTN_ADMIN_CLIENT_BACK
    if kind == BACK_FIND:
        return _button(label, ClientAdminAction.FIND)
    if kind == BACK_PARTICIPANTS and item_id:
        return _button(label, ClientAdminAction.PARTICIPANTS, item_id)
    if kind == BACK_CANCELLED and item_id:
        return _button(label, ClientAdminAction.CANCELLED, item_id)
    if kind == BACK_WAITLIST and item_id:
        return _button(label, ClientAdminAction.WAITLIST, item_id)
    if kind == BACK_ORDER and item_id:
        return [InlineKeyboardButton(text=label, callback_data=OrderAdminCb(action=OrderAdminAction.VIEW,
                                                                           order_id=item_id).pack())]
    return _button(label, ClientAdminAction.MENU)


# --- Меню и поиск ----------------------------------------------------------------------------------------------

def clients_menu(total: int) -> Screen:
    return texts.ADMIN_CLIENTS.format(total=total), _markup(
        _button(texts.BTN_ADMIN_CLIENT_FIND, ClientAdminAction.FIND),
        _button(texts.BTN_ADMIN_WAITLISTS, ClientAdminAction.WAITLISTS),
        [InlineKeyboardButton(text=texts.BTN_ADMIN_BACK, callback_data=AdminCb(action=AdminAction.MENU).pack())],
    )


def ask_query() -> Screen:
    return texts.ADMIN_CLIENT_ASK_QUERY, _markup(_button(texts.BTN_ADMIN_CANCEL, ClientAdminAction.MENU))


def client_row(client: Client) -> str:
    """«Петров Пётр Петрович · …0010 (не в боте)» - по последним цифрам телефона тёзок не спутать. HTML."""
    return texts.ADMIN_CLIENT_ROW.format(
        name=safe(client.full_name), phone=safe((client.phone or "")[-4:]),
        not_in_bot="" if client.telegram_id else texts.ADMIN_CLIENT_NOT_IN_BOT,
    )


def _cb_row(text: str, action: ClientAdminAction, item_id: int = 0, value: str = "") -> list[tuple[str, str]]:
    """Ряд из одной кнопки для пронумерованного списка (picker хранит кнопки текстом)."""
    return [(text, _cb(action, item_id, value))]


def search_results(result: SearchResult, raw_query: str) -> PickList:
    """Найденные клиенты - пронумерованным списком (правило 26.10.2026): клиента открывают его номером,
    не номер - новый запрос. Никого - просто экран с «Искать ещё»."""
    query = safe(" ".join(raw_query.split())[:100])
    more = texts.ADMIN_CLIENT_FOUND_MORE.format(limit=SEARCH_LIMIT) if result.more else ""
    return PickList(
        kind=PICK_CLIENT,
        ids=[client.id for client in result.clients],
        title=texts.ADMIN_CLIENT_FOUND.format(count=len(result.clients), query=query),
        lines=[client_row(client) for client in result.clients],
        hint=more + texts.PICK_HINT_SEARCH,
        empty=texts.ADMIN_CLIENT_NOT_FOUND.format(query=query),
        buttons=[_cb_row(texts.BTN_ADMIN_FIND_AGAIN, ClientAdminAction.FIND),
                 _cb_row(texts.BTN_ADMIN_TO_CLIENTS, ClientAdminAction.MENU)],
        context={"back": BACK_FIND, "requery": True},
    )


# --- Карточка клиента ------------------------------------------------------------------------------------------

def client_card(card: AdminClientCard, timezone: str, back: str) -> Screen:
    client = card.client
    lines = [texts.ADMIN_CLIENT_TITLE.format(name=safe(client.full_name)),
             texts.ADMIN_CLIENT_PHONE.format(value=safe(client.phone)),
             texts.ADMIN_CLIENT_TELEGRAM.format(value=_telegram(client))]
    if client.email:
        lines.append(texts.ADMIN_CLIENT_EMAIL.format(value=safe(client.email)))
    place = [value for value in (client.city, client.workplace_raw) if value]
    if place:
        lines.append(texts.ADMIN_CLIENT_PLACE.format(value=safe(" · ".join(place))))
    if client.specialties:
        lines.append(texts.ADMIN_CLIENT_SPECIALTIES.format(value=safe_join([s.title for s in client.specialties])))
    lines.append(texts.ADMIN_CLIENT_MARKETING.format(
        value=texts.ADMIN_CLIENT_MARKETING_ON if card.marketing else texts.ADMIN_CLIENT_MARKETING_OFF))
    if card.advance > 0:
        lines.append(texts.ADMIN_CLIENT_ADVANCE.format(amount=format_rub(card.advance)))
    lines.append(texts.ADMIN_CLIENT_SINCE.format(date=_local_day(client.created_at, timezone)))
    lines += ["", *_enrollment_lines(card)]
    if card.waiting:
        courses = ", ".join(texts.ADMIN_CLIENT_WAITING_COURSE.format(
            title=safe(short(program.title)), date=format_date(since.astimezone(ZoneInfo(timezone)).date()))
            for program, since in card.waiting)
        lines += ["", texts.ADMIN_CLIENT_WAITING.format(courses=courses)]
    if card.orders:
        # Заказы - по номерам в тексте (правило 26.10.2026): заказ открывают его номером.
        lines += ["", texts.ADMIN_CLIENT_ORDERS]
        lines += [texts.PICK_LINE.format(number=number, line=texts.ADMIN_CLIENT_ORDER.format(
            number=order.number, amount=format_rub(order.total_amount) if order.total_amount is not None else "?",
            status=texts.ADMIN_ORDER_STATUS[order.status.value]))
            for number, order in enumerate(card.orders[:MAX_ORDERS], start=1)]
        lines += ["", texts.ADMIN_CLIENT_ORDERS_HINT]
    return "\n".join(lines), _card_markup(card, back)


def orders_pick(card: AdminClientCard) -> PickList:
    """Номера заказов из карточки клиента: «1» - открыть первый. Строки уже в тексте карточки."""
    return PickList(kind=PICK_ORDER, ids=[order.id for order in card.orders[:MAX_ORDERS]], title="")


def _telegram(client: Client) -> str:
    if client.telegram_id is None:
        return texts.ADMIN_CLIENT_TG_NONE
    if client.is_bot_blocked:
        return texts.ADMIN_CLIENT_TG_BLOCKED
    return f"@{safe(client.tg_username)}" if client.tg_username else texts.ADMIN_CLIENT_TG_NO_USERNAME


def _enrollment_lines(card: AdminClientCard) -> list[str]:
    if not card.enrollments:
        return [texts.ADMIN_CLIENT_NO_ENROLLMENTS]
    lines = [texts.ADMIN_CLIENT_ENROLLMENTS]
    for enrollment, course_session, program, _, order in card.enrollments[:MAX_ENROLLMENTS]:
        lines.append(texts.ADMIN_CLIENT_ENROLLMENT.format(
            title=safe(short(course_session.title_override or program.title)), dates=dates_text(course_session),
            status=texts.ENROLLMENT_STATUS_LABELS[enrollment.status.value],
            order=texts.ADMIN_CLIENT_ENROLLMENT_ORDER.format(number=order.number) if order else "",
        ))
    if len(card.enrollments) > MAX_ENROLLMENTS:
        lines.append(texts.ADMIN_CLIENT_ENROLLMENTS_MORE.format(count=len(card.enrollments) - MAX_ENROLLMENTS))
    return lines


def _card_markup(card: AdminClientCard, back: str) -> InlineKeyboardMarkup:
    client_id = card.client.id
    rows = []
    rows.append([
        InlineKeyboardButton(text=texts.BTN_ADMIN_CLIENT_WRITE, callback_data=_cb(ClientAdminAction.WRITE, client_id,
                                                                                  back)),
        InlineKeyboardButton(text=texts.BTN_ADMIN_CLIENT_ADVANCE,
                             callback_data=_cb(ClientAdminAction.ADVANCE, client_id, back)),
    ])
    rows.append(back_button(back))
    return _markup(*rows)


def to_client(client_id: int, back: str) -> InlineKeyboardMarkup:
    return _markup(_button(texts.BTN_ADMIN_TO_CLIENT, ClientAdminAction.VIEW, client_id, back))


def _cancel_to_client(client_id: int, back: str) -> list[InlineKeyboardButton]:
    return _button(texts.BTN_ADMIN_CANCEL, ClientAdminAction.CANCEL, client_id, back)


# --- Сообщение клиенту и правка аванса -------------------------------------------------------------------------

def ask_message(client: Client, back: str) -> Screen:
    return (texts.ADMIN_CLIENT_ASK_MESSAGE.format(name=safe(client.full_name)),
            _markup(_cancel_to_client(client.id, back)))


def preview_buttons(client_id: int, token: str, back: str) -> InlineKeyboardMarkup:
    """Под предпросмотром. token в кнопке: «Отправить» под старым предпросмотром не отправит новый черновик."""
    return _markup([
        InlineKeyboardButton(text=texts.BTN_ADMIN_SEND, callback_data=_cb(ClientAdminAction.SEND, client_id, token)),
        InlineKeyboardButton(text=texts.BTN_ADMIN_CANCEL, callback_data=_cb(ClientAdminAction.CANCEL, client_id, back)),
    ])


def ask_advance(client: Client, balance: int, back: str) -> Screen:
    text = texts.ADMIN_CLIENT_ASK_ADVANCE.format(name=safe(client.full_name), balance=format_rub(balance))
    return text, _markup(_cancel_to_client(client.id, back))


def advance_confirm(client: Client, change: AdvanceInput, balance: int, token: str, back: str) -> Screen:
    action = texts.ADMIN_CLIENT_ADVANCE_ADD if change.amount > 0 else texts.ADMIN_CLIENT_ADVANCE_TAKE
    text = texts.ADMIN_CLIENT_ADVANCE_CONFIRM.format(
        action=action, amount=format_rub(abs(change.amount)), name=safe(_short(client)), reason=safe(change.reason),
        balance=format_rub(balance + change.amount),
    )
    return text, _markup([
        InlineKeyboardButton(text=texts.BTN_ADMIN_YES, callback_data=_cb(ClientAdminAction.ADVANCE_OK, client.id,
                                                                         token)),
        InlineKeyboardButton(text=texts.BTN_ADMIN_CANCEL, callback_data=_cb(ClientAdminAction.CANCEL, client.id, back)),
    ])


# --- Участники проведения --------------------------------------------------------------------------------------

def _person_line(row) -> str:
    """«✅ Петров П. П. · №100001» - участник в пронумерованном списке. HTML."""
    enrollment, client, order = row
    return texts.ADMIN_PARTICIPANT_LINE.format(
        mark=texts.ADMIN_PARTICIPANT_MARKS[enrollment.status.value], name=safe(_short(client)),
        order=texts.ADMIN_CLIENT_ENROLLMENT_ORDER.format(number=order.number) if order else "",
    )


def participants(view: SessionParticipants) -> PickList:
    """Участники проведения - пронумерованным списком: карточку клиента открывают номером. Отменившие -
    отдельной кнопкой (решение 21.10.2026)."""
    course_session = view.session
    seats = ""
    if view.seats_left is not None:
        seats = texts.ADMIN_PARTICIPANTS_SEATS.format(left=view.seats_left, capacity=course_session.capacity)
    header = texts.ADMIN_PARTICIPANTS.format(
        title=safe(short(session_title(course_session))), dates=dates_text(course_session),
        confirmed=len(view.confirmed), awaiting=len(view.awaiting), waitlist=len(view.waitlist), seats=seats,
    )
    buttons = []
    if view.cancelled:
        buttons.append(_cb_row(texts.BTN_ADMIN_CANCELLED_PARTICIPANTS.format(count=len(view.cancelled)),
                               ClientAdminAction.CANCELLED, course_session.id))
    buttons.append([_to_session(course_session.id)])
    return PickList(kind=PICK_CLIENT, ids=[client.id for _, client, _ in view.active], title=header,
                    lines=[_person_line(row) for row in view.active], empty=header + texts.ADMIN_PARTICIPANTS_EMPTY,
                    buttons=buttons, context={"back": f"{BACK_PARTICIPANTS}{course_session.id}"})


def cancelled_participants(view: SessionParticipants) -> PickList:
    course_session = view.session
    header = texts.ADMIN_CANCELLED_PARTICIPANTS.format(
        title=safe(short(session_title(course_session))), dates=dates_text(course_session), count=len(view.cancelled),
    )
    return PickList(kind=PICK_CLIENT, ids=[client.id for _, client, _ in view.cancelled], title=header,
                    lines=[_person_line(row) for row in view.cancelled], empty=header,
                    buttons=[_cb_row(texts.BTN_ADMIN_TO_PARTICIPANTS, ClientAdminAction.PARTICIPANTS,
                                     course_session.id)],
                    context={"back": f"{BACK_CANCELLED}{course_session.id}"})


def _to_session(session_id: int) -> tuple[str, str]:
    return texts.BTN_ADMIN_TO_SESSION, DictCb(kind=DictKind.SESSIONS, action=DictAction.VIEW, item_id=session_id).pack()


# --- Листы ожидания курсов -------------------------------------------------------------------------------------

def waitlists(rows: list[tuple[Program, int]]) -> PickList:
    """Курсы, где кто-то ждёт нового проведения, - по номерам: лист ожидания открывают номером курса."""
    return PickList(kind=PICK_WAITLIST, ids=[program.id for program, _ in rows], title=texts.ADMIN_WAITLISTS,
                    lines=[texts.ADMIN_WAITLIST_ROW.format(title=safe(program.title), count=count)
                           for program, count in rows],
                    empty=texts.ADMIN_WAITLISTS_EMPTY,
                    buttons=[_cb_row(texts.BTN_ADMIN_TO_CLIENTS, ClientAdminAction.MENU)])


def course_waitlist(program: Program, rows: list[tuple[Client, datetime]], timezone: str,
                    from_course: bool) -> PickList:
    """Кто ждёт нового проведения курса - по номерам: карточку клиента открывают номером."""
    title = safe(short(program.title))
    buttons = []
    if rows:
        buttons.append(_cb_row(texts.BTN_ADMIN_INVITED, ClientAdminAction.INVITED, program.id,
                               FROM_COURSE if from_course else ""))
    buttons.append([_to_course(program.id)] if from_course
                   else _cb_row(texts.BTN_ADMIN_TO_WAITLISTS, ClientAdminAction.WAITLISTS))
    return PickList(
        kind=PICK_CLIENT, ids=[client.id for client, _ in rows],
        title=texts.ADMIN_COURSE_WAITLIST.format(title=title, count=len(rows)),
        lines=[texts.ADMIN_WAITLIST_CLIENT.format(name=safe(_short(client)),
                                                  date=format_date(since.astimezone(ZoneInfo(timezone)).date()))
               for client, since in rows],
        hint=texts.ADMIN_COURSE_WAITLIST_NOTE + texts.PICK_HINT,
        empty=texts.ADMIN_COURSE_WAITLIST_EMPTY.format(title=title), buttons=buttons,
        context={"back": f"{BACK_WAITLIST}{program.id}"},
    )


def invited_confirm(program: Program, count: int, from_course: bool) -> Screen:
    text = texts.ADMIN_INVITED_CONFIRM.format(count=count, title=safe(short(program.title)))
    value = FROM_COURSE if from_course else ""
    return text, _markup([
        InlineKeyboardButton(text=texts.BTN_ADMIN_YES, callback_data=_cb(ClientAdminAction.INVITED_OK, program.id,
                                                                         value)),
        InlineKeyboardButton(text=texts.BTN_ADMIN_CANCEL, callback_data=_cb(ClientAdminAction.WAITLIST, program.id,
                                                                            value)),
    ])


def _to_course(program_id: int) -> tuple[str, str]:
    return texts.BTN_ADMIN_TO_COURSE, DictCb(kind=DictKind.COURSES, action=DictAction.VIEW, item_id=program_id).pack()
