"""Экраны расписания и заявки. Не импортирует flows.py (flows вызывает отсюда start_application).

Расписание - карусель: одно сообщение с фото курса и подробностями в подписи; ◀️ ▶️, «Программа»,
шаги заявки - всё меняет то же сообщение (tg/screen.py). in_place=False - прислать новое (например, после текста,
который написал пользователь, или из главного меню).
"""

from datetime import date

from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from stubbot.config import get_settings
from stubbot.db.enums import SessionStatus
from stubbot.db.models import Client, PriceOption
from stubbot.services.enrollments import PAIR_SEATS, EnrollmentService, is_pair_price
from stubbot.services.schedule import ScheduleService, SessionCard
from stubbot.tg import keyboards, render, texts
from stubbot.tg.screen import DEFAULT_COVER, Photo, send_screen, show_screen, strip_keyboard
from stubbot.tg.states import PENDING_APPLY_KEY, Application
from stubbot.utils.dates import format_moment
from stubbot.utils.links import with_draft_text

CAROUSEL_INDEX_KEY = "carousel_index"
PROMPT_MESSAGE_KEY = "prompt_message_id"
# Запись «Для двух коллег» (FSM data): ФИО и телефон коллеги, галочка «Коллега согласен».
COLLEAGUE_NAME_KEY = "colleague_name"
COLLEAGUE_PHONE_KEY = "colleague_phone"
COLLEAGUE_OK_KEY = "colleague_ok"
ORDER_KEY = "order_id"  # заказ, по которому ждём файл с реквизитами юрлица


async def _screen(message: Message, text: str, markup: InlineKeyboardMarkup | None = None, photo: Photo = None,
                  in_place: bool = True) -> Message:
    return await (show_screen if in_place else send_screen)(message, text, markup, photo)


# --- Расписание ------------------------------------------------------------------------------------------------

async def show_course(message: Message, session: AsyncSession, today: date, index: int, timezone: str,
                      in_place: bool = True) -> None:
    """Слайд карусели - проведение: с датами или без дат («даты уточняются»)."""
    slide = await ScheduleService(session).slide(today, index)
    settings = get_settings()
    if slide is None:
        # Пустое расписание не должно быть тупиком: есть менеджер - предлагаем спросить у него.
        if settings.manager_url:
            url = with_draft_text(settings.manager_url, texts.MANAGER_DRAFT_SCHEDULE)
            await _screen(message, texts.SCHEDULE_EMPTY + texts.SCHEDULE_EMPTY_MANAGER_HINT,
                          keyboards.contact_manager(url, texts.BTN_MANAGER_COURSES), in_place=in_place)
        else:
            await _screen(message, texts.SCHEDULE_EMPTY, in_place=in_place)
        return
    program, card = slide.program, slide.card
    # MVP - афиша: без заявки, «Сообщить о наборе» и счётчика мест, пока заявки выключены в настройках.
    applications = settings.applications_enabled
    markup = keyboards.course_carousel(
        index=slide.index,
        total=slide.total,
        program_id=program.id,
        session_id=card.session.id,
        apply_text=_apply_button(card) if applications else None,
        notify=card.session.status is SessionStatus.ANNOUNCED and applications,
        has_program_text=bool(program.program_html),
        # Запись в MVP - через менеджера: чат откроется с готовым черновиком про этот курс.
        manager_url=with_draft_text(settings.manager_url, render.manager_draft(card))
        if settings.manager_url else None,
        admin=settings.is_admin(message.chat.id),  # в личке chat.id = id пользователя
    )
    caption = render.session_caption(card, deadline_text(card, timezone), show_seats=applications)
    photo = card.session.cover_file_id or program.cover_file_id or DEFAULT_COVER
    await _screen(message, caption, markup, photo, in_place)


async def show_program_page(message: Message, session: AsyncSession, today: date, program_id: int, index: int,
                            page: int) -> bool:
    """Программа курса постранично; index - карточка афиши, с которой её открыли (туда вернёт «Назад к курсу»)."""
    service = ScheduleService(session)
    program = await service.program(program_id)
    if program is None:
        return False
    manager_url = get_settings().manager_url
    if manager_url:
        # Черновик - как под карточкой, с которой открыли программу: с датами этого проведения. Если карточку
        # за это время убрали из афиши - общий вопрос про курс.
        slide = await service.slide(today, index)
        draft = (render.manager_draft(slide.card) if slide is not None and slide.program.id == program_id
                 else texts.MANAGER_DRAFT_PROGRAM.format(title=program.title))
        manager_url = with_draft_text(manager_url, draft)
    pages = render.program_pages(program)
    page = min(max(page, 0), len(pages) - 1)
    await show_screen(message, pages[page], keyboards.program_pages(program_id, index, page, len(pages), manager_url))
    return True


def _apply_button(card: SessionCard) -> str | None:
    if not card.accepts_applications:
        return None
    return texts.BTN_APPLY_WAITLIST if card.goes_to_waitlist else texts.BTN_APPLY


def deadline_text(card: SessionCard, timezone: str) -> str | None:
    deadline = card.session.registration_deadline
    if deadline is None or not card.accepts_applications:
        return None
    return format_moment(deadline, timezone)


# --- Запись на курс --------------------------------------------------------------------------------------------
# Тариф → (для двух коллег) ФИО и телефон коллеги → проверка (галочка за коллегу) → «Записаться».

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
    await after_price(message, state, session, client, today, price, in_place)


async def after_price(message: Message, state: FSMContext, session: AsyncSession, client: Client, today: date,
                      price: PriceOption | None, in_place: bool = True) -> None:
    """Тариф выбран: «Для двух коллег» - сначала данные коллеги, иначе - сразу проверка записи."""
    if is_pair_price(price):
        await ask_colleague_name(message, state, in_place)
        return
    await show_confirm(message, state, session, client, today, in_place)


async def ask_colleague_name(message: Message, state: FSMContext, in_place: bool = True) -> None:
    await state.set_state(Application.colleague_name)
    prompt = await _screen(message, texts.APPLY_ASK_COLLEAGUE_NAME, keyboards.apply_input_cancel(), in_place=in_place)
    # Ответ придёт текстом ниже - тогда у этого сообщения уберём кнопку «Отмена».
    await state.update_data({PROMPT_MESSAGE_KEY: prompt.message_id})


async def ask_colleague_phone(message: Message, state: FSMContext) -> None:
    """После ФИО (ответ текстом): вопрос новым сообщением, у прошлого убираем кнопку."""
    await _strip_prompt(message, state)
    await state.set_state(Application.colleague_phone)
    prompt = await _screen(message, texts.APPLY_ASK_COLLEAGUE_PHONE, keyboards.apply_input_cancel(), in_place=False)
    await state.update_data({PROMPT_MESSAGE_KEY: prompt.message_id})


async def show_confirm(message: Message, state: FSMContext, session: AsyncSession, client: Client,
                       today: date, in_place: bool = True) -> None:
    """«Проверьте запись». Места пересчитываются заново: пока человек вводил данные, их могли занять."""
    data = await state.get_data()
    index = data.get(CAROUSEL_INDEX_KEY, 0)
    card, existing = await EnrollmentService(session).availability(client, data[PENDING_APPLY_KEY], today)
    if card is None or existing is not None:
        await start_application(message, state, session, client, today, data[PENDING_APPLY_KEY], index, in_place)
        return
    if not in_place:
        await _strip_prompt(message, state)
    price = next((p for p in card.active_prices if p.id == data.get("price_id")), None)
    pair = is_pair_price(price)
    seats = PAIR_SEATS if pair else 1
    waitlist = card.goes_to_waitlist or (card.seats_left is not None and card.seats_left < seats)
    await state.set_state(Application.confirm)
    text = render.apply_confirm(card, price, data.get(COLLEAGUE_NAME_KEY) if pair else None,
                                data.get(COLLEAGUE_PHONE_KEY) if pair else None, waitlist)
    await _screen(message, text, keyboards.apply_confirm(pair, bool(data.get(COLLEAGUE_OK_KEY))), in_place=in_place)


async def _strip_prompt(message: Message, state: FSMContext) -> None:
    prompt_id = (await state.get_data()).get(PROMPT_MESSAGE_KEY)
    if prompt_id:
        await strip_keyboard(message, prompt_id)

