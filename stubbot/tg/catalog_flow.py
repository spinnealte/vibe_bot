"""Экраны расписания и заявки. Не импортирует flows.py (flows вызывает отсюда start_application).

Расписание — карусель: одно сообщение с фото курса и подробностями в подписи; ◀️ ▶️, «Программа»,
шаги заявки — всё меняет то же сообщение (tg/screen.py). in_place=False — прислать новое (например, после текста,
который написал пользователь, или из главного меню).
"""

from datetime import date
from zoneinfo import ZoneInfo

from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from stubbot.config import get_settings
from stubbot.db.enums import SessionStatus
from stubbot.db.models import Client, PriceOption
from stubbot.services.enrollments import EnrollmentService, fixed_group_seats, seat_choices
from stubbot.services.schedule import ScheduleService, SessionCard
from stubbot.tg import keyboards, render, texts
from stubbot.tg.formatting import safe
from stubbot.tg.screen import DEFAULT_COVER, Photo, send_screen, show_screen, strip_keyboard
from stubbot.tg.states import PENDING_APPLY_KEY, Application
from stubbot.utils.dates import MONTHS_GENITIVE, format_range
from stubbot.utils.links import with_draft_text

CAROUSEL_INDEX_KEY = "carousel_index"
PROMPT_MESSAGE_KEY = "prompt_message_id"


async def _screen(message: Message, text: str, markup: InlineKeyboardMarkup | None = None, photo: Photo = None,
                  in_place: bool = True) -> Message:
    return await (show_screen if in_place else send_screen)(message, text, markup, photo)


# --- Расписание ------------------------------------------------------------------------------------------------

async def show_course(message: Message, session: AsyncSession, today: date, index: int, timezone: str,
                      in_place: bool = True) -> None:
    """Слайд карусели: поток с датами или курс «даты уточняются»."""
    slide = await ScheduleService(session).slide(today, index)
    if slide is None:
        await _screen(message, texts.SCHEDULE_EMPTY, in_place=in_place)
        return
    program, card = slide.program, slide.card
    # MVP — афиша: без заявки, «Сообщить о наборе» и счётчика мест, пока заявки выключены в настройках.
    settings = get_settings()
    applications = settings.applications_enabled
    if card is not None:
        caption = render.session_caption(card, _deadline_text(card, timezone), show_seats=applications)
        photo = card.session.cover_file_id or program.cover_file_id
        notify = card.session.status is SessionStatus.ANNOUNCED
    else:
        caption = render.program_caption(program)
        photo = program.cover_file_id
        notify = True
    markup = keyboards.course_carousel(
        index=slide.index,
        total=slide.total,
        program_id=program.id,
        session_id=card.session.id if card else None,
        apply_text=_apply_button(card) if card and applications else None,
        notify=notify and applications,
        has_program_text=bool(program.program_html),
        # Запись в MVP — через менеджера: чат откроется с готовым черновиком про этот курс.
        manager_url=with_draft_text(settings.manager_url, render.manager_draft(program, card))
        if settings.manager_url else None,
        admin=settings.is_admin(message.chat.id),  # в личке chat.id = id пользователя
    )
    await _screen(message, caption, markup, photo or DEFAULT_COVER, in_place)


async def show_program_page(message: Message, session: AsyncSession, program_id: int, index: int,
                            page: int) -> bool:
    program = await ScheduleService(session).program(program_id)
    if program is None:
        return False
    pages = render.program_pages(program)
    page = min(max(page, 0), len(pages) - 1)
    await show_screen(message, pages[page], keyboards.program_pages(program_id, index, page, len(pages)))
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


# --- Заявка ----------------------------------------------------------------------------------------------------

async def start_application(message: Message, state: FSMContext, session: AsyncSession, client: Client,
                            today: date, session_id: int, index: int = 0, in_place: bool = True) -> None:
    card, existing = await EnrollmentService(session).availability(client, session_id, today)
    if card is None:
        await state.clear()
        await _screen(message, texts.APPLY_CLOSED, keyboards.to_schedule(index), in_place=in_place)
        return
    if existing is not None:
        await state.clear()
        await _screen(message, texts.APPLY_DUPLICATE.format(status=texts.ENROLLMENT_STATUS_LABELS[existing.status.value]),
                      keyboards.to_schedule(index), in_place=in_place)
        return

    await state.set_data({PENDING_APPLY_KEY: session_id, CAROUSEL_INDEX_KEY: index})
    prices = card.active_prices
    if len(prices) > 1:
        await state.set_state(Application.price)
        await _screen(message, texts.APPLY_CHOOSE_PRICE,
                      keyboards.apply_prices([(p.id, render.price_label(p)) for p in prices]), in_place=in_place)
        return
    price = prices[0] if prices else None
    await state.update_data(price_id=price.id if price else None)
    await ask_seats(message, state, price, in_place)


async def ask_seats(message: Message, state: FSMContext, price: PriceOption | None, in_place: bool = True) -> None:
    fixed = fixed_group_seats(price)
    choices = seat_choices(price)
    if fixed is not None or choices == [1]:
        await state.update_data(seats=fixed or 1)
        await ask_comment(message, state, in_place)
        return
    await state.set_state(Application.seats)
    await _screen(message, texts.APPLY_CHOOSE_SEATS, keyboards.apply_seats(choices), in_place=in_place)


async def ask_comment(message: Message, state: FSMContext, in_place: bool = True) -> None:
    await state.set_state(Application.comment)
    prompt = await _screen(message, texts.APPLY_ASK_COMMENT, keyboards.apply_comment(), in_place=in_place)
    # Ответ придёт текстом ниже — тогда у этого сообщения уберём кнопки (иначе висят «Без комментария»/«Отменить»).
    await state.update_data({PROMPT_MESSAGE_KEY: prompt.message_id})


async def show_confirm(message: Message, state: FSMContext, session: AsyncSession, client: Client,
                       today: date, in_place: bool = True) -> None:
    data = await state.get_data()
    index = data.get(CAROUSEL_INDEX_KEY, 0)
    card, existing = await EnrollmentService(session).availability(client, data[PENDING_APPLY_KEY], today)
    if card is None or existing is not None:
        await start_application(message, state, session, client, today, data[PENDING_APPLY_KEY], index, in_place)
        return
    if not in_place and data.get(PROMPT_MESSAGE_KEY):
        await strip_keyboard(message, data[PROMPT_MESSAGE_KEY])
    price = next((p for p in card.active_prices if p.id == data.get("price_id")), None)
    seats = data.get("seats", 1)
    waitlist = card.goes_to_waitlist or (card.seats_left is not None and card.seats_left < seats)
    await state.set_state(Application.confirm)
    await _screen(
        message,
        texts.APPLY_CONFIRM.format(
            title=safe(render.session_title(card.session)),
            dates=format_range(card.session.start_date, card.session.end_date),
            price=safe(render.price_label(price)) if price else texts.APPLY_NO_PRICE,
            seats=seats,
            comment=safe(data.get("comment")) if data.get("comment") else f"<i>{texts.APPLY_NO_COMMENT}</i>",
            waitlist_note=texts.APPLY_CONFIRM_WAITLIST_NOTE if waitlist else "",
        ),
        keyboards.apply_confirm(),
        in_place=in_place,
    )

