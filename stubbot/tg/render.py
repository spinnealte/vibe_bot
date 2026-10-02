"""Тексты карточек расписания и заявок из моделей. Пользовательские и админские строки — через safe()."""

import re
from html import escape, unescape

from stubbot.db.enums import EnrollmentStatus
from stubbot.db.models import CourseSession, PriceOption, Program, Venue
from stubbot.services.enrollments import MyApplication
from stubbot.services.schedule import SessionCard
from stubbot.tg import texts
from stubbot.tg.formatting import safe
from stubbot.utils.dates import format_range_with_year, format_time_range
from stubbot.utils.money import format_rub
from stubbot.utils.names import short_name

MAX_BUTTON_TITLE = 40
CAPTION_LIMIT = 1024  # лимит подписи к фото в Telegram (считается видимый текст, без HTML-тегов)
MIN_DESCRIPTION = 80  # если под описание остаётся меньше — не показываем обрывок
EXCERPT_LIMIT = 400  # сколько программы/описания показывать в карточке; полная программа — по кнопке
PROGRAM_PAGE_LIMIT = 900  # страница полной программы ≈ один экран телефона
_TAG = re.compile(r"<[^>]+>")


def session_title(course_session: CourseSession) -> str:
    return course_session.title_override or course_session.program.title


def short(title: str, limit: int = MAX_BUTTON_TITLE) -> str:
    return title if len(title) <= limit else title[: limit - 1].rstrip() + "…"


def visible_length(html_text: str) -> int:
    """Длина так, как её считает Telegram: без тегов, с раскрытыми &lt; &amp;."""
    return len(unescape(_TAG.sub("", html_text)))


def plain_excerpt(html_text: str, limit: int) -> str:
    """Начало текста без разметки, по границе слова, с сохранением строк (пунктов программы).

    Разметку снимаем: обрезать HTML посередине тега нельзя — так ломалась подпись в старом боте.
    """
    lines = (" ".join(line.split()) for line in unescape(_TAG.sub("", html_text)).splitlines())
    plain = "\n".join(line for line in lines if line)
    if len(plain) <= limit:
        return escape(plain, quote=False)
    cut = plain[: limit - 1]
    boundary = max(cut.rfind(" "), cut.rfind("\n"))
    if boundary > 0:
        cut = cut[:boundary]
    return escape(cut.rstrip(), quote=False) + "…"


def _with_excerpt(lines: list[str], program: Program) -> str:
    """В конец подписи — начало программы курса (или описания, если программы нет), до EXCERPT_LIMIT символов
    и в пределах лимита подписи. Полная программа — по кнопке «📖 Программа» с листанием."""
    caption = "\n".join(lines)
    source, header = (
        (program.program_html, texts.CAPTION_PROGRAM) if program.program_html
        else (program.description_html, texts.CAPTION_DESCRIPTION)
    )
    if not source:
        return caption
    header = f"\n\n{header}\n"
    more = f"\n{texts.CAPTION_PROGRAM_MORE}" if program.program_html else ""
    budget = min(EXCERPT_LIMIT, CAPTION_LIMIT - visible_length(caption + header + more))
    if budget < MIN_DESCRIPTION:
        return caption
    excerpt = plain_excerpt(source, budget)
    if not excerpt.endswith("…"):
        more = ""  # программа короткая и показана целиком — подсказка про кнопку не нужна
    return caption + header + excerpt + more


def _lecturers_line(program: Program) -> list[str]:
    """Лекторы курса «Иванов С. П., Смирнова А. О.» — у курса, общие для всех его потоков."""
    if not program.lecturers:
        return []
    return [texts.CAPTION_LECTURERS.format(value=safe(", ".join(short_name(lec.full_name) for lec in program.lecturers)))]


def _meta_lines(program: Program, format_value: str) -> list[str]:
    lines = []
    if program.specialties:
        lines.append(texts.CAPTION_SPECIALTIES.format(value=safe(" / ".join(s.title for s in program.specialties))))
    lines.append(texts.CAPTION_FORMAT.format(value=texts.FORMAT_BADGES[format_value]))
    if program.level:
        lines.append(texts.CAPTION_LEVEL.format(value=texts.LEVEL_BADGES[program.level.value]))
    # Баллы НМО не показываем (решение 02.10.2026): колонка programs.nmo_points в БД остаётся на будущее.
    if program.duration_hours:
        lines.append(texts.CAPTION_VOLUME.format(value=f"{program.duration_hours} ак. ч."))
    return lines


def _dates_text(course_session: CourseSession) -> str:
    """13–14 октября 2026, 10:00–18:00 — время добавляем, если во все дни одинаковое."""
    text = format_range_with_year(course_session.start_date, course_session.end_date)
    times = {format_time_range(day.start_time, day.end_time) for day in course_session.days}
    if len(times) == 1 and (time_text := times.pop()):
        text += f", {time_text}"
    return text


def session_caption(card: SessionCard, deadline_text: str | None, show_seats: bool = True) -> str:
    """Подпись под фото потока в карусели: всё главное сразу, полная программа — по кнопке «Программа».

    show_seats=False — режим афиши (заявки выключены): мест никто не занимает, счётчик всегда равен вместимости.
    """
    s, program = card.session, card.program
    lines = [texts.CAPTION_TITLE.format(title=safe(session_title(s)))]
    if program.short_description:
        lines.append(f"<i>{safe(program.short_description)}</i>")
    lines.append("")
    lines += _lecturers_line(program)
    lines.append(texts.CAPTION_DATES.format(value=_dates_text(s)))
    if s.venue:
        lines.append(texts.CAPTION_VENUE.format(value=f"{safe(s.venue.name)}, {safe(s.venue.address)}"))
    lines += _meta_lines(program, s.format.value)

    lines += ["", texts.CAPTION_PRICES]
    if card.active_prices:
        lines += [texts.CAPTION_PRICE_LINE.format(
            label=safe(p.label), amount=format_rub(p.amount) + texts.PRICE_UNIT_SUFFIX[p.unit.value],
        ) for p in card.active_prices]
    else:
        lines.append(texts.CAPTION_NO_PRICE)

    status = texts.SESSION_STATUS_LABELS[s.status.value]
    if show_seats and card.seats_left is not None and card.accepts_applications and not card.goes_to_waitlist:
        status += texts.CAPTION_SEATS_LEFT.format(seats=card.seats_left)
    lines += ["", texts.CAPTION_STATUS.format(value=status)]
    if deadline_text:
        lines.append(texts.CAPTION_DEADLINE.format(value=deadline_text))
    return _with_excerpt(lines, program)


def program_caption(program: Program) -> str:
    """Подпись под фото курса, у которого пока нет дат (в конце карусели расписания)."""
    lines = [texts.CAPTION_TITLE.format(title=safe(program.title))]
    if program.short_description:
        lines.append(f"<i>{safe(program.short_description)}</i>")
    lines += ["", *_lecturers_line(program), texts.CAPTION_DATES_TBD, *_meta_lines(program, program.default_format.value),
              "", texts.CAPTION_STATUS_TBD]
    return _with_excerpt(lines, program)


def program_pages(program: Program, limit: int = PROGRAM_PAGE_LIMIT) -> list[str]:
    """Программа курса страницами «на один экран телефона» для листания в одном сообщении."""
    title = f"📖 <b>{safe(short(program.title, 200))}</b>"
    # Заголовок стоит на первой странице — оставляем под него место, чтобы и она влезала в экран.
    pages = split_html(program.program_html or "", limit=limit - visible_length(title) - 2) or [texts.PROGRAM_TEXT_EMPTY]
    pages[0] = f"{title}\n\n{pages[0]}"
    if len(pages) > 1:
        pages = [page + texts.PROGRAM_PAGE.format(page=i + 1, pages=len(pages)) for i, page in enumerate(pages)]
    return pages


def manager_draft(program: Program, card: SessionCard | None) -> str:
    """Черновик сообщения менеджеру по курсу на слайде (простой текст, без HTML)."""
    if card is None:
        return texts.MANAGER_DRAFT_PROGRAM.format(title=program.title)
    s = card.session
    return texts.MANAGER_DRAFT_SESSION.format(title=session_title(s),
                                              dates=format_range_with_year(s.start_date, s.end_date))


def about_text(venues: list[Venue], with_manager: bool) -> str:
    """«О центре»: текст о центре, площадки из БД (с картой, если есть ссылка), подсказка про кнопку «Менеджер»."""
    parts = [texts.ABOUT]
    if venues:
        lines = [texts.ABOUT_VENUES_TITLE]
        for venue in venues:
            line = texts.ABOUT_VENUE.format(name=safe(venue.name), address=safe(venue.address))
            if venue.map_url and venue.map_url.startswith(("https://", "http://")):
                line += texts.ABOUT_VENUE_MAP.format(url=escape(venue.map_url, quote=True))
            lines.append(line)
        parts.append("\n".join(lines))
    if with_manager:
        parts.append(texts.ABOUT_MANAGER_HINT)
    return "\n\n".join(parts)


def price_label(price: PriceOption) -> str:
    return f"{price.label} — {format_rub(price.amount)}{texts.PRICE_UNIT_SUFFIX[price.unit.value]}"


def my_application_card(app: MyApplication) -> str:
    """Карточка одной заявки в «Моих заявках» (листаются стрелками)."""
    enrollment, s = app.enrollment, app.session
    lines = [texts.MY_APPLICATIONS_TITLE, "", texts.MY_APPLICATION_COURSE.format(value=safe(app.title)),
             texts.MY_APPLICATION_DATES.format(value=_dates_text(s))]
    if s.venue:
        lines.append(texts.MY_APPLICATION_VENUE.format(value=f"{safe(s.venue.name)}, {safe(s.venue.address)}"))
    if app.price:
        lines.append(texts.MY_APPLICATION_PRICE.format(value=safe(price_label(app.price))))
    lines.append(texts.MY_APPLICATION_SEATS.format(value=enrollment.requested_seats))
    if enrollment.client_comment:
        lines.append(texts.MY_APPLICATION_COMMENT.format(value=safe(enrollment.client_comment)))
    lines += ["", texts.MY_APPLICATION_STATUS.format(value=texts.ENROLLMENT_STATUS_LABELS[enrollment.status.value])]
    if enrollment.status is EnrollmentStatus.CONFIRMED:
        lines.append(texts.MY_APPLICATION_CONFIRMED_HINT)
    return "\n".join(lines)


def cancel_application_question(app: MyApplication) -> str:
    return texts.CANCEL_APPLICATION_CONFIRM.format(title=safe(app.title), dates=_dates_text(app.session))


def split_html(text: str, limit: int = PROGRAM_PAGE_LIMIT) -> list[str]:
    """Длинный текст на страницы по `limit` видимых символов: сначала по абзацам, слишком длинный абзац — по строкам,
    слишком длинная строка — по словам. Разметка внутри строки не разрывается, пока строка не длиннее страницы."""
    pages: list[str] = []
    current = ""

    def fits(piece: str, separator: str) -> bool:
        nonlocal current
        candidate = f"{current}{separator}{piece}" if current else piece
        if visible_length(candidate) > limit:
            return False
        current = candidate
        return True

    def flush() -> None:
        nonlocal current
        if current:
            pages.append(current)
            current = ""

    for paragraph in text.strip().split("\n\n"):
        if fits(paragraph, "\n\n"):
            continue
        flush()
        if fits(paragraph, ""):
            continue
        for line in paragraph.split("\n"):
            if fits(line, "\n"):
                continue
            flush()
            if fits(line, ""):
                continue
            for word in line.split(" "):
                if not fits(word, " "):
                    flush()
                    current = word
    flush()
    return pages
