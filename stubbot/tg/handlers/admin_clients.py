"""Раздел «👥 Клиенты» в админке (полная версия, этап 1, шаг 4.2): поиск по ФИО и телефону, карточка клиента,
сообщение клиенту, правка аванса, участники проведения, листы ожидания курсов. Проверки - в AdminClientService.

Только для админов: IsAdmin на весь роутер. Подключается вместе с записью на курсы (APPLICATIONS_ENABLED), после
admin.router - «⚙️ Админка» посреди ввода открывает меню. Списки (найденные, участники, листы ожидания, заказы
клиента) - пронумерованные (tg/picker.py, правило 26.10.2026): номер открывает строку, в результатах поиска
другой текст - новый запрос.
"""

import secrets

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from stubbot.config import Settings
from stubbot.db.models import Client
from stubbot.services.admin_clients import (
    ADVANCE_REASON_LIMIT,
    AdminClientService,
    AdvanceInput,
    AdvanceInputError,
    AdvanceOutcome,
    QueryKind,
    parse_advance,
)
from stubbot.services.admin_log import log_admin_action
from stubbot.tg import admin_clients_views as views
from stubbot.tg import admin_orders_views, picker, texts
from stubbot.tg.callbacks import ClientAdminAction, ClientAdminCb
from stubbot.tg.filters import IsAdmin
from stubbot.tg.flows import with_notice
from stubbot.tg.outgoing import Outgoing, deliver, deliver_to_client, from_message, send_once
from stubbot.tg.render import visible_length
from stubbot.tg.screen import send_screen, show_screen, strip_keyboard
from stubbot.tg.states import AdminClient
from stubbot.utils.money import format_rub

router = Router(name="admin_clients")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())

# FSM data.
CLIENT_KEY = "client_id"
BACK_KEY = "client_back"  # откуда открыли карточку - туда вернёт «Назад»
DRAFT_KEY = "draft"
TOKEN_KEY = "token"
ADVANCE_KEY = "advance"  # [сумма в копейках со знаком, причина]
PROMPT_KEY = "client_prompt_id"


def _service(session: AsyncSession, client: Client) -> AdminClientService:
    return AdminClientService(session, client.telegram_id)


def _cb(action: ClientAdminAction):
    return ClientAdminCb.filter(F.action == action)


async def _show_client(message: Message, state: FSMContext, session: AsyncSession, admin: Client,
                       settings: Settings, client_id: int, back: str, notice: str | None = None,
                       in_place: bool = True) -> bool:
    card = await _service(session, admin).card(client_id)
    if card is None:
        return False
    text, markup = views.client_card(card, settings.timezone, back)
    await (show_screen if in_place else send_screen)(message, with_notice(notice, text), markup)
    # Заказы клиента перечислены в карточке по номерам - номер открывает заказ.
    await picker.remember(state, views.orders_pick(card))
    return True


async def _ask(message: Message, state: FSMContext, text: str, markup, in_place: bool = True) -> None:
    prompt = await (show_screen if in_place else send_screen)(message, text, markup)
    await state.update_data({PROMPT_KEY: prompt.message_id})


async def _strip_prompt(message: Message, state: FSMContext) -> None:
    prompt_id = (await state.get_data()).get(PROMPT_KEY)
    if prompt_id:
        await strip_keyboard(message, prompt_id)


# --- Меню и поиск ----------------------------------------------------------------------------------------------

@router.callback_query(_cb(ClientAdminAction.MENU))
async def clients_menu(callback: CallbackQuery, state: FSMContext, session: AsyncSession, client: Client) -> None:
    await callback.answer()
    await state.clear()
    await show_screen(callback.message, *views.clients_menu(await _service(session, client).total()))


@router.callback_query(_cb(ClientAdminAction.FIND))
async def ask_query(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.clear()
    await state.set_state(AdminClient.query)
    await _ask(callback.message, state, *views.ask_query())


@router.message(AdminClient.query, F.text)
async def on_query(message: Message, state: FSMContext, session: AsyncSession, client: Client) -> None:
    await _search(message, state, session, client, message.text)


@picker.requery(views.PICK_CLIENT)
async def search_again(message: Message, state: FSMContext, session: AsyncSession, client: Client,
                       settings: Settings, text: str, context: dict) -> None:
    """В результатах поиска написан не номер из списка - это новый запрос."""
    await _search(message, state, session, client, text)


async def _search(message: Message, state: FSMContext, session: AsyncSession, client: Client, text: str) -> None:
    result = await _service(session, client).search(text)
    if result.query.kind is QueryKind.TOO_SHORT:
        await message.answer(texts.ADMIN_CLIENT_QUERY_SHORT)
        return
    await _strip_prompt(message, state)
    pick = views.search_results(result, text)
    if not result.clients:
        # Никого - ждём следующий запрос; у этого экрана кнопки уберём, когда он придёт.
        await state.set_state(AdminClient.query)
        await _ask(message, state, *picker.render(pick), in_place=False)
        return
    # Найденные - по номерам: номер открывает карточку, другой текст - новый запрос (search_again).
    screen = await picker.show(message, state, pick, in_place=False)
    await state.update_data({PROMPT_KEY: screen.message_id})


@picker.opener(views.PICK_CLIENT)
async def open_client(message: Message, state: FSMContext, session: AsyncSession, client: Client,
                      settings: Settings, client_id: int, context: dict) -> None:
    """Номер из пронумерованного списка клиентов (поиск, участники, лист ожидания) - карточка клиента."""
    if not await _show_client(message, state, session, client, settings, client_id, context.get("back", ""),
                              in_place=False):
        await message.answer(texts.ADMIN_CLIENT_UNAVAILABLE)


@router.message(AdminClient.query)
async def query_expects_text(message: Message) -> None:
    await message.answer(texts.ADMIN_CLIENT_QUERY_SHORT)


# --- Карточка клиента ------------------------------------------------------------------------------------------

@router.callback_query(_cb(ClientAdminAction.VIEW))
@router.callback_query(_cb(ClientAdminAction.CANCEL))
async def view_client(callback: CallbackQuery, callback_data: ClientAdminCb, state: FSMContext,
                      session: AsyncSession, client: Client, settings: Settings) -> None:
    """Карточка клиента; «✖️ Отмена» сообщения или правки аванса - назад к ней."""
    await state.clear()
    if not await _show_client(callback.message, state, session, client, settings, callback_data.item_id,
                              callback_data.value):
        await callback.answer(texts.ADMIN_CLIENT_UNAVAILABLE, show_alert=True)
        return
    await callback.answer()


# --- Сообщение клиенту: текст или файл → предпросмотр → «Отправить» --------------------------------------------

@router.callback_query(_cb(ClientAdminAction.WRITE))
async def ask_message(callback: CallbackQuery, callback_data: ClientAdminCb, state: FSMContext,
                      session: AsyncSession, client: Client) -> None:
    card = await _service(session, client).card(callback_data.item_id)
    if card is None:
        await callback.answer(texts.ADMIN_CLIENT_UNAVAILABLE, show_alert=True)
        return
    await callback.answer()
    await state.clear()
    await state.set_state(AdminClient.message)
    await state.update_data({CLIENT_KEY: card.client.id, BACK_KEY: callback_data.value})
    await _ask(callback.message, state, *views.ask_message(card.client, callback_data.value))


@router.message(AdminClient.message)
async def on_message_content(message: Message, state: FSMContext, session: AsyncSession, client: Client,
                             bot: Bot) -> None:
    data = await state.get_data()
    outgoing = from_message(message)
    if outgoing is None:
        await message.answer(texts.ADMIN_ORDER_MESSAGE_UNSUPPORTED)
        return
    over = visible_length(outgoing.text) - outgoing.limit
    if over > 0:
        await message.answer(texts.ADMIN_ORDER_TOO_LONG.format(over=over))
        return
    card = await _service(session, client).card(data.get(CLIENT_KEY, 0))
    if card is None:
        await state.clear()
        await message.answer(texts.ADMIN_CLIENT_UNAVAILABLE)
        return
    await _strip_prompt(message, state)
    token = secrets.token_hex(8)
    await state.update_data({DRAFT_KEY: outgoing.to_data(), TOKEN_KEY: token})
    await message.answer(admin_orders_views.preview_note(card.client))
    buttons = views.preview_buttons(card.client.id, token, data.get(BACK_KEY, ""))
    try:
        await deliver(bot, message.chat.id, outgoing, buttons)
    except TelegramBadRequest:
        await state.update_data({DRAFT_KEY: None})
        await message.answer(texts.ADMIN_ORDER_MESSAGE_UNSUPPORTED)


@router.callback_query(AdminClient.message, _cb(ClientAdminAction.SEND))
async def send_to_client(callback: CallbackQuery, callback_data: ClientAdminCb, state: FSMContext,
                         session: AsyncSession, client: Client, bot: Bot) -> None:
    data = await state.get_data()
    token, draft = data.get(TOKEN_KEY), data.get(DRAFT_KEY)
    if (data.get(CLIENT_KEY) != callback_data.item_id or not token or token != callback_data.value or not draft
            or not send_once.claim(token)):
        await callback.answer(texts.ADMIN_ORDER_EXPIRED, show_alert=True)
        return
    await callback.answer()
    await state.clear()
    try:
        await callback.message.edit_reply_markup(reply_markup=None)  # предпросмотр остаётся, кнопки - нет
    except TelegramBadRequest:
        pass
    card = await _service(session, client).card(callback_data.item_id)
    if card is None:
        await callback.message.answer(texts.ADMIN_CLIENT_UNAVAILABLE)
        return
    delivery = await deliver_to_client(bot, session, card.client, Outgoing.from_data(draft))
    log_admin_action(client.telegram_id, "клиенты", "клиент #%s: сообщение - %s", card.client.id, delivery.value)
    await callback.message.answer(admin_orders_views.delivery_report(delivery, card.client),
                                  reply_markup=views.to_client(card.client.id, data.get(BACK_KEY, "")))


# --- Правка аванса: «+5000 причина» → «Да» ---------------------------------------------------------------------

@router.callback_query(_cb(ClientAdminAction.ADVANCE))
async def ask_advance(callback: CallbackQuery, callback_data: ClientAdminCb, state: FSMContext,
                      session: AsyncSession, client: Client) -> None:
    card = await _service(session, client).card(callback_data.item_id)
    if card is None:
        await callback.answer(texts.ADMIN_CLIENT_UNAVAILABLE, show_alert=True)
        return
    await callback.answer()
    await state.clear()
    await state.set_state(AdminClient.advance)
    await state.update_data({CLIENT_KEY: card.client.id, BACK_KEY: callback_data.value})
    await _ask(callback.message, state, *views.ask_advance(card.client, card.advance, callback_data.value))


@router.message(AdminClient.advance, F.text)
async def on_advance(message: Message, state: FSMContext, session: AsyncSession, client: Client) -> None:
    change = parse_advance(message.text)
    if isinstance(change, AdvanceInputError):
        errors = {
            AdvanceInputError.INVALID: texts.ADMIN_CLIENT_ADVANCE_INVALID,
            AdvanceInputError.NO_REASON: texts.ADMIN_CLIENT_ADVANCE_NO_REASON,
            AdvanceInputError.TOO_LONG: texts.ADMIN_CLIENT_ADVANCE_TOO_LONG.format(limit=ADVANCE_REASON_LIMIT),
        }
        await message.answer(errors[change])
        return
    data = await state.get_data()
    card = await _service(session, client).card(data.get(CLIENT_KEY, 0))
    if card is None:
        await state.clear()
        await message.answer(texts.ADMIN_CLIENT_UNAVAILABLE)
        return
    if card.advance + change.amount < 0:
        await message.answer(texts.ADMIN_CLIENT_ADVANCE_NEGATIVE.format(balance=format_rub(card.advance)))
        return
    await _strip_prompt(message, state)
    token = secrets.token_hex(8)
    await state.update_data({ADVANCE_KEY: [change.amount, change.reason], TOKEN_KEY: token})
    await send_screen(message, *views.advance_confirm(card.client, change, card.advance, token,
                                                      data.get(BACK_KEY, "")))


@router.callback_query(AdminClient.advance, _cb(ClientAdminAction.ADVANCE_OK))
async def save_advance(callback: CallbackQuery, callback_data: ClientAdminCb, state: FSMContext,
                       session: AsyncSession, client: Client, settings: Settings) -> None:
    data = await state.get_data()
    token, saved = data.get(TOKEN_KEY), data.get(ADVANCE_KEY)
    # Ключ в кнопке и одноразовая отметка: двойное нажатие «Да» не запишет правку дважды.
    if (data.get(CLIENT_KEY) != callback_data.item_id or not token or token != callback_data.value or not saved
            or not send_once.claim(f"advance:{token}")):
        await callback.answer(texts.ADMIN_ORDER_EXPIRED, show_alert=True)
        return
    change = AdvanceInput(*saved)
    result = await _service(session, client).adjust_advance(callback_data.item_id, change.amount, change.reason)
    await state.clear()
    if result.outcome is AdvanceOutcome.NEGATIVE:
        await callback.answer(texts.ADMIN_CLIENT_ADVANCE_NEGATIVE.format(balance=format_rub(result.balance)),
                              show_alert=True)
        return
    if result.outcome is AdvanceOutcome.NOT_FOUND:
        await callback.answer(texts.ADMIN_CLIENT_UNAVAILABLE, show_alert=True)
        return
    await callback.answer()
    await _show_client(callback.message, state, session, client, settings, callback_data.item_id,
                       data.get(BACK_KEY, ""),
                       notice=texts.ADMIN_CLIENT_ADVANCE_SAVED.format(balance=format_rub(result.balance)))


@router.message(AdminClient.advance)
async def advance_expects_text(message: Message) -> None:
    await message.answer(texts.ADMIN_CLIENT_ADVANCE_INVALID)


# --- Участники проведения --------------------------------------------------------------------------------------

@router.callback_query(_cb(ClientAdminAction.PARTICIPANTS))
@router.callback_query(_cb(ClientAdminAction.CANCELLED))
async def show_participants(callback: CallbackQuery, callback_data: ClientAdminCb, state: FSMContext,
                            session: AsyncSession, client: Client) -> None:
    view = await _service(session, client).participants(callback_data.item_id)
    if view is None:
        await callback.answer(texts.ADMIN_NOT_FOUND, show_alert=True)
        return
    await callback.answer()
    await state.clear()
    cancelled = callback_data.action is ClientAdminAction.CANCELLED
    await picker.show(callback.message, state,
                      views.cancelled_participants(view) if cancelled else views.participants(view))


# --- Листы ожидания курсов -------------------------------------------------------------------------------------

@router.callback_query(_cb(ClientAdminAction.WAITLISTS))
async def show_waitlists(callback: CallbackQuery, state: FSMContext, session: AsyncSession, client: Client) -> None:
    await callback.answer()
    await state.clear()
    await picker.show(callback.message, state, views.waitlists(await _service(session, client).waitlists()))


@picker.opener(views.PICK_WAITLIST)
async def open_course_waitlist(message: Message, state: FSMContext, session: AsyncSession, client: Client,
                               settings: Settings, program_id: int, context: dict) -> None:
    """Номер курса из «Листов ожидания курсов» - кто его ждёт."""
    program, rows = await _service(session, client).course_waitlist(program_id)
    if program is None:
        await message.answer(texts.ADMIN_NOT_FOUND)
        return
    await picker.show(message, state, views.course_waitlist(program, rows, settings.timezone, from_course=False),
                      in_place=False)


@router.callback_query(_cb(ClientAdminAction.WAITLIST))
async def show_course_waitlist(callback: CallbackQuery, callback_data: ClientAdminCb, state: FSMContext,
                               session: AsyncSession, client: Client, settings: Settings) -> None:
    program, rows = await _service(session, client).course_waitlist(callback_data.item_id)
    if program is None:
        await callback.answer(texts.ADMIN_NOT_FOUND, show_alert=True)
        return
    await callback.answer()
    await state.clear()
    from_course = callback_data.value == views.FROM_COURSE
    await picker.show(callback.message, state, views.course_waitlist(program, rows, settings.timezone, from_course))


@router.callback_query(_cb(ClientAdminAction.INVITED))
async def ask_invited(callback: CallbackQuery, callback_data: ClientAdminCb, session: AsyncSession,
                      client: Client) -> None:
    program, rows = await _service(session, client).course_waitlist(callback_data.item_id)
    if program is None or not rows:
        await callback.answer(texts.ADMIN_NOT_FOUND, show_alert=True)
        return
    await callback.answer()
    await show_screen(callback.message, *views.invited_confirm(program, len(rows),
                                                               callback_data.value == views.FROM_COURSE))


@router.callback_query(_cb(ClientAdminAction.INVITED_OK))
async def mark_invited(callback: CallbackQuery, callback_data: ClientAdminCb, state: FSMContext,
                       session: AsyncSession, client: Client, settings: Settings) -> None:
    service = _service(session, client)
    count = await service.mark_invited(callback_data.item_id)
    program, rows = await service.course_waitlist(callback_data.item_id)
    if program is None:
        await callback.answer(texts.ADMIN_NOT_FOUND, show_alert=True)
        return
    await callback.answer()
    await picker.show(callback.message, state,
                      views.course_waitlist(program, rows, settings.timezone, callback_data.value == views.FROM_COURSE),
                      notice=texts.ADMIN_INVITED_DONE.format(count=count))
