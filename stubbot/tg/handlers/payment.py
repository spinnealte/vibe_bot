"""Онлайн-оплата заказа клиентом (полная версия, этап 1, шаг 7): «💳 Оплатить онлайн» → ссылка на страницу ЮKassa
(при ONLINE_PAYMENTS=fake - кнопки «Оплата прошла / не прошла») → «🔄 Проверить оплату» или подтверждение от фоновой
сверки. Кнопка «Оплатить онлайн» стоит под «Оплата как физическое лицо», в «Моих записях» и в напоминании об оплате.

Подключается вместе с записью на курсы; при ONLINE_PAYMENTS=off кнопок нет, а старая кнопка отвечает «недоступна».
Сначала отвечаем на нажатие (запрос к ЮKassa бывает долгим), потом показываем экран вместо сообщения с кнопкой.
"""

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from stubbot.config import Settings
from stubbot.db.enums import PaymentState
from stubbot.db.models import Client
from stubbot.payments import receipts_enabled
from stubbot.services.online_payments import Applied, StartOutcome
from stubbot.services.registration import OptionalField, RegistrationService
from stubbot.tg import keyboards, render, texts
from stubbot.tg.callbacks import PayAction, PayCb
from stubbot.tg.flows import with_notice
from stubbot.tg.online_payments import OnlinePayments
from stubbot.tg.screen import send_screen, show_screen
from stubbot.tg.states import PayOnline
from stubbot.utils.profile_fields import parse_email

router = Router(name="payment")

ORDER_KEY = "pay_order_id"
INDEX_KEY = "pay_index"


@router.callback_query(PayCb.filter(F.action == PayAction.START))
async def start_online_payment(callback: CallbackQuery, callback_data: PayCb, state: FSMContext, client: Client,
                               settings: Settings, online_payments: OnlinePayments | None) -> None:
    if online_payments is None:
        await callback.answer(texts.PAY_ONLINE_OFF, show_alert=True)
        return
    await callback.answer()
    await state.clear()
    await _start(callback.message, state, settings, client.id, client.email, online_payments, callback_data.item_id,
                 callback_data.index)


@router.message(PayOnline.email, F.text)
async def on_email(message: Message, state: FSMContext, client: Client, settings: Settings,
                   online_payments: OnlinePayments | None) -> None:
    """E-mail для чека: сохраняем в профиль (как из кабинета) и продолжаем оплату."""
    email = parse_email(message.text)
    if email is None:
        await message.answer(texts.PAY_ONLINE_EMAIL_INVALID)
        return
    RegistrationService.save_optional(client, OptionalField.EMAIL, email)
    data = await state.get_data()
    await state.clear()
    if online_payments is None:
        await message.answer(texts.PAY_ONLINE_OFF)
        return
    await _start(message, state, settings, client.id, email, online_payments, data.get(ORDER_KEY, 0),
                 data.get(INDEX_KEY, 0), in_place=False)


@router.message(PayOnline.email)
async def email_expects_text(message: Message) -> None:
    await message.answer(texts.PAY_ONLINE_EMAIL_INVALID)


async def _start(message: Message, state: FSMContext, settings: Settings, client_id: int, email: str | None,
                 payments: OnlinePayments, order_id: int, index: int, in_place: bool = True) -> None:
    show = show_screen if in_place else send_screen
    started = await payments.start(client_id, email, order_id)
    match started.outcome:
        case StartOutcome.READY:
            text = render.pay_online(started.order_number, started.title, started.amount,
                                     _receipt_email(settings, email), payments.has_payment_page)
            await show(message, text, keyboards.pay_online(started.payment_id, started.url, index,
                                                           payments.has_payment_page))
        case StartOutcome.NEED_EMAIL:
            await state.set_state(PayOnline.email)
            await state.update_data({ORDER_KEY: order_id, INDEX_KEY: index})
            await show(message, texts.PAY_ONLINE_ASK_EMAIL, keyboards.to_my_applications(index))
        case StartOutcome.NO_AMOUNT:
            await show(message, texts.PAY_ONLINE_NO_AMOUNT.format(number=started.order_number),
                       keyboards.to_my_applications(index))
        case StartOutcome.NOTHING_LEFT:
            await show(message, texts.PAY_ONLINE_NOTHING_LEFT.format(number=started.order_number),
                       keyboards.to_my_applications(index))
        case StartOutcome.ERROR:
            await show(message, texts.PAY_ONLINE_ERROR.format(number=started.order_number),
                       keyboards.pay_online_retry(order_id, index))
        case _:
            await show(message, texts.ORDER_UNAVAILABLE, keyboards.to_my_applications(index))


def _receipt_email(settings: Settings, email: str | None) -> str | None:
    """«Чек придёт на …» - только когда чек действительно передаётся в ЮKassa."""
    return email if receipts_enabled(settings) else None


@router.callback_query(PayCb.filter(F.action == PayAction.CHECK))
async def check_online_payment(callback: CallbackQuery, callback_data: PayCb, client: Client, settings: Settings,
                               online_payments: OnlinePayments | None) -> None:
    if online_payments is None:
        await callback.answer(texts.PAY_ONLINE_OFF, show_alert=True)
        return
    await callback.answer()
    applied = await online_payments.check(callback_data.item_id, client_id=client.id)
    await _show_result(callback.message, client, settings, online_payments, applied, callback_data.index)


@router.callback_query(PayCb.filter(F.action.in_({PayAction.FAKE_OK, PayAction.FAKE_FAIL})))
async def finish_fake_payment(callback: CallbackQuery, callback_data: PayCb, client: Client, settings: Settings,
                              online_payments: OnlinePayments | None) -> None:
    """Тестовая оплата (ONLINE_PAYMENTS=fake): нажатие - итог «оплаты». В других режимах кнопка не работает."""
    if online_payments is None or online_payments.has_payment_page:
        await callback.answer(texts.UNKNOWN_CALLBACK)
        return
    await callback.answer()
    applied = await online_payments.finish_fake(callback_data.item_id, client.id,
                                                success=callback_data.action is PayAction.FAKE_OK)
    await _show_result(callback.message, client, settings, online_payments, applied, callback_data.index)


async def _show_result(message: Message, client: Client, settings: Settings, payments: OnlinePayments,
                       applied: Applied | None, index: int) -> None:
    """Итог проверки на месте сообщения с кнопкой: получена, не прошла или ждём (тот же экран со ссылкой)."""
    if applied is None:
        await show_screen(message, texts.ORDER_UNAVAILABLE, keyboards.to_my_applications(index))
        return
    payment, order = applied.payment, applied.order
    match payment.state:
        case PaymentState.SUCCEEDED:
            await show_screen(message, render.online_payment_received(applied), keyboards.to_my_applications(index))
        case PaymentState.CANCELED:
            await show_screen(message, render.pay_online_failed(order.number, payment.cancel_reason),
                              keyboards.pay_online_retry(order.id, index))
        case _:
            text = render.pay_online(order.number, render.session_title(applied.course_session), payment.amount,
                                     _receipt_email(settings, client.email), payments.has_payment_page)
            await show_screen(message, with_notice(texts.PAY_ONLINE_PENDING, text),
                              keyboards.pay_online(payment.id, payment.confirmation_url, index,
                                                   payments.has_payment_page))
