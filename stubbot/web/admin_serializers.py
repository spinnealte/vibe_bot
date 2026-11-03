"""Админка бота → JSON для приложения. Данные - из тех же сервисов, что у админки в боте (services/admin_*);
подписи статусов, форматов и полей - тексты бота (tg/texts.py) без разметки.

Деньги - копейки и готовая строка (serializers.money); даты - готовой строкой по-русски в часовом поясе центра.
"""

from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from stubbot.db.enums import CANCELLED_ENROLLMENT_STATUSES, InvoiceChannel, OrderStatus
from stubbot.db.models import Client, CourseSession, Enrollment, Order, Program
from stubbot.repositories.admin_sessions import SessionScope
from stubbot.services.admin_clients import AdminClientCard, SessionParticipants
from stubbot.services.admin_dictionaries import DictField, DictionarySpec, FieldKind
from stubbot.services.admin_orders import AdminOrderCard
from stubbot.services.admin_sessions import NO_DATES
from stubbot.tg import texts
from stubbot.tg.render import dates_text, price_label, session_title
from stubbot.utils.dates import format_date, format_month, format_moment, month_key
from stubbot.utils.names import short_name
from stubbot.utils.profile_fields import experience_years
from stubbot.web.html import to_plain
from stubbot.web.serializers import cover_url, money

RICH_KINDS = (FieldKind.HTML, FieldKind.DOCUMENT)
# Раздел проведения в адресах приложения (в кнопках бота - короткие значения SessionScope).
SCOPE_NAMES = {SessionScope.ACTIVE: "active", SessionScope.ARCHIVED: "archived", SessionScope.PAST: "past"}


def _label(text: str) -> str:
    """Подпись бота без значка в начале: «📚 Курсы» → «Курсы» (у приложения свои значки)."""
    head, _, rest = text.partition(" ")
    return rest if rest and not any(ch.isalnum() for ch in head) else text


# --- Описание полей -----------------------------------------------------------------------------------------------

def field_spec(field: DictField) -> dict[str, Any]:
    labels = texts.ADMIN_CHOICE_LABELS.get(field.name, {})
    return {
        "name": field.name,
        "kind": field.kind.value,
        "label": texts.ADMIN_FIELD_LABELS.get(field.name, field.name),
        "required": field.required,
        "editable": field.editable,
        "max_length": field.max_length or None,
        "min": field.min_value if field.kind is FieldKind.NUMBER else None,
        "max": field.max_value if field.kind is FieldKind.NUMBER else None,
        "choices": [{"value": value, "label": _label(to_plain(labels.get(value, value)))} for value in field.choices],
        "when": {"field": field.when[0], "values": list(field.when[1])} if field.when else None,
    }


def spec(item: DictionarySpec) -> dict[str, Any]:
    return {"kind": item.kind.value, "title": _label(texts.ADMIN_DICT_TITLES[item.kind.value]),
            "new_title": texts.ADMIN_DICT_NEW[item.kind.value], "fields": [field_spec(f) for f in item.fields]}


def options(rows: list[tuple[int, str]]) -> list[dict[str, Any]]:
    return [{"id": option_id, "title": title} for option_id, title in rows]


def json_value(field: DictField, value: Any) -> Any:
    """Значение поля для формы: как его хранит сервис (field_value), но только JSON-типы."""
    if isinstance(value, datetime | date):
        return value.isoformat()
    return value


# --- Курсы и проведения в списках ---------------------------------------------------------------------------------

def course_row(program: Program, in_afisha: int, waiting: int, timezone: str) -> dict[str, Any]:
    return {
        "id": program.id,
        "title": program.title,
        "subtitle": program.short_description,
        "cover_url": cover_url(program.cover_file_id),
        "specialties": [s.title for s in program.specialties],
        "lecturers": [short_name(lec.full_name) for lec in program.lecturers],
        "level_label": to_plain(texts.LEVEL_BADGES[program.level.value]) if program.level else None,
        "format_label": to_plain(texts.FORMAT_BADGES[program.default_format.value]),
        "duration_hours": program.duration_hours,
        "has_program": bool(program.program_html),
        "sessions_in_afisha": in_afisha,
        "waiting": waiting,
        "archived_at": local_date(program.archived_at, timezone),
    }


def session_row(item: CourseSession, scope: SessionScope, taken: int, timezone: str) -> dict[str, Any]:
    prices = sorted((p.amount for p in item.price_options if p.is_active))
    key = month_key(item.start_date) if item.start_date else NO_DATES
    return {
        "id": item.id,
        "program_id": item.program_id,
        "title": session_title(item),
        "dates": dates_text(item),
        "start_date": item.start_date.isoformat() if item.start_date else None,
        "month": key,
        "month_label": format_month(key) if key else "Без дат",
        "status": item.status.value,
        "status_label": to_plain(texts.SESSION_STATUS_LABELS.get(item.status.value, "")),
        "format_label": to_plain(texts.FORMAT_BADGES[item.format.value]),
        "venue": item.venue.name if item.venue else None,
        "cover_url": cover_url(item.cover_file_id, item.program.cover_file_id),
        "capacity": item.capacity,
        "taken": taken,
        "price_from": money(prices[0]) if prices else None,
        "scope": SCOPE_NAMES[scope],
        "program_archived": item.program.archived_at is not None,
        "archived_at": local_date(item.archived_at, timezone),
    }


def local_date(moment: datetime | None, timezone: str) -> str | None:
    return format_date(moment.astimezone(ZoneInfo(timezone)).date()) if moment else None


# --- Заказы -------------------------------------------------------------------------------------------------------

def _person(client: Client) -> dict[str, Any]:
    return {"id": client.id, "name": client.full_name or texts.NOT_SET, "short_name":
            short_name(client.full_name) if client.full_name else texts.NOT_SET,
            "phone": client.phone, "in_bot": client.telegram_id is not None, "username": client.tg_username,
            "blocked": client.is_bot_blocked}


def order_row(order: Order, payer: Client, timezone: str, now: datetime) -> dict[str, Any]:
    return {
        "id": order.id,
        "number": order.number,
        "payer": payer.full_name or texts.NOT_SET,
        "payer_short": short_name(payer.full_name) if payer.full_name else texts.NOT_SET,
        "total": money(order.total_amount),
        "status": order.status.value,
        "payer_type": order.payer_type.value if order.payer_type else None,
        "requisites": bool(order.requisites_file_id),
        "invoice_sent": format_moment(order.invoice_sent_at, timezone) if order.invoice_sent_at else None,
        "due": format_moment(order.due_at, timezone) if order.total_amount is not None else None,
        "overdue": (order.status is OrderStatus.AWAITING_PAYMENT and order.total_amount is not None
                    and order.due_at < now),
        "created": format_moment(order.created_at, timezone),
    }


def order_card(card: AdminOrderCard, timezone: str, now: datetime) -> dict[str, Any]:
    order, payable = card.order, card.payable
    cancelled = order.status is OrderStatus.CANCELLED
    category = card.category(now)
    return {
        "id": order.id,
        "number": order.number,
        "status": order.status.value,
        "status_label": to_plain(texts.ADMIN_ORDER_STATUS[order.status.value]),
        "session": {"id": card.session.id, "program_id": card.session.program_id,
                    "title": session_title(card.session), "dates": dates_text(card.session)},
        "payer": _person(card.payer),
        "participants": [{**_person(person), "status": row.status.value,
                          "status_label": to_plain(texts.ENROLLMENT_STATUS_LABELS[row.status.value])}
                         for row, person in card.participants],
        "price_label": price_label(card.price) if card.price else None,
        "total": money(order.total_amount),
        "paid": money(card.paid) if card.paid else None,
        "left": money(card.left) if card.left is not None else None,
        "due": format_moment(order.due_at, timezone) if payable and order.total_amount is not None else None,
        "overdue": card.overdue(now),
        "payer_type": order.payer_type.value if order.payer_type else None,
        "requisites": {"name": order.requisites_file_name} if order.requisites_file_id else None,
        "invoice": {"sent_at": format_moment(order.invoice_sent_at, timezone),
                    "manual": order.invoice_channel is InvoiceChannel.MANUAL} if order.invoice_sent_at else None,
        "online_pending": {"amount": money(card.online_pending.amount),
                           "created": format_moment(card.online_pending.created_at, timezone)}
        if card.online_pending is not None else None,
        "advance": money(card.advance) if card.advance > 0 else None,
        "created": format_moment(order.created_at, timezone),
        "cancelled": {
            "at": format_moment(order.cancelled_at, timezone) if order.cancelled_at else None,
            "reason": order.cancel_reason,
            "choice": to_plain(texts.CANCEL_CHOICE_LABELS[order.cancel_choice.value]) if order.cancel_choice else None,
        } if cancelled else None,
        "money_pending": money(card.to_settle) if card.money_pending else None,
        "money_settled": cancelled and bool(card.paid) and not card.money_pending,
        "category": category.value if category else None,
        "actions": {
            "invoice": payable,
            "invoice_manual": payable and order.invoice_sent_at is None,
            "invoice_undo": payable and order.invoice_channel is InvoiceChannel.MANUAL,
            "total": payable and order.total_amount is None,
            "pay": payable,
            "apply_advance": money(card.advance_to_apply) if card.advance_to_apply else None,
            "settle": card.money_pending,
            "remove": not cancelled,
            "requisites": bool(order.requisites_file_id),
        },
    }


# --- Клиенты ------------------------------------------------------------------------------------------------------

def client_row(client: Client) -> dict[str, Any]:
    # Без ФИО - человек открыл бота или приложение, но не дал согласие: имя из Telegram до согласия не храним.
    return {"id": client.id, "name": client.full_name or client.tg_first_name or "Без имени",
            "phone_tail": (client.phone or "")[-4:] or None, "in_bot": client.telegram_id is not None,
            "username": client.tg_username}


def client_card(card: AdminClientCard, timezone: str, today: date) -> dict[str, Any]:
    client = card.client
    return {
        "id": client.id,
        "full_name": client.full_name,
        "telegram_name": " ".join(filter(None, (client.tg_first_name, client.tg_last_name))) or None,
        "phone": client.phone,
        "email": client.email,
        "city": client.city,
        "workplace": client.workplace_raw,
        "experience_years": experience_years(client.practice_since_year, today),
        "birth_date": client.birth_date.strftime("%d.%m.%Y") if client.birth_date else None,
        "specialties": [s.title for s in client.specialties],
        "username": client.tg_username,
        "in_bot": client.telegram_id is not None,
        "blocked": client.is_bot_blocked,
        "marketing": card.marketing,
        "advance": money(card.advance),
        "since": local_date(client.created_at, timezone),
        "last_seen": format_moment(client.last_seen_at, timezone) if client.last_seen_at else None,
        "enrollments": [{
            "id": enrollment.id,
            "session_id": course_session.id,
            "title": course_session.title_override or program.title,
            "dates": dates_text(course_session),
            "status": enrollment.status.value,
            "status_label": to_plain(texts.ENROLLMENT_STATUS_LABELS[enrollment.status.value]),
            "cancelled": enrollment.status in CANCELLED_ENROLLMENT_STATUSES,
            "order": {"id": order.id, "number": order.number} if order else None,
        } for enrollment, course_session, program, _, order in card.enrollments],
        "orders": [{"id": order.id, "number": order.number, "total": money(order.total_amount),
                    "status": order.status.value,
                    "status_label": to_plain(texts.ADMIN_ORDER_STATUS[order.status.value])} for order in card.orders],
        "waiting": [{"program_id": program.id, "title": program.title, "since": local_date(since, timezone)}
                    for program, since in card.waiting],
    }


def participants(view: SessionParticipants) -> dict[str, Any]:
    def rows(found: list[tuple[Enrollment, Client, Order | None]]) -> list[dict[str, Any]]:
        return [{**client_row(client), "enrollment_id": enrollment.id, "status": enrollment.status.value,
                 "status_label": to_plain(texts.ENROLLMENT_STATUS_LABELS[enrollment.status.value]),
                 "seats": enrollment.requested_seats,
                 "order": {"id": order.id, "number": order.number} if order else None}
                for enrollment, client, order in found]

    course_session = view.session
    return {
        "session": {"id": course_session.id, "title": session_title(course_session),
                    "dates": dates_text(course_session), "capacity": course_session.capacity},
        "seats_left": view.seats_left,
        "confirmed": rows(view.confirmed),
        "awaiting": rows(view.awaiting),
        "waitlist": rows(view.waitlist),
        "cancelled": rows(view.cancelled),
    }
