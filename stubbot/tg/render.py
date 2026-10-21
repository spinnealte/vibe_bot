"""Тексты карточек расписания и заявок из моделей. Пользовательские и админские строки - через safe()."""

import re
from datetime import datetime
from html import escape, unescape

from stubbot.db.enums import CancelChoice, EnrollmentStatus, OrderStatus, PayerType
from stubbot.db.models import CourseSession, Order, PriceOption, Program, Venue
from stubbot.services.enrollments import PAYABLE_ORDER_STATUSES, CancelResult, MyApplication
from stubbot.services.schedule import SessionCard
from stubbot.tg import texts
from stubbot.tg.formatting import safe
from stubbot.utils.dates import format_date, format_moment, format_range_with_year, format_time_range
from stubbot.utils.money import format_rub
from stubbot.utils.names import short_name

MAX_BUTTON_TITLE = 40
CAPTION_LIMIT = 1024  # лимит подписи к фото в Telegram (считается видимый текст, без HTML-тегов)
MIN_DESCRIPTION = 80  # если под описание остаётся меньше - не показываем обрывок
EXCERPT_LIMIT = 400  # сколько программы/описания показывать в карточке; полная программа - по кнопке
PROGRAM_PAGE_LIMIT = 900  # страница полной программы ≈ один экран телефона
MAX_QUESTION_DRAFT = 200  # текст едет в ссылке кнопки: кириллица там в шесть раз длиннее, а ссылка не резиновая
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

    Разметку снимаем: обрезать HTML посередине тега нельзя - так ломалась подпись в старом боте.
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
    """В конец подписи - начало программы курса (или описания, если программы нет), до EXCERPT_LIMIT символов
    и в пределах лимита подписи. Полная программа - по кнопке «📖 Программа» с листанием."""
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
        more = ""  # программа короткая и показана целиком - подсказка про кнопку не нужна
    return caption + header + excerpt + more


def _lecturers_line(program: Program) -> list[str]:
    """Лекторы курса «Иванов С. П., Смирнова А. О.» - у курса, общие для всех его потоков."""
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
    """13–14 октября 2026, 10:00–18:00 - время добавляем, если во все дни одинаковое.
    У проведения без дат - «даты уточняются»."""
    if course_session.start_date is None:
        return texts.DATES_TBD
    text = format_range_with_year(course_session.start_date, course_session.end_date)
    times = {format_time_range(day.start_time, day.end_time) for day in course_session.days}
    if len(times) == 1 and (time_text := times.pop()):
        text += f", {time_text}"
    return text


def session_caption(card: SessionCard, deadline_text: str | None, show_seats: bool = True) -> str:
    """Подпись под фото потока в карусели: всё главное сразу, полная программа - по кнопке «Программа».

    show_seats=False - режим афиши (заявки выключены): мест никто не занимает, счётчик всегда равен вместимости.
    """
    s, program = card.session, card.program
    lines = [texts.CAPTION_TITLE.format(title=safe(session_title(s)))]
    if program.short_description:
        lines.append(f"<i>{safe(program.short_description)}</i>")
    lines.append("")
    lines += _lecturers_line(program)
    lines.append(texts.CAPTION_DATES.format(value=_dates_text(s)) if s.start_date else texts.CAPTION_DATES_TBD)
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


def program_caption(program: Program, intro: str | None = None) -> str:
    """Подпись под фото курса в админке (раздел «Курсы» и архив курсов). Курс - запись библиотеки: дат,
    площадки, цен и статуса у него нет - они у проведения, а в афише стоят только проведения.

    intro - строки над карточкой (вопрос мастера «Какой курс проводим?»): место под них отнимается у
    отрывка программы, поэтому они не пропадут и подпись не вылезет за лимит.
    """
    lines = [intro, ""] if intro else []
    lines.append(texts.CAPTION_TITLE.format(title=safe(program.title)))
    if program.short_description:
        lines.append(f"<i>{safe(program.short_description)}</i>")
    lines += ["", *_lecturers_line(program), *_meta_lines(program, program.default_format.value)]
    return _with_excerpt(lines, program)


def program_pages(program: Program, limit: int = PROGRAM_PAGE_LIMIT) -> list[str]:
    """Программа курса страницами «на один экран телефона» для листания в одном сообщении."""
    title = f"📖 <b>{safe(short(program.title, 200))}</b>"
    # Заголовок стоит на первой странице - оставляем под него место, чтобы и она влезала в экран.
    pages = split_html(program.program_html or "", limit=limit - visible_length(title) - 2) or [texts.PROGRAM_TEXT_EMPTY]
    pages[0] = f"{title}\n\n{pages[0]}"
    if len(pages) > 1:
        pages = [page + texts.PROGRAM_PAGE.format(page=i + 1, pages=len(pages)) for i, page in enumerate(pages)]
    return pages


def manager_draft(card: SessionCard) -> str:
    """Черновик сообщения менеджеру по проведению на слайде (простой текст, без HTML).
    У проведения без дат - вопрос «когда ближайший поток»."""
    s = card.session
    if s.start_date is None:
        return texts.MANAGER_DRAFT_PROGRAM.format(title=session_title(s))
    return texts.MANAGER_DRAFT_SESSION.format(title=session_title(s),
                                              dates=format_range_with_year(s.start_date, s.end_date))


def question_draft(text: str) -> str:
    """Черновик менеджеру из того, что человек написал боту: короткий текст подставляем как есть - не придётся
    набирать вопрос заново. Длинный не поместится в ссылку кнопки - тогда общий черновик."""
    question = " ".join(text.split())
    return question if 0 < len(question) <= MAX_QUESTION_DRAFT else texts.MANAGER_DRAFT_ABOUT


def about_text(venues: list[Venue], with_manager: bool) -> str:
    """«О центре»: текст о центре, площадки из БД (с картой, если есть ссылка), подсказка про кнопку «Менеджер»."""
    parts = [texts.ABOUT]
    if not texts.ABOUT_VENUE:
        # Шаблон строки площадки пуст (texts.ABOUT_VENUE) - адреса пока не показываем: остаётся один заголовок
        # с пометкой «Уточняется». Чтобы вернуть список площадок, достаточно вернуть шаблон.
        parts.append(texts.ABOUT_VENUES_TITLE)
    elif venues:
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
    return f"{price.label} - {format_rub(price.amount)}{texts.PRICE_UNIT_SUFFIX[price.unit.value]}"


# --- Запись на курс (полная версия, этап 1) ---------------------------------------------------------------------

def apply_confirm(card: SessionCard, price: PriceOption | None, colleague_name: str | None,
                  colleague_phone: str | None, waitlist: bool) -> str:
    """«Проверьте запись»: курс, даты, тариф, участники; для пары - просьба отметить согласие коллеги."""
    participants = (texts.APPLY_PARTICIPANTS_PAIR.format(name=safe(colleague_name), phone=safe(colleague_phone))
                    if colleague_name else "")
    note = texts.APPLY_CONFIRM_WAITLIST_NOTE if waitlist else texts.APPLY_CONFIRM_NOTE
    if colleague_name:
        note += texts.APPLY_COLLEAGUE_CONSENT_NOTE
    return texts.APPLY_CONFIRM.format(
        title=safe(session_title(card.session)),
        dates=_dates_text(card.session),
        price=safe(price_label(price)) if price else texts.APPLY_NO_PRICE,
        participants=participants,
        note=note,
    )


def order_amount(order: Order) -> str:
    return format_rub(order.total_amount) if order.total_amount is not None else texts.APPLY_NO_PRICE


def enrolled(card: SessionCard, order: Order, colleague_name: str | None, timezone: str) -> str:
    """«Вы записаны!»: заказ, сумма, срок оплаты и вопрос «Как будете оплачивать?»."""
    return texts.APPLY_ENROLLED.format(
        title=safe(session_title(card.session)),
        dates=_dates_text(card.session),
        colleague=texts.APPLY_ENROLLED_COLLEAGUE.format(name=safe(colleague_name)) if colleague_name else "",
        number=order.number,
        amount=order_amount(order),
        due=format_moment(order.due_at, timezone),
    )


def pay_individual(order: Order, email: str | None, timezone: str) -> str:
    return texts.PAY_INDIVIDUAL.format(
        number=order.number,
        email=texts.PAY_INDIVIDUAL_EMAIL.format(email=safe(email)) if email else "",
        due=format_moment(order.due_at, timezone),
    )


def colleague_enrolled(card: SessionCard, enroller_name: str | None) -> str:
    """Коллеге, который уже есть в боте: его записали вместе с собой."""
    return texts.COLLEAGUE_ENROLLED.format(
        title=safe(session_title(card.session)),
        dates=_dates_text(card.session),
        name=safe(enroller_name),
    )


def order_pay_choice(order: Order, timezone: str) -> str:
    """«Как будете оплачивать?» из «Моих записей» - если после записи не выбрали «Физлицо / Юрлицо»."""
    return texts.ORDER_PAY_CHOICE.format(number=order.number, amount=order_amount(order),
                                         due=format_moment(order.due_at, timezone))


# --- Мои записи и отмена (полная версия, этап 1, шаг 3) ----------------------------------------------------------

def my_application_card(app: MyApplication, timezone: str, now: datetime) -> str:
    """Карточка одной записи в «Моих записях» (листаются стрелками). Заказ и оплату видит только плательщик;
    коллеге из пары - кто его записал."""
    enrollment, s, order = app.enrollment, app.session, app.order
    lines = [texts.MY_APPLICATIONS_TITLE, "", texts.MY_APPLICATION_COURSE.format(value=safe(app.title)),
             texts.MY_APPLICATION_DATES.format(value=_dates_text(s))]
    if s.venue:
        lines.append(texts.MY_APPLICATION_VENUE.format(value=f"{safe(s.venue.name)}, {safe(s.venue.address)}"))
    if app.price:
        lines.append(texts.MY_APPLICATION_PRICE.format(value=safe(price_label(app.price))))
    if app.partner:
        template = texts.MY_APPLICATION_COLLEAGUE if app.is_payer else texts.MY_APPLICATION_ENROLLER
        lines.append(template.format(name=safe(app.partner.full_name)))
    if app.is_payer and order is not None and order.status is not OrderStatus.CANCELLED:
        lines += ["", *_order_lines(app, order, timezone, now)]
    lines += ["", texts.MY_APPLICATION_STATUS.format(value=texts.ENROLLMENT_STATUS_LABELS[enrollment.status.value])]
    hint = _cancel_hint(app)
    if hint:
        lines += ["", hint]
    return "\n".join(lines)


def _order_lines(app: MyApplication, order: Order, timezone: str, now: datetime) -> list[str]:
    """Заказ, сумма, сколько оплачено (или срок оплаты), плательщик."""
    lines = [texts.MY_APPLICATION_ORDER.format(number=order.number, amount=order_amount(order))]
    if app.paid:
        if order.total_amount is None:
            lines.append(texts.MY_APPLICATION_PAID_OPEN.format(paid=format_rub(app.paid)))
        elif app.paid >= order.total_amount:
            lines.append(texts.MY_APPLICATION_PAID_FULL.format(paid=format_rub(app.paid)))
        else:
            lines.append(texts.MY_APPLICATION_PAID.format(paid=format_rub(app.paid),
                                                          left=format_rub(order.total_amount - app.paid)))
    elif app.active and order.status in PAYABLE_ORDER_STATUSES:
        lines.append(texts.MY_APPLICATION_DUE.format(value=format_moment(order.due_at, timezone)))
        if order.due_at < now:
            lines.append(texts.MY_APPLICATION_OVERDUE)
    if order.payer_type is PayerType.INDIVIDUAL:
        lines.append(texts.MY_APPLICATION_PAYER.format(value=texts.PAYER_INDIVIDUAL))
    elif order.payer_type is PayerType.ORGANIZATION:
        value = texts.PAYER_ORGANIZATION if order.requisites_file_id else texts.PAYER_ORGANIZATION_WAITING
        lines.append(texts.MY_APPLICATION_PAYER.format(value=value))
    return lines


def _cancel_hint(app: MyApplication) -> str | None:
    """Подсказка, как отменить: до какого дня здесь, позже или коллеге - через менеджера."""
    if not app.active or app.enrollment.status is EnrollmentStatus.WAITLIST:
        return None
    if not app.is_payer:
        return texts.MY_APPLICATION_COLLEAGUE_HINT
    if not app.self_cancel:
        return texts.MY_APPLICATION_CANCEL_LATE
    if app.cancel_until is not None:
        return texts.MY_APPLICATION_CANCEL_UNTIL.format(date=format_date(app.cancel_until))
    return None  # проведение без дат - отменить здесь можно всегда


def _partner_short_name(app: MyApplication) -> str | None:
    """«Петрова А. С.» - коллега в вопросе об отмене. None - записи вдвоём нет."""
    if app.partner is None or not app.partner.full_name:
        return None
    return safe(short_name(app.partner.full_name))


def cancel_question(app: MyApplication) -> str:
    """«Отменить запись?» без оплаты: заказ закроется, оба - в лист ожидания курса. Или «Выйти из листа ожидания?»."""
    title, dates = safe(app.title), _dates_text(app.session)
    name = _partner_short_name(app)
    if app.enrollment.status is EnrollmentStatus.WAITLIST:
        colleague = texts.CANCEL_WAITLIST_COLLEAGUE_LINE.format(name=name) if name else ""
        return texts.CANCEL_WAITLIST_CONFIRM.format(title=title, dates=dates, colleague=colleague)
    colleague = texts.CANCEL_COLLEAGUE_LINE.format(name=name) if name else ""
    if app.order is None:
        return texts.CANCEL_PLAIN_CONFIRM.format(title=title, dates=dates, colleague=colleague)
    return texts.CANCEL_APPLICATION_CONFIRM.format(
        title=title, dates=dates, colleague=colleague, number=app.order.number,
        seats=texts.CANCEL_SEATS_PAIR if name else texts.CANCEL_SEATS_ONE,
        who=texts.CANCEL_WHO_PAIR if name else texts.CANCEL_WHO_ONE,
    )


def cancel_paid_question(app: MyApplication, paid: int) -> str:
    """«Отменить запись?», когда по заказу уже платили: что сделать с оплатой."""
    name = _partner_short_name(app)
    return texts.CANCEL_PAID_CONFIRM.format(
        title=safe(app.title), dates=_dates_text(app.session),
        colleague=texts.CANCEL_COLLEAGUE_LINE.format(name=name) if name else "",
        number=app.order.number, paid=format_rub(paid),
    )


def cancelled(result: CancelResult, has_manager: bool) -> str:
    """Ответ после отмены: что будет с деньгами, или «Вы в листе ожидания курса»."""
    paid = format_rub(result.paid)
    pair = bool(result.colleagues)
    match result.choice:
        case CancelChoice.TRANSFER:
            return texts.CANCELLED_TRANSFER.format(paid=paid)
        case CancelChoice.ADVANCE:
            return texts.CANCELLED_ADVANCE.format(paid=paid)
        case CancelChoice.REFUND:
            return (texts.CANCELLED_REFUND if has_manager else texts.CANCELLED_REFUND_NO_MANAGER).format(paid=paid)
    if result.waitlist:
        return texts.CANCELLED_WAITLIST_PAIR if pair else texts.CANCELLED_WAITLIST
    if result.course_waitlist:
        return texts.CANCELLED_UNPAID.format(number=result.order.number,
                                             who=texts.CANCELLED_WHO_PAIR if pair else texts.CANCELLED_WHO_ONE)
    return texts.CANCELLED_PLAIN


def colleague_cancelled(app: MyApplication, enroller_name: str | None) -> str:
    """Коллеге, который уже в боте: записавший отменил запись пары."""
    return texts.COLLEAGUE_CANCELLED.format(title=safe(app.title), dates=_dates_text(app.session),
                                            name=safe(enroller_name))


def manager_cancel_draft(app: MyApplication) -> str:
    """Черновик менеджеру (простой текст), когда отменить самому уже нельзя."""
    order = app.order if app.is_payer and app.order is not None else None
    return texts.MANAGER_DRAFT_CANCEL.format(
        title=app.title, order=texts.MANAGER_DRAFT_ORDER.format(number=order.number) if order else "",
    )


def manager_refund_draft(order: Order) -> str:
    return texts.MANAGER_DRAFT_REFUND.format(number=order.number)


def split_html(text: str, limit: int = PROGRAM_PAGE_LIMIT) -> list[str]:
    """Длинный текст на страницы по `limit` видимых символов: сначала по абзацам, слишком длинный абзац - по строкам,
    слишком длинная строка - по словам. Разметка внутри строки не разрывается, пока строка не длиннее страницы."""
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
