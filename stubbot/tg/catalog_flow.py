"""Экраны расписания и заявки. Не импортирует flows.py (flows вызывает отсюда start_application)."""

from datetime import date
from zoneinfo import ZoneInfo

from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from stubbot.db.enums import SessionStatus
from stubbot.db.models import Client, PriceOption
from stubbot.services.enrollments import EnrollmentService, fixed_group_seats, seat_choices
from stubbot.services.schedule import ScheduleService, SessionCard
from stubbot.tg import keyboards, render, texts
from stubbot.tg.formatting import safe
from stubbot.tg.states import PENDING_APPLY_KEY, Application
from stubbot.utils.dates import MONTHS_GENITIVE, format_range


# --- Расписание ------------------------------------------------------------------------------------------------

async def show_schedule(message: Message, session: AsyncSession, today: date, page: int = 0,
                        edit: bool = False) -> None:
    schedule = await ScheduleService(session).page(today, page)
    if not schedule.sessions:
        text, markup = texts.SCHEDULE_EMPTY, keyboards.schedule_list([], 0, 1)
    else:
        text = texts.SCHEDULE_TITLE
        if schedule.pages > 1:
            text += texts.SCHEDULE_PAGE.format(page=schedule.page + 1, pages=schedule.pages)
        buttons = [(s.id, render.session_button(s)) for s in schedule.sessions]
        markup = keyboards.schedule_list(buttons, schedule.page, schedule.pages)
    await _send_or_edit(message, text, markup, edit)


async def show_session(message: Message, session: AsyncSession, today: date, session_id: int, page: int,
                       timezone: str) -> bool:
    """Карточка потока. False — поток недоступен (скрыт, прошёл)."""
    card = await ScheduleService(session).card(session_id, today)
    if card is None:
        await message.answer(texts.SESSION_NOT_FOUND)
        await show_schedule(message, session, today)
        return False
    cover = card.session.cover_file_id or card.program.cover_file_id
    if cover:
        # Обложка отдельным сообщением с короткой подписью: длинный текст карточки не влезет в caption (1024).
        await message.answer_photo(cover, caption=f"🦷 <b>{safe(render.session_title(card.session))}</b>")
    await message.answer(
        render.session_card(card, _deadline_text(card, timezone)),
        reply_markup=keyboards.session_card(
            session_id=card.session.id,
            program_id=card.program.id,
            page=page,
            apply_text=_apply_button(card),
            notify=card.session.status is SessionStatus.ANNOUNCED,
            has_program_text=bool(card.program.program_html),
        ),
    )
    return True


async def show_programs(message: Message, session: AsyncSession, edit: bool = False) -> None:
    programs = await ScheduleService(session).programs()
    if not programs:
        await _send_or_edit(message, texts.PROGRAMS_EMPTY, keyboards.programs_list([]), edit)
        return
    await _send_or_edit(message, texts.PROGRAMS_TITLE,
                        keyboards.programs_list([(p.id, render.short(p.title)) for p in programs]), edit)


async def show_program(message: Message, session: AsyncSession, today: date, program_id: int,
                       edit: bool = False) -> bool:
    service = ScheduleService(session)
    program = await service.program(program_id)
    if program is None:
        return False
    sessions = await service.program_sessions(program_id, today)
    await _send_or_edit(
        message,
        render.program_card(program, sessions),
        keyboards.program_card(program.id, [(s.id, render.session_button(s)) for s in sessions],
                               has_program_text=bool(program.program_html)),
        edit,
    )
    return True


def _apply_button(card: SessionCard) -> str | None:
    if not card.accepts_applications:
        return None
    return texts.BTN_APPLY_WAITLIST if card.goes_to_waitlist else texts.BTN_APPLY


def _deadline_text(card: SessionCard, timezone: str) -> str | None:
    deadline = card.session.registration_deadline
    if deadline is None or not card.accepts_applications:
        return None
    local = deadline.astimezone(ZoneInfo(timezone))
    return f"{local.day} {MONTHS_GENITIVE[local.month - 1]}, {local:%H:%M}"


async def _send_or_edit(message: Message, text: str, markup, edit: bool) -> None:
    if edit:
        try:
            await message.edit_text(text, reply_markup=markup)
            return
        except TelegramBadRequest:
            # Старое сообщение нельзя изменить (фото, слишком старое) — отправляем новое.
            pass
    await message.answer(text, reply_markup=markup)


# --- Заявка ----------------------------------------------------------------------------------------------------

async def start_application(message: Message, state: FSMContext, session: AsyncSession, client: Client,
                            today: date, session_id: int) -> None:
    card, existing = await EnrollmentService(session).availability(client, session_id, today)
    if card is None:
        await state.clear()
        await message.answer(texts.APPLY_CLOSED, reply_markup=keyboards.main_menu())
        return
    if existing is not None:
        await state.clear()
        await message.answer(texts.APPLY_DUPLICATE.format(status=texts.ENROLLMENT_STATUS_LABELS[existing.status.value]),
                             reply_markup=keyboards.main_menu())
        return

    await state.set_data({PENDING_APPLY_KEY: session_id})
    prices = card.active_prices
    if len(prices) > 1:
        await state.set_state(Application.price)
        await message.answer(texts.APPLY_CHOOSE_PRICE,
                             reply_markup=keyboards.apply_prices([(p.id, render.price_label(p)) for p in prices]))
        return
    price = prices[0] if prices else None
    await state.update_data(price_id=price.id if price else None)
    await ask_seats(message, state, price)


async def ask_seats(message: Message, state: FSMContext, price: PriceOption | None) -> None:
    fixed = fixed_group_seats(price)
    if fixed is not None:
        await state.update_data(seats=fixed)
        await ask_comment(message, state)
        return
    choices = seat_choices(price)
    if choices == [1]:
        await state.update_data(seats=1)
        await ask_comment(message, state)
        return
    await state.set_state(Application.seats)
    await message.answer(texts.APPLY_CHOOSE_SEATS, reply_markup=keyboards.apply_seats(choices))


async def ask_comment(message: Message, state: FSMContext) -> None:
    await state.set_state(Application.comment)
    await message.answer(texts.APPLY_ASK_COMMENT, reply_markup=keyboards.apply_comment())


async def show_confirm(message: Message, state: FSMContext, session: AsyncSession, client: Client,
                       today: date) -> None:
    data = await state.get_data()
    card, existing = await EnrollmentService(session).availability(client, data[PENDING_APPLY_KEY], today)
    if card is None or existing is not None:
        await start_application(message, state, session, client, today, data[PENDING_APPLY_KEY])
        return
    price = next((p for p in card.active_prices if p.id == data.get("price_id")), None)
    seats = data.get("seats", 1)
    waitlist = card.goes_to_waitlist or (card.seats_left is not None and card.seats_left < seats)
    await state.set_state(Application.confirm)
    await message.answer(
        texts.APPLY_CONFIRM.format(
            title=safe(render.session_title(card.session)),
            dates=format_range(card.session.start_date, card.session.end_date),
            price=safe(render.price_label(price)) if price else texts.APPLY_NO_PRICE,
            seats=seats,
            comment=safe(data.get("comment")) if data.get("comment") else f"<i>{texts.APPLY_NO_COMMENT}</i>",
            waitlist_note=texts.APPLY_CONFIRM_WAITLIST_NOTE if waitlist else "",
        ),
        reply_markup=keyboards.apply_confirm(),
    )
