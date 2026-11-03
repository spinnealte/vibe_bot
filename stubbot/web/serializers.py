"""Данные бота → JSON для приложения. Источник тот же, что у бота: карточка проведения (ScheduleService),
«Мои записи» (EnrollmentService), заказ (OrderService). Подписи статусов и форматов - те же, что в боте (tg/texts.py).

Деньги - копейки (amount) и готовая строка (amount_text); даты - ISO и готовая строка по-русски.
"""

from datetime import date, datetime
from typing import Any

from stubbot.db.enums import EnrollmentStatus, OrderStatus, PayerType, PriceUnit, SessionStatus
from stubbot.db.models import Client, CourseSession, Order, PriceOption, Program, Venue
from stubbot.services.enrollments import PAYABLE_ORDER_STATUSES, MyApplication, is_pair_price
from stubbot.services.schedule import SessionCard
from stubbot.tg import texts
from stubbot.tg.render import dates_text, plain_excerpt, session_title
from stubbot.utils.dates import format_date, format_day, format_moment, format_time_range
from stubbot.utils.money import format_rub
from stubbot.utils.names import short_name
from stubbot.utils.profile_fields import experience_years
from stubbot.web.html import to_web_html

COVER_URL = "/api/files/{file_id}"
# Статус оплаты записи - коллеге, которого записали вдвоём (заказ оплачивает записавший).
PAYMENT_LABELS = {"unpaid": "не оплачено", "partial": "оплачено частично", "paid": "оплачено", "refunded": "возвращено"}
DEFAULT_COVER_URL = "/api/files/default"
EXCERPT = 220  # символов программы в списке курсов


def cover_url(*file_ids: str | None) -> str:
    """Обложка: своя у проведения, иначе курса, иначе заглушка (как в афише бота)."""
    file_id = next((f for f in file_ids if f), None)
    return COVER_URL.format(file_id=file_id) if file_id else DEFAULT_COVER_URL


def money(kopecks: int | None) -> dict[str, Any] | None:
    return None if kopecks is None else {"amount": kopecks, "text": format_rub(kopecks)}


def venue(item: Venue | None) -> dict[str, Any] | None:
    if item is None:
        return None
    map_url = item.map_url if item.map_url and item.map_url.startswith(("https://", "http://")) else None
    return {"id": item.id, "name": item.name, "address": item.address, "map_url": map_url,
            "directions_html": to_web_html(item.directions_text)}


def price(option: PriceOption) -> dict[str, Any]:
    return {
        "id": option.id,
        "label": option.label,
        "amount": option.amount,
        "amount_text": format_rub(option.amount) + texts.PRICE_UNIT_SUFFIX[option.unit.value],
        "pair": is_pair_price(option),
        "per_group": option.unit is PriceUnit.PER_GROUP,
    }


def _days(course_session: CourseSession) -> list[dict[str, Any]]:
    return [{
        "date": day.date.isoformat(),
        "label": format_day(day.date),
        "time": format_time_range(day.start_time, day.end_time),
        "topic": day.topic,
        "venue": venue(day.venue) if day.venue_id and day.venue_id != course_session.venue_id else None,
    } for day in course_session.days]


def _lecturers(program: Program) -> list[dict[str, Any]]:
    return [{"id": lec.id, "name": lec.full_name, "short_name": short_name(lec.full_name), "regalia": lec.regalia,
             "bio_html": to_web_html(lec.bio_html),
             "photo_url": COVER_URL.format(file_id=lec.photo_file_id) if lec.photo_file_id else None}
            for lec in program.lecturers]


def course_card(card: SessionCard, timezone: str, applications: bool, mine: dict | None = None) -> dict[str, Any]:
    """Проведение в списке расписания (как слайд карусели бота) - без программы и подробностей."""
    s, program = card.session, card.program
    prices = sorted(card.active_prices, key=lambda p: p.amount)
    status = card.shown_status
    return {
        "id": s.id,
        "program_id": program.id,
        "title": session_title(s),
        "subtitle": program.short_description,
        "cover_url": cover_url(s.cover_file_id, program.cover_file_id),
        "dates": dates_text(s),
        "start_date": s.start_date.isoformat() if s.start_date else None,
        "end_date": s.end_date.isoformat() if s.end_date else None,
        "venue": venue(s.venue),
        "format": s.format.value,
        "format_label": texts.FORMAT_BADGES[s.format.value],
        "level_label": texts.LEVEL_BADGES[program.level.value] if program.level else None,
        "duration_hours": program.duration_hours,
        "specialties": [sp.title for sp in program.specialties],
        "lecturers": [short_name(lec.full_name) for lec in program.lecturers],
        "status": status.value,
        "status_label": texts.SESSION_STATUS_LABELS.get(status.value, ""),
        # Счётчик мест - только когда запись в боте включена (в афише MVP его нет).
        "seats_left": card.seats_left if applications and card.accepts_applications and not card.goes_to_waitlist
        else None,
        "accepts_applications": applications and card.accepts_applications,
        "goes_to_waitlist": card.goes_to_waitlist,
        "announced": s.status is SessionStatus.ANNOUNCED,
        "deadline": format_moment(s.registration_deadline, timezone)
        if s.registration_deadline and card.accepts_applications else None,
        "price_from": money(prices[0].amount) if prices else None,
        "prices_count": len(prices),
        "has_program": bool(program.program_html),
        "excerpt": plain_excerpt(program.program_html or program.description_html or "", EXCERPT) or None,
        "mine": mine,
    }


def course_details(card: SessionCard, timezone: str, applications: bool, mine: dict | None) -> dict[str, Any]:
    """Карточка проведения целиком: дни, лекторы, тарифы, описание."""
    s, program = card.session, card.program
    data = course_card(card, timezone, applications, mine)
    data.update({
        "days": _days(s),
        "lecturers_full": _lecturers(program),
        "prices": [price(p) for p in card.active_prices],
        "description_html": to_web_html(program.description_html),
        "online": s.format.value in ("online", "hybrid"),
    })
    return data


def program_page(program: Program) -> dict[str, Any]:
    return {"id": program.id, "title": program.title, "subtitle": program.short_description,
            "cover_url": cover_url(program.cover_file_id),
            "html": to_web_html(program.program_html or program.description_html)}


def my_enrollment(enrollment_status: EnrollmentStatus) -> dict[str, str]:
    """Моя запись на проведение - метка в расписании («Вы записаны»)."""
    return {"status": enrollment_status.value, "label": texts.ENROLLMENT_STATUS_LABELS[enrollment_status.value]}


def order(item: Order, paid: int, timezone: str, now: datetime) -> dict[str, Any]:
    total = item.total_amount
    left = None if total is None else max(total - paid, 0)
    payable = item.status in PAYABLE_ORDER_STATUSES
    return {
        "id": item.id,
        "number": item.number,
        "status": item.status.value,
        "total": money(total),
        "paid": money(paid) if paid else None,
        "left": money(left) if left is not None else None,
        "payable": payable,
        "payer_type": item.payer_type.value if item.payer_type else None,
        "requisites_received": bool(item.requisites_file_id),
        "requisites_file_name": item.requisites_file_name,
        # Срок оплаты: у заказа без суммы его ещё нет (72 часа пойдут, когда менеджер её назовёт).
        "due": format_moment(item.due_at, timezone) if payable and total is not None and not paid else None,
        "overdue": payable and total is not None and not paid and item.due_at < now,
        "invoice_sent": item.invoice_sent_at is not None,
    }


def application(app: MyApplication, timezone: str, now: datetime, online_payments: bool,
                manager_url: str | None) -> dict[str, Any]:
    """Одна запись в «Моих записях». Заказ и оплату видит только плательщик; коллеге - кто его записал."""
    enrollment, s, item = app.enrollment, app.session, app.order
    show_order = app.is_payer and item is not None and item.status is not OrderStatus.CANCELLED
    order_data = order(item, app.paid, timezone, now) if show_order else None
    payable = bool(order_data and order_data["payable"] and app.active)
    left = order_data["left"]["amount"] if order_data and order_data["left"] else None
    return {
        "id": enrollment.id,
        "session_id": s.id,
        "program_id": app.program.id,
        "title": app.title,
        "cover_url": cover_url(s.cover_file_id, app.program.cover_file_id),
        "dates": dates_text(s),
        "start_date": s.start_date.isoformat() if s.start_date else None,
        "venue": venue(s.venue),
        "price": price(app.price) if app.price else None,
        "status": enrollment.status.value,
        "status_label": texts.ENROLLMENT_STATUS_LABELS[enrollment.status.value],
        "active": app.active,
        "waitlist": enrollment.status is EnrollmentStatus.WAITLIST,
        "is_payer": app.is_payer,
        "partner": app.partner.full_name if app.partner and app.partner.full_name else None,
        "payment_label": PAYMENT_LABELS.get(enrollment.payment_status.value, ""),
        "order": order_data,
        "can_choose_payer": payable,
        "can_send_requisites": payable and item.payer_type is PayerType.ORGANIZATION,
        "can_pay_online": bool(online_payments and payable and item.payer_type is PayerType.INDIVIDUAL
                               and left),
        "can_cancel": app.self_cancel,
        "cancel_until": format_date(app.cancel_until) if app.self_cancel and app.cancel_until else None,
        "paid": app.paid,
        "manager_cancel": bool(app.active and not app.self_cancel and manager_url),
    }


def profile(client: Client, today: date, marketing: bool, advance: int, referrals: int,
            referral_link: str | None) -> dict[str, Any]:
    years = experience_years(client.practice_since_year, today)
    return {
        "id": client.id,
        "full_name": client.full_name,
        "phone": client.phone,
        "email": client.email,
        "birth_date": client.birth_date.strftime("%d.%m.%Y") if client.birth_date else None,
        "city": client.city,
        "workplace": client.workplace_raw,
        "experience_years": years,
        "specialties": [{"id": s.id, "title": s.title} for s in client.specialties],
        "positions": [{"id": p.id, "title": p.title} for p in client.positions],
        "marketing": marketing,
        "advance": money(advance) if advance > 0 else None,
        "referral_link": referral_link,
        "referrals": referrals,
    }
