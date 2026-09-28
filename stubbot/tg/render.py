"""Тексты карточек расписания и заявок из моделей. Пользовательские и админские строки — через safe()."""

from html import escape

from stubbot.db.models import CourseSession, Enrollment, Lecturer, PriceOption, Program, Venue
from stubbot.services.enrollments import CLIENT_CANCELLABLE
from stubbot.services.schedule import SessionCard
from stubbot.tg import texts
from stubbot.tg.formatting import safe
from stubbot.utils.dates import format_day, format_range, format_short, format_time_range
from stubbot.utils.money import format_rub
from stubbot.utils.names import full_name

MAX_BUTTON_TITLE = 40


def session_title(course_session: CourseSession) -> str:
    return course_session.title_override or course_session.program.title


def short(title: str, limit: int = MAX_BUTTON_TITLE) -> str:
    return title if len(title) <= limit else title[: limit - 1].rstrip() + "…"


def session_button(course_session: CourseSession) -> str:
    """«12–13 окт · Имплантация…» для кнопки списка (без HTML)."""
    dates = format_range(course_session.start_date, course_session.end_date)
    return f"🗓 {dates} · {short(session_title(course_session), 32)}"


def price_label(price: PriceOption) -> str:
    return f"{price.label} — {format_rub(price.amount)}{texts.PRICE_UNIT_SUFFIX[price.unit.value]}"


def _lecturer_line(lecturer: Lecturer) -> str:
    name = safe(full_name(lecturer.last_name, lecturer.first_name, lecturer.middle_name))
    return f"• {name}" + (f" — <i>{safe(lecturer.regalia)}</i>" if lecturer.regalia else "")


def _venue_text(venue: Venue) -> str:
    text = f"{safe(venue.name)}, {safe(venue.address)}"
    if venue.map_url:
        text += f' (<a href="{escape(venue.map_url, quote=True)}">карта</a>)'
    return text


def session_card(card: SessionCard, deadline_text: str | None) -> str:
    s, program = card.session, card.program
    lines = [texts.SESSION_CARD_TITLE.format(title=safe(session_title(s)))]
    if program.short_description:
        lines.append(f"<i>{safe(program.short_description)}</i>")
    lines += ["", texts.SESSION_STATUS_LABELS[s.status.value], "", texts.SESSION_CARD_DATES]

    for day in s.days:
        parts = [f"<b>{format_day(day.date)}</b>"]
        if time_text := format_time_range(day.start_time, day.end_time):
            parts.append(time_text)
        line = texts.SESSION_CARD_DAY.format(day=", ".join(parts))
        if day.topic:
            line += f" — {safe(day.topic)}"
        if day.venue and day.venue.id != s.venue_id:
            line += f"\n   📍 {_venue_text(day.venue)}"
        lines.append(line)
    if not s.days:
        lines.append(texts.SESSION_CARD_DAY.format(day=format_range(s.start_date, s.end_date)))

    lines.append("")
    if s.venue:
        lines.append(texts.SESSION_CARD_VENUE.format(venue=_venue_text(s.venue)))
    elif s.online_url or s.format.value == "online":
        lines.append(texts.SESSION_CARD_ONLINE)
    lines.append(texts.SESSION_CARD_FORMAT.format(format=texts.FORMAT_LABELS[s.format.value]))
    if program.level:
        lines.append(texts.SESSION_CARD_LEVEL.format(level=texts.LEVEL_LABELS[program.level.value]))
    if program.duration_hours:
        lines.append(texts.SESSION_CARD_HOURS.format(hours=program.duration_hours))
    if program.nmo_points:
        lines.append(texts.SESSION_CARD_NMO.format(points=program.nmo_points))

    if s.lecturers:
        lines += ["", texts.SESSION_CARD_LECTURERS, *(_lecturer_line(lec) for lec in s.lecturers)]

    if card.active_prices:
        lines += ["", texts.SESSION_CARD_PRICES, *(f"• {safe(price_label(p))}" for p in card.active_prices)]

    footer = []
    if card.seats_left is not None and card.accepts_applications:
        footer.append(texts.SESSION_CARD_SEATS.format(seats=max(card.seats_left, 0)))
    if deadline_text:
        footer.append(texts.SESSION_CARD_DEADLINE.format(deadline=deadline_text))
    if footer:
        lines += ["", *footer]
    return "\n".join(lines)


def program_card(program: Program, sessions: list[CourseSession]) -> str:
    lines = [texts.SESSION_CARD_TITLE.format(title=safe(program.title))]
    if program.short_description:
        lines.append(f"<i>{safe(program.short_description)}</i>")
    if program.description_html:
        # description_html заполняет админ в HTML-разметке Telegram — не экранируем.
        lines += ["", program.description_html]
    meta = []
    if program.level:
        meta.append(texts.SESSION_CARD_LEVEL.format(level=texts.LEVEL_LABELS[program.level.value]))
    if program.duration_hours:
        meta.append(texts.SESSION_CARD_HOURS.format(hours=program.duration_hours))
    if program.nmo_points:
        meta.append(texts.SESSION_CARD_NMO.format(points=program.nmo_points))
    if meta:
        lines += ["", *meta]
    lines += ["", texts.PROGRAM_CARD_SESSIONS if sessions else texts.PROGRAM_CARD_NO_SESSIONS]
    return "\n".join(lines)


def my_applications(rows: list[tuple[Enrollment, CourseSession, Program]]) -> tuple[str, list[tuple[int, str]]]:
    """Текст списка заявок и кнопки отмены для тех, что клиент может отменить сам."""
    text = texts.MY_APPLICATIONS_TITLE
    cancellable = []
    for enrollment, course_session, program in rows:
        title = course_session.title_override or program.title
        text += texts.MY_APPLICATION_ITEM.format(
            title=safe(title),
            dates=format_range(course_session.start_date, course_session.end_date),
            status=texts.ENROLLMENT_STATUS_LABELS[enrollment.status.value],
        )
        if enrollment.requested_seats > 1:
            text += texts.MY_APPLICATION_SEATS.format(seats=enrollment.requested_seats)
        if enrollment.status in CLIENT_CANCELLABLE:
            cancellable.append((enrollment.id, texts.BTN_CANCEL_APPLICATION.format(
                title=short(title, 24), date=format_short(course_session.start_date))))
    if cancellable:
        text += texts.MY_APPLICATIONS_CANCEL_HINT
    return text, cancellable


def split_html(text: str, limit: int = 4000) -> list[str]:
    """Длинный текст (программа курса) на части по абзацам — лимит сообщения Telegram 4096."""
    chunks, current = [], ""
    for paragraph in text.split("\n\n"):
        candidate = f"{current}\n\n{paragraph}" if current else paragraph
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
        while len(paragraph) > limit:
            chunks.append(paragraph[:limit])
            paragraph = paragraph[limit:]
        current = paragraph
    if current:
        chunks.append(current)
    return chunks
