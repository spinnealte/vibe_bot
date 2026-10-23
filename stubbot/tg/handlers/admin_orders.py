"""Раздел «🧾 Заказы» в админке (полная версия, этап 1, шаг 4): разделы, карточка заказа, отметка оплаты, сумма
заказа, счёт и сообщение клиенту, снятие брони, аванс. Все проверки и деньги - в AdminOrderService.

Только для админов: IsAdmin на весь роутер. Подключается вместе с записью на курсы (APPLICATIONS_ENABLED).
Ответ кнопкой - в том же сообщении; ответ текстом или файлом - новым экраном, у вопроса убираем кнопку.
«🧾 Открыть заказ» под уведомлением присылает карточку новым сообщением - уведомление остаётся в чате.
"""

import logging
import secrets
from datetime import UTC, datetime

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from stubbot.config import Settings
from stubbot.db.enums import MANUAL_PAYMENT_METHODS, OrderStatus
from stubbot.db.models import Client
from stubbot.services.admin_log import log_admin_action
from stubbot.services.admin_orders import ActionResult, AdminOrderCard, AdminOrderService, OrderCategory, Outcome
from stubbot.tg import admin_orders_views as views
from stubbot.tg import render, texts
from stubbot.tg.callbacks import OrderAdminAction, OrderAdminCb
from stubbot.tg.filters import IsAdmin
from stubbot.tg.flows import with_notice
from stubbot.tg.outgoing import Delivery, Outgoing, OutgoingKind, deliver, deliver_to_client, from_message, send_once
from stubbot.tg.render import visible_length
from stubbot.tg.screen import send_screen, show_screen, strip_keyboard
from stubbot.tg.states import AdminOrder
from stubbot.utils.dates import local_today
from stubbot.utils.money import format_rub
from stubbot.utils.schedule_input import parse_rubles

logger = logging.getLogger(__name__)

router = Router(name="admin_orders")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())

# FSM data.
ORDER_KEY = "order_id"
AMOUNT_KEY = "amount"
TOKEN_KEY = "token"  # одноразовый ключ: отметка оплаты (уникальный ключ платежа) или отправка сообщения
DRAFT_KEY = "draft"
KIND_KEY = "kind"
PROMPT_KEY = "order_prompt_id"
KIND_INVOICE = "invoice"
KIND_MESSAGE = "message"


def _service(session: AsyncSession, client: Client) -> AdminOrderService:
    return AdminOrderService(session, client.telegram_id)


def _now() -> datetime:
    return datetime.now(UTC)


def _order_cb(action: OrderAdminAction):
    return OrderAdminCb.filter(F.action == action)


async def _card(callback: CallbackQuery, session: AsyncSession, client: Client) -> AdminOrderCard | None:
    """Карточка заказа из кнопки; нет такого заказа (подделанная кнопка) - всплывающее окно."""
    order_id = OrderAdminCb.unpack(callback.data).order_id
    card = await _service(session, client).card(order_id)
    if card is None:
        await callback.answer(texts.ADMIN_ORDER_UNAVAILABLE, show_alert=True)
    return card


async def _show_card(message: Message, session: AsyncSession, client: Client, settings: Settings, order_id: int,
                     notice: str | None = None, in_place: bool = True) -> None:
    card = await _service(session, client).card(order_id)
    if card is None:
        await send_screen(message, texts.ADMIN_ORDER_UNAVAILABLE)
        return
    text, markup = views.order_card(card, settings.timezone, _now())
    await (show_screen if in_place else send_screen)(message, with_notice(notice, text), markup)


async def _ask(message: Message, state: FSMContext, text: str, markup, in_place: bool = True) -> None:
    """Вопрос, на который ответят текстом или файлом: запоминаем его, чтобы потом убрать у него кнопку."""
    prompt = await (show_screen if in_place else send_screen)(message, text, markup)
    await state.update_data({PROMPT_KEY: prompt.message_id})


async def _strip_prompt(message: Message, state: FSMContext) -> None:
    prompt_id = (await state.get_data()).get(PROMPT_KEY)
    if prompt_id:
        await strip_keyboard(message, prompt_id)


async def _tell_client(bot: Bot, client: Client, text: str) -> None:
    """Сообщение клиенту от имени бота; не дошло (заблокировал бота) - только в лог, действие админа уже сделано."""
    if client.telegram_id is None:
        return
    try:
        await bot.send_message(client.telegram_id, text)
    except (TelegramForbiddenError, TelegramBadRequest) as error:
        logger.info("Клиенту #%s не доставлено сообщение о заказе: %s", client.id, type(error).__name__)


# --- Разделы, список, карточка, поиск --------------------------------------------------------------------------

@router.callback_query(_order_cb(OrderAdminAction.MENU))
async def orders_menu(callback: CallbackQuery, state: FSMContext, session: AsyncSession, client: Client,
                      settings: Settings) -> None:
    await callback.answer()
    await state.clear()
    counts = await _service(session, client).counts(local_today(settings.timezone), _now())
    await show_screen(callback.message, *views.orders_menu(counts))


@router.callback_query(_order_cb(OrderAdminAction.LIST))
async def orders_list(callback: CallbackQuery, callback_data: OrderAdminCb, state: FSMContext,
                      session: AsyncSession, client: Client, settings: Settings) -> None:
    try:
        category = OrderCategory(callback_data.value)
    except ValueError:
        await callback.answer(texts.UNKNOWN_CALLBACK)
        return
    await callback.answer()
    await state.clear()
    rows = await _service(session, client).category(category, local_today(settings.timezone), _now())
    await show_screen(callback.message, *views.order_list(category, rows, callback_data.page))


@router.callback_query(_order_cb(OrderAdminAction.NOOP))
async def on_counter(callback: CallbackQuery) -> None:
    await callback.answer()


@router.callback_query(_order_cb(OrderAdminAction.VIEW))
async def view_order(callback: CallbackQuery, callback_data: OrderAdminCb, state: FSMContext,
                     session: AsyncSession, client: Client, settings: Settings) -> None:
    card = await _card(callback, session, client)
    if card is None:
        return
    await callback.answer()
    await state.clear()
    text, markup = views.order_card(card, settings.timezone, _now())
    # Из уведомления - новым сообщением: само уведомление (и файл реквизитов) остаётся в чате.
    new = callback_data.value == views.NEW_SCREEN or callback.message.text is None
    await (send_screen if new else show_screen)(callback.message, text, markup)


@router.callback_query(_order_cb(OrderAdminAction.FIND))
async def ask_order_number(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.clear()
    await state.set_state(AdminOrder.number)
    await _ask(callback.message, state, *views.ask_number())


@router.message(AdminOrder.number, F.text)
async def on_order_number(message: Message, state: FSMContext, session: AsyncSession, client: Client,
                          settings: Settings) -> None:
    number = message.text.strip().lstrip("№").strip()
    if not (number.isdigit() and len(number) == 6):
        await message.answer(texts.ADMIN_ORDER_BAD_NUMBER)
        return
    order = await _service(session, client).find(int(number))
    if order is None:
        await message.answer(texts.ADMIN_ORDER_NOT_FOUND.format(number=number))
        return
    await _strip_prompt(message, state)
    await state.clear()
    await _show_card(message, session, client, settings, order.id, in_place=False)


@router.callback_query(_order_cb(OrderAdminAction.CANCEL))
async def cancel_input(callback: CallbackQuery, callback_data: OrderAdminCb, state: FSMContext,
                       session: AsyncSession, client: Client, settings: Settings) -> None:
    """«✖️ Отмена» под вопросом или предпросмотром - назад к карточке заказа."""
    await callback.answer()
    await state.clear()
    await _show_card(callback.message, session, client, settings, callback_data.order_id)


# --- Оплата: сумма → способ (он же подтверждение) --------------------------------------------------------------

@router.callback_query(_order_cb(OrderAdminAction.PAY))
async def ask_amount(callback: CallbackQuery, state: FSMContext, session: AsyncSession, client: Client) -> None:
    card = await _card(callback, session, client)
    if card is None:
        return
    if not card.payable:
        await callback.answer(texts.ADMIN_ORDER_UNAVAILABLE, show_alert=True)
        return
    await callback.answer()
    await state.clear()
    await state.set_state(AdminOrder.amount)
    await state.update_data({ORDER_KEY: card.order.id})
    await _ask(callback.message, state, *views.ask_amount(card))


@router.callback_query(AdminOrder.amount, _order_cb(OrderAdminAction.FULL))
async def pay_in_full(callback: CallbackQuery, callback_data: OrderAdminCb, state: FSMContext,
                      session: AsyncSession, client: Client) -> None:
    card = await _card(callback, session, client)
    if card is None:
        return
    if (await state.get_data()).get(ORDER_KEY) != card.order.id or not card.payable or not card.left:
        await callback.answer(texts.ADMIN_ORDER_UNAVAILABLE, show_alert=True)
        return
    await callback.answer()
    await _ask_method(callback.message, state, card, card.left)


@router.message(AdminOrder.amount, F.text)
async def on_amount(message: Message, state: FSMContext, session: AsyncSession, client: Client) -> None:
    amount = parse_rubles(message.text)
    if amount is None:
        await message.answer(texts.ADMIN_ORDER_AMOUNT_INVALID)
        return
    card = await _service(session, client).card((await state.get_data()).get(ORDER_KEY, 0))
    if card is None or not card.payable:
        await state.clear()
        await message.answer(texts.ADMIN_ORDER_UNAVAILABLE)
        return
    if card.left is not None and amount > card.left:
        await message.answer(texts.ADMIN_ORDER_AMOUNT_TOO_MUCH.format(left=format_rub(card.left)))
        return
    await _strip_prompt(message, state)
    await _ask_method(message, state, card, amount, in_place=False)


async def _ask_method(message: Message, state: FSMContext, card: AdminOrderCard, amount: int,
                      in_place: bool = True) -> None:
    # Новый ключ на каждую сумму: он едет в кнопках способа и становится уникальным ключом платежа -
    # двойное нажатие не создаст второй платёж, а кнопки прошлого экрана (другая сумма) уже не сработают.
    token = secrets.token_hex(8)
    await state.update_data({AMOUNT_KEY: amount, TOKEN_KEY: token})
    text, markup = views.ask_method(card.order, amount, token)
    await (show_screen if in_place else send_screen)(message, text, markup)


@router.callback_query(AdminOrder.amount, _order_cb(OrderAdminAction.METHOD))
async def on_method(callback: CallbackQuery, callback_data: OrderAdminCb, state: FSMContext, session: AsyncSession,
                    client: Client, settings: Settings, bot: Bot) -> None:
    data = await state.get_data()
    method_value, _, token = callback_data.value.partition(views.TOKEN_SEPARATOR)
    method = next((m for m in MANUAL_PAYMENT_METHODS if m.value == method_value), None)
    if (method is None or data.get(ORDER_KEY) != callback_data.order_id or not data.get(AMOUNT_KEY)
            or not token or token != data.get(TOKEN_KEY)):
        await callback.answer(texts.ADMIN_ORDER_EXPIRED, show_alert=True)
        return
    service = _service(session, client)
    result = await service.mark_payment(callback_data.order_id, data[AMOUNT_KEY], method, token)
    await state.clear()
    if result.outcome is not Outcome.DONE:
        await _answer_refused(callback, result)
        await _show_card(callback.message, session, client, settings, callback_data.order_id)
        return
    await callback.answer()
    card = await service.card(callback_data.order_id)
    await _tell_client(bot, card.payer, render.payment_received(card.session, card.order, result.paid, result.full))
    await _show_card(callback.message, session, client, settings, card.order.id,
                     notice=texts.ADMIN_ORDER_PAYMENT_SAVED.format(amount=format_rub(result.amount)))


async def _answer_refused(callback: CallbackQuery, result: ActionResult) -> None:
    popups = {
        Outcome.DUPLICATE: texts.ADMIN_ORDER_DUPLICATE,
        Outcome.NO_ADVANCE: texts.ADMIN_ORDER_NO_ADVANCE,
        Outcome.TOO_MUCH: texts.ADMIN_ORDER_UNAVAILABLE,  # остаток изменился, пока админ вводил сумму
    }
    await callback.answer(popups.get(result.outcome, texts.ADMIN_ORDER_UNAVAILABLE), show_alert=True)


# --- Сумма заказа («стоимость уточнит менеджер») ---------------------------------------------------------------

@router.callback_query(_order_cb(OrderAdminAction.TOTAL))
async def ask_total(callback: CallbackQuery, state: FSMContext, session: AsyncSession, client: Client) -> None:
    card = await _card(callback, session, client)
    if card is None:
        return
    if not card.payable or card.order.total_amount is not None:
        await callback.answer(texts.ADMIN_ORDER_UNAVAILABLE, show_alert=True)
        return
    await callback.answer()
    await state.clear()
    await state.set_state(AdminOrder.total)
    await state.update_data({ORDER_KEY: card.order.id})
    await _ask(callback.message, state, *views.ask_total(card.order))


@router.message(AdminOrder.total, F.text)
async def on_total(message: Message, state: FSMContext, session: AsyncSession, client: Client,
                   settings: Settings) -> None:
    amount = parse_rubles(message.text)
    if amount is None:
        await message.answer(texts.ADMIN_ORDER_AMOUNT_INVALID)
        return
    service = _service(session, client)
    order_id = (await state.get_data()).get(ORDER_KEY, 0)
    if await service.set_total(order_id, amount) is None:
        card = await service.card(order_id)
        if card is not None and card.payable and card.order.total_amount is None and amount < card.paid:
            await message.answer(texts.ADMIN_ORDER_TOTAL_INVALID.format(paid=format_rub(card.paid)))
            return
        await state.clear()
        await message.answer(texts.ADMIN_ORDER_UNAVAILABLE)
        return
    await _strip_prompt(message, state)
    await state.clear()
    await _show_card(message, session, client, settings, order_id, in_place=False,
                     notice=texts.ADMIN_ORDER_TOTAL_SAVED.format(amount=format_rub(amount)))


@router.message(AdminOrder.number)
async def number_expects_text(message: Message) -> None:
    await message.answer(texts.ADMIN_ORDER_BAD_NUMBER)


@router.message(AdminOrder.amount)
@router.message(AdminOrder.total)
async def amount_expects_text(message: Message) -> None:
    await message.answer(texts.ADMIN_ORDER_AMOUNT_INVALID)


# --- Счёт и сообщение клиенту: файл или текст → предпросмотр → «Отправить» ------------------------------------

@router.callback_query(_order_cb(OrderAdminAction.INVOICE))
async def ask_invoice(callback: CallbackQuery, state: FSMContext, session: AsyncSession, client: Client) -> None:
    card = await _card(callback, session, client)
    if card is None:
        return
    if not card.payable:
        await callback.answer(texts.ADMIN_ORDER_UNAVAILABLE, show_alert=True)
        return
    await _start_message(callback, state, card, KIND_INVOICE)


@router.callback_query(_order_cb(OrderAdminAction.WRITE))
async def ask_message(callback: CallbackQuery, state: FSMContext, session: AsyncSession, client: Client) -> None:
    card = await _card(callback, session, client)
    if card is not None:
        await _start_message(callback, state, card, KIND_MESSAGE)


async def _start_message(callback: CallbackQuery, state: FSMContext, card: AdminOrderCard, kind: str) -> None:
    await callback.answer()
    await state.clear()
    await state.set_state(AdminOrder.message)
    await state.update_data({ORDER_KEY: card.order.id, KIND_KEY: kind})
    screen = views.ask_invoice(card) if kind == KIND_INVOICE else views.ask_message(card)
    await _ask(callback.message, state, *screen)


@router.message(AdminOrder.message)
async def on_message_content(message: Message, state: FSMContext, session: AsyncSession, client: Client,
                             settings: Settings, bot: Bot) -> None:
    data = await state.get_data()
    invoice = data.get(KIND_KEY) == KIND_INVOICE
    outgoing = from_message(message)
    if outgoing is None or (invoice and outgoing.kind is OutgoingKind.TEXT):
        await message.answer(texts.ADMIN_ORDER_INVOICE_FILE_ONLY if invoice else texts.ADMIN_ORDER_MESSAGE_UNSUPPORTED)
        return
    card = await _service(session, client).card(data.get(ORDER_KEY, 0))
    if card is None:
        await state.clear()
        await message.answer(texts.ADMIN_ORDER_UNAVAILABLE)
        return
    if invoice:
        # У счёта своя подпись: номер заказа, сколько оплатить и до когда; подпись админа - ниже.
        caption = render.invoice_caption(card.order, card.left, settings.timezone)
        outgoing = Outgoing(outgoing.kind, caption + (f"\n\n{outgoing.text}" if outgoing.text else ""),
                            outgoing.file_id)
    over = visible_length(outgoing.text) - outgoing.limit
    if over > 0:
        await message.answer(texts.ADMIN_ORDER_TOO_LONG.format(over=over))
        return
    await _strip_prompt(message, state)
    token = secrets.token_hex(8)
    await state.update_data({DRAFT_KEY: outgoing.to_data(), TOKEN_KEY: token})
    await message.answer(views.preview_note(card.payer))
    try:
        await deliver(bot, message.chat.id, outgoing, views.preview_buttons(card.order.id, token))
    except TelegramBadRequest:
        await state.update_data({DRAFT_KEY: None})
        await message.answer(texts.ADMIN_ORDER_MESSAGE_UNSUPPORTED)


@router.callback_query(AdminOrder.message, _order_cb(OrderAdminAction.SEND))
async def send_to_client(callback: CallbackQuery, callback_data: OrderAdminCb, state: FSMContext,
                         session: AsyncSession, client: Client, bot: Bot) -> None:
    data = await state.get_data()
    token, draft = data.get(TOKEN_KEY), data.get(DRAFT_KEY)
    if (data.get(ORDER_KEY) != callback_data.order_id or not token or token != callback_data.value or not draft
            or not send_once.claim(token)):
        await callback.answer(texts.ADMIN_ORDER_EXPIRED, show_alert=True)
        return
    await callback.answer()
    await state.clear()
    try:
        await callback.message.edit_reply_markup(reply_markup=None)  # предпросмотр остаётся, кнопки - нет
    except TelegramBadRequest:
        pass
    service = _service(session, client)
    card = await service.card(callback_data.order_id)
    delivery = await deliver_to_client(bot, session, card.payer, Outgoing.from_data(draft))
    if delivery is Delivery.DELIVERED and data.get(KIND_KEY) == KIND_INVOICE:
        await service.mark_invoice_sent(card.order.id)
    log_admin_action(client.telegram_id, "заказ", "№%s: сообщение клиенту (%s) - %s", card.order.number,
                     data.get(KIND_KEY), delivery.value)
    await callback.message.answer(views.delivery_report(delivery, card.payer),
                                  reply_markup=views.to_card(card.order.id))


# --- Реквизиты -------------------------------------------------------------------------------------------------

@router.callback_query(_order_cb(OrderAdminAction.REQUISITES))
async def send_requisites(callback: CallbackQuery, session: AsyncSession, client: Client, bot: Bot) -> None:
    card = await _card(callback, session, client)
    if card is None:
        return
    file_id = card.order.requisites_file_id
    if not file_id:
        await callback.answer(texts.ADMIN_ORDER_NO_REQUISITES, show_alert=True)
        return
    await callback.answer()
    caption, markup = views.notify_requisites(card.order, card.payer)
    chat_id = callback.message.chat.id
    try:
        await bot.send_document(chat_id, file_id, caption=caption, reply_markup=markup)
    except TelegramBadRequest:
        await bot.send_photo(chat_id, file_id, caption=caption, reply_markup=markup)  # прислали фото карточки


# --- Снятие брони ----------------------------------------------------------------------------------------------

@router.callback_query(_order_cb(OrderAdminAction.REMOVE))
async def ask_remove(callback: CallbackQuery, callback_data: OrderAdminCb, state: FSMContext,
                     session: AsyncSession, client: Client) -> None:
    card = await _card(callback, session, client)
    if card is None:
        return
    if card.order.status is OrderStatus.CANCELLED:
        await callback.answer(texts.ADMIN_ORDER_UNAVAILABLE, show_alert=True)
        return
    await callback.answer()
    await state.clear()
    # Из уведомления «просрочена оплата» - вопрос новым сообщением, уведомление остаётся в чате.
    new = callback_data.value == views.NEW_SCREEN or callback.message.text is None
    await (send_screen if new else show_screen)(callback.message, *views.remove_confirm(card))


@router.callback_query(_order_cb(OrderAdminAction.REMOVE_OK))
async def remove_booking(callback: CallbackQuery, callback_data: OrderAdminCb, session: AsyncSession,
                         client: Client, settings: Settings, bot: Bot) -> None:
    service = _service(session, client)
    result = await service.remove_booking(callback_data.order_id)
    if result.outcome is not Outcome.DONE:
        await _answer_refused(callback, result)
        return
    await callback.answer()
    card = await service.card(callback_data.order_id)
    for _, person in result.participants:
        if person.id == card.payer.id:
            await _tell_client(bot, person, render.booking_removed(card.session, card.order, result.paid))
        else:
            await _tell_client(bot, person, render.booking_removed_colleague(card.session))
    await _show_card(callback.message, session, client, settings, card.order.id, notice=texts.ADMIN_ORDER_REMOVED)


# --- Аванс -----------------------------------------------------------------------------------------------------

@router.callback_query(_order_cb(OrderAdminAction.CREDIT))
@router.callback_query(_order_cb(OrderAdminAction.REFUND))
async def ask_settle_money(callback: CallbackQuery, callback_data: OrderAdminCb, state: FSMContext,
                           session: AsyncSession, client: Client) -> None:
    card = await _card(callback, session, client)
    if card is None:
        return
    if not card.money_pending:
        await callback.answer(texts.ADMIN_ORDER_UNAVAILABLE, show_alert=True)
        return
    await callback.answer()
    await state.clear()
    credit = callback_data.action is OrderAdminAction.CREDIT
    await show_screen(callback.message, *(views.credit_confirm(card) if credit else views.refund_confirm(card)))


@router.callback_query(_order_cb(OrderAdminAction.CREDIT_OK))
async def credit_advance(callback: CallbackQuery, callback_data: OrderAdminCb, session: AsyncSession,
                         client: Client, settings: Settings, bot: Bot) -> None:
    service = _service(session, client)
    result = await service.credit_advance(callback_data.order_id)
    if result.outcome is not Outcome.DONE:
        await _answer_refused(callback, result)
        return
    await callback.answer()
    card = await service.card(callback_data.order_id)
    await _tell_client(bot, card.payer, render.advance_credited(result.amount))
    await _show_card(callback.message, session, client, settings, card.order.id,
                     notice=texts.ADMIN_ORDER_CREDITED.format(amount=format_rub(result.amount),
                                                              balance=format_rub(result.balance)))


@router.callback_query(_order_cb(OrderAdminAction.REFUND_OK))
async def mark_refunded(callback: CallbackQuery, callback_data: OrderAdminCb, session: AsyncSession,
                        client: Client, settings: Settings) -> None:
    result = await _service(session, client).mark_refunded(callback_data.order_id)
    if result.outcome is not Outcome.DONE:
        await _answer_refused(callback, result)
        return
    await callback.answer()
    await _show_card(callback.message, session, client, settings, callback_data.order_id,
                     notice=texts.ADMIN_ORDER_REFUNDED.format(amount=format_rub(result.amount)))


@router.callback_query(_order_cb(OrderAdminAction.APPLY))
async def ask_apply_advance(callback: CallbackQuery, state: FSMContext, session: AsyncSession,
                            client: Client) -> None:
    card = await _card(callback, session, client)
    if card is None:
        return
    if not card.advance_to_apply:
        await callback.answer(texts.ADMIN_ORDER_NO_ADVANCE, show_alert=True)
        return
    await callback.answer()
    await state.clear()
    await show_screen(callback.message, *views.apply_confirm(card))


@router.callback_query(_order_cb(OrderAdminAction.APPLY_OK))
async def apply_advance(callback: CallbackQuery, callback_data: OrderAdminCb, session: AsyncSession,
                        client: Client, settings: Settings, bot: Bot) -> None:
    service = _service(session, client)
    result = await service.apply_advance(callback_data.order_id)
    if result.outcome is not Outcome.DONE:
        await _answer_refused(callback, result)
        return
    await callback.answer()
    card = await service.card(callback_data.order_id)
    await _tell_client(bot, card.payer, render.payment_received(card.session, card.order, result.paid, result.full,
                                                                from_advance=result.amount))
    await _show_card(callback.message, session, client, settings, card.order.id,
                     notice=texts.ADMIN_ORDER_APPLIED.format(amount=format_rub(result.amount)))
