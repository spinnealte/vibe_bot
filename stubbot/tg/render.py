"""Тексты карточек расписания и заявок из моделей. Пользовательские и админские строки — через safe()."""

import re
from html import escape, unescape

from stubbot.db.models import CourseSession, Enrollment, Lecturer, PriceOption, Program, Venue
from stubbot.services.enrollments import CLIENT_CANCELLABLE
from stubbot.services.schedule import SessionCard
from stubbot.tg import texts
from stubbot.tg.formatting import safe
from stubbot.utils.dates import format_day, format_range, format_range_with_year, format_short, format_time_range
from stubbot.utils.money import format_rub
from stubbot.utils.names import full_name

MAX_BUTTON_TITLE = 40
CAPTION_LIMIT = 1024  # лимит подписи к фото в Telegram (считается видимый текст, без HTML-тегов)
MIN_DESCRIPTION = 80  # если под описание остаётся меньше — не показываем обрывок
_TAG = re.compile(r"<[^>]+>")


def session_title(course_session: CourseSession) -> str:
    return course_session.title_override or course_session.program.title


def short(title: str, limit: int = MAX_BUTTON_TITLE) -> str:
    return title if len(title) <= limit else title[: limit - 1].rstrip() + "…"


def visible_length(html_text: str) -> int:
    """Длина так, как её считает Telegram: без тегов, с раскрытыми &lt; &amp;."""
    return len(unescape(_TAG.sub("", html_text)))


def _plain_excerpt(html_text: str, limit: int) -> str:
    """Начало описания без разметки, по границе слова (обрезать HTML посередине тега нельзя — было в старом боте)."""
    plain = " ".join(unescape(_TAG.sub(" ", html_text)).split())
    if len(plain) <= limit:
        return escape(plain, quote=False)
    cut = plain[: limit - 1].rsplit(" ", 1)[0]
    return escape(cut, quote=False) + "…"


def _with_description(lines: list[str], description_html: str | None) -> str:
    caption = "\n".join(lines)
    if not description_html:
        return caption
    header = f"\n\n{texts.CAPTION_DESCRIPTION}\n"
    budget = CAPTION_LIMIT - visible_length(caption) - visible_length(header)
    if budget < MIN_DESCRIPTION:
        return caption
    return caption + header + _plain_excerpt(description_html, budget)


def _short_person(lecturer: Lecturer) -> str:
    """Иванов С. П."""
    initials = " ".join(f"{part[0]}." for part in (lecturer.first_name, lecturer.middle_name) if part)
    return f"{lecturer.last_name} {initials}".strip()


def _meta_lines(program: Program, format_value: str) -> list[str]:
    lines = []
    if program.specialties:
        lines.append(texts.CAPTION_SPECIALTIES.format(value=safe(" / ".join(s.title for s in program.specialties))))
    lines.append(texts.CAPTION_FORMAT.format(value=texts.FORMAT_BADGES[format_value]))
    if program.level:
        lines.append(texts.CAPTION_LEVEL.format(value=texts.LEVEL_BADGES[program.level.value]))
    volume = [f"{program.duration_hours} ак. ч."] if program.duration_hours else []
    if program.nmo_points:
        volume.append(f"🏅 НМО: {program.nmo_points}")
    if volume:
        lines.append(texts.CAPTION_VOLUME.format(value=" · ".join(volume)))
    return lines


def session_caption(card: SessionCard, deadline_text: str | None) -> str:
    """Подпись под фото потока в карусели: главное сразу, детали — по кнопке «Подробнее»."""
    s, program = card.session, card.program
    lines = [texts.CAPTION_TITLE.format(title=safe(session_title(s)))]
    if program.short_description:
        lines.append(f"<i>{safe(program.short_description)}</i>")
    lines.append("")
    if s.lecturers:
        lines.append(texts.CAPTION_LECTURERS.format(value=safe(", ".join(_short_person(lec) for lec in s.lecturers))))
    lines.append(texts.CAPTION_DATES.format(value=format_range_with_year(s.start_date, s.end_date)))
    if s.venue:
        lines.append(texts.CAPTION_VENUE.format(value=safe(s.venue.name)))
    lines += _meta_lines(program, s.format.value)

    lines += ["", texts.CAPTION_PRICES]
    if card.active_prices:
        lines += [texts.CAPTION_PRICE_LINE.format(
            label=safe(p.label), amount=format_rub(p.amount) + texts.PRICE_UNIT_SUFFIX[p.unit.value],
        ) for p in card.active_prices]
    else:
        lines.append(texts.CAPTION_NO_PRICE)

    status = texts.SESSION_STATUS_LABELS[s.status.value]
    if card.seats_left is not None and card.accepts_applications and not card.goes_to_waitlist:
        status += texts.CAPTION_SEATS_LEFT.format(seats=card.seats_left)
    lines += ["", texts.CAPTION_STATUS.format(value=status)]
    if deadline_text:
        lines.append(texts.CAPTION_DEADLINE.format(value=deadline_text))
    return _with_description(lines, program.description_html)


def program_caption(program: Program) -> str:
    """Подпись под фото курса, у которого пока нет дат (в конце карусели расписания)."""
    lines = [texts.CAPTION_TITLE.format(title=safe(program.title))]
    if program.short_description:
        lines.append(f"<i>{safe(program.short_description)}</i>")
    lines += ["", texts.CAPTION_DATES_TBD, *_meta_lines(program, program.default_format.value),
              "", texts.CAPTION_STATUS_TBD]
    return _with_description(lines, program.description_html)


def program_pages(program: Program, limit: int = 3800) -> list[str]:
    """Программа курса страницами для листания в одном сообщении (лимит сообщения 4096, запас под подвал)."""
    title = f"📖 <b>{safe(short(program.title, 200))}</b>"
    pages = split_html(program.program_html or "", limit=limit) or [texts.PROGRAM_TEXT_EMPTY]
    pages[0] = f"{title}\n\n{pages[0]}"
    if len(pages) > 1:
        pages = [page + texts.PROGRAM_PAGE.format(page=i + 1, pages=len(pages)) for i, page in enumerate(pages)]
    return pages


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


def session_details(card: SessionCard, deadline_text: str | None) -> str:
    """Полная карточка потока (кнопка «Подробнее»): расписание по дням, адрес с картой, регалии лекторов."""
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
