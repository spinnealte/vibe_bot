"""Запись на курс: «📝 Записаться» и «Сообщить о наборе» под карточкой расписания, шаги записи (тариф → для двух
коллег ФИО и телефон коллеги → проверка → «Записаться»), выбор «Физлицо / Юрлицо» и файл с реквизитами юрлица;
«Мои записи» в кабинете и отмена записи. Все проверки - в EnrollmentService и OrderService.

В MVP-афише выключено (APPLICATIONS_ENABLED=false): роутер не подключается, кнопки не показываются.
Шаги по кнопкам меняют одно и то же сообщение; ответы текстом (ФИО, телефон коллеги) - новым сообщением.
"""

import logging
from datetime import UTC, datetime

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from stubbot.config import Settings
from stubbot.db.enums import CancelChoice, ConsentType, PayerType
from stubbot.db.models import Client
from stubbot.services.consents import ConsentService
from stubbot.services.enrollments import (
    PAYABLE_ORDER_STATUSES,
    CancelOutcome,
    CancelResult,
    Colleague,
    EnrollmentService,
    EnrollOutcome,
    EnrollResult,
    MyApplication,
)
from stubbot.services.orders import OrderService
from stubbot.services.registration import RegistrationService, RegistrationStep
from stubbot.services.schedule import ScheduleService
from stubbot.tg import catalog_flow, flows, keyboards, render, texts
from stubbot.tg.callbacks import (
    ApplyAction,
    ApplyCb,
    ApplyPriceCb,
    CabinetAction,
    CabinetCb,
    MyApplicationAction,
    MyApplicationCb,
    OrderAction,
    OrderCb,
    ScheduleAction,
    ScheduleCb,
)
from stubbot.tg.catalog_flow import (
    CAROUSEL_INDEX_KEY,
    COLLEAGUE_NAME_KEY,
    COLLEAGUE_OK_KEY,
    COLLEAGUE_PHONE_KEY,
    ORDER_KEY,
)
from stubbot.tg.formatting import safe
from stubbot.tg.screen import delete_quietly, show_screen
from stubbot.tg.states import PENDING_APPLY_KEY, Application
from stubbot.utils.dates import local_today
from stubbot.utils.links import with_draft_text
from stubbot.utils.names import parse_full_name
from stubbot.utils.phone import normalize_phone

logger = logging.getLogger(__name__)

router = Router(name="application")


@router.callback_query(StateFilter(Application), ApplyCb.filter(F.action == ApplyAction.CANCEL))
async def cancel_enrollment_steps(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    index = (await state.get_data()).get(CAROUSEL_INDEX_KEY, 0)
    await state.clear()
    await show_screen(callback.message, texts.APPLY_CANCELLED, keyboards.to_schedule(index))


@router.callback_query(Application.price, ApplyPriceCb.filter())
async def on_price(callback: CallbackQuery, callback_data: ApplyPriceCb, state: FSMContext, session: AsyncSession,
                   client: Client, settings: Settings) -> None:
    await callback.answer()
    data = await state.get_data()
    today = local_today(settings.timezone)
    card, _ = await EnrollmentService(session).availability(client, data.get(PENDING_APPLY_KEY), today)
    price = next((p for p in card.active_prices if p.id == callback_data.price_id), None) if card else None
    if price is None:
        await state.clear()
        await show_screen(callback.message, texts.APPLY_EXPIRED, keyboards.to_schedule(data.get(CAROUSEL_INDEX_KEY, 0)))
        return
    await state.update_data(price_id=price.id)
    await catalog_flow.after_price(callback.message, state, session, client, today, price)


# --- Коллега (тариф «Для двух коллег») -----------------------------------------------------------------------------

@router.message(Application.colleague_name, F.text)
async def on_colleague_name(message: Message, state: FSMContext) -> None:
    name = parse_full_name(message.text)
    if name is None:
        await message.answer(texts.APPLY_COLLEAGUE_NAME_INVALID)
        return
    await state.update_data({COLLEAGUE_NAME_KEY: name})
    await catalog_flow.ask_colleague_phone(message, state)


@router.message(Application.colleague_phone, F.text)
async def on_colleague_phone(message: Message, state: FSMContext, session: AsyncSession, client: Client,
                             settings: Settings) -> None:
    phone = normalize_phone(message.text)
    if phone is None:
        await message.answer(texts.APPLY_COLLEAGUE_PHONE_INVALID)
        return
    if phone == client.phone:
        await message.answer(texts.APPLY_COLLEAGUE_PHONE_IS_YOURS)
        return
    # Данные коллеги поменялись - галочку согласия ставят заново.
    await state.update_data({COLLEAGUE_PHONE_KEY: phone, COLLEAGUE_OK_KEY: False})
    await catalog_flow.show_confirm(message, state, session, client, local_today(settings.timezone), in_place=False)


@router.message(StateFilter(Application.colleague_name, Application.colleague_phone))
async def colleague_expects_text(message: Message) -> None:
    await message.answer(texts.REG_TEXT_ONLY)


@router.callback_query(Application.confirm, ApplyCb.filter(F.action == ApplyAction.COLLEAGUE_OK))
async def on_colleague_ok(callback: CallbackQuery, state: FSMContext) -> None:
    """Галочка «Коллега согласен на передачу его ФИО и телефона»: каждое нажатие ставит или снимает её."""
    data = await state.get_data()
    if not data.get(COLLEAGUE_NAME_KEY):
        await callback.answer(texts.UNKNOWN_CALLBACK)  # запись не вдвоём - галочки на экране нет
        return
    ok = not data.get(COLLEAGUE_OK_KEY, False)
    await state.update_data({COLLEAGUE_OK_KEY: ok})
    await callback.answer()
    try:
        await callback.message.edit_reply_markup(reply_markup=keyboards.apply_confirm(pair=True, colleague_ok=ok))
    except TelegramBadRequest:
        pass  # кнопки уже такие (два нажатия подряд) - менять нечего


# --- «Записаться» -------------------------------------------------------------------------------------------------

@router.callback_query(Application.confirm, ApplyCb.filter(F.action == ApplyAction.SEND))
async def on_send(callback: CallbackQuery, state: FSMContext, session: AsyncSession, client: Client,
                  settings: Settings, bot: Bot) -> None:
    data = await state.get_data()
    back = keyboards.to_schedule(data.get(CAROUSEL_INDEX_KEY, 0))
    if PENDING_APPLY_KEY not in data:
        await callback.answer()
        await state.clear()
        await show_screen(callback.message, texts.APPLY_EXPIRED, back)
        return
    colleague = None
    if data.get(COLLEAGUE_NAME_KEY):
        if not data.get(COLLEAGUE_OK_KEY):
            await callback.answer(texts.APPLY_COLLEAGUE_OK_REQUIRED, show_alert=True)
            return
        colleague = Colleague(data[COLLEAGUE_NAME_KEY], data[COLLEAGUE_PHONE_KEY])
    await callback.answer()
    await state.clear()
    result = await EnrollmentService(session).enroll(client, data[PENDING_APPLY_KEY], data.get("price_id"), colleague,
                                                     local_today(settings.timezone))
    message = callback.message
    match result.outcome:
        case EnrollOutcome.CREATED:
            colleague_name = (result.colleague.full_name or colleague.full_name) if result.colleague else None
            await show_screen(message, render.enrolled(result.card, result.order, colleague_name, settings.timezone),
                              keyboards.payer_choice(result.order.id))
            if result.colleague is not None:
                await _tell_about_colleague(message, bot, result, client)
            return
        case EnrollOutcome.WAITLIST:
            text = texts.APPLY_WAITLISTED_PAIR if colleague else texts.APPLY_WAITLISTED
        case EnrollOutcome.DUPLICATE:
            status = result.enrollment.status.value if result.enrollment else "application"
            text = texts.APPLY_DUPLICATE.format(status=texts.ENROLLMENT_STATUS_LABELS[status])
        case EnrollOutcome.COLLEAGUE_DUPLICATE:
            text = texts.APPLY_COLLEAGUE_DUPLICATE
        case EnrollOutcome.COLLEAGUE_IS_SELF:
            text = texts.APPLY_COLLEAGUE_IS_SELF
        case EnrollOutcome.CLOSED:
            text = texts.APPLY_CLOSED
        case EnrollOutcome.BUSY:
            text = texts.APPLY_BUSY
        case _:
            text = texts.APPLY_EXPIRED
    await show_screen(message, text, back)


async def _tell_about_colleague(message: Message, bot: Bot, result: EnrollResult, client: Client) -> None:
    """Коллега уже в боте - сообщаем ему, что его записали. Ещё нет - записавшему его реферальная ссылка, чтобы
    пригласить коллегу: по телефону запись свяжется с его аккаунтом."""
    colleague = result.colleague
    if colleague.telegram_id is None:
        me = await bot.me()  # имя бота - только из API
        link = f"https://t.me/{me.username}?start=ref_{client.referral_code}"
        await message.answer(texts.APPLY_INVITE_COLLEAGUE.format(phone=safe(colleague.phone), link=safe(link)))
        return
    try:
        await bot.send_message(colleague.telegram_id, render.colleague_enrolled(result.card, client.full_name))
    except (TelegramForbiddenError, TelegramBadRequest):
        # Коллега заблокировал бота или не начинал с ним диалог - запись от этого не страдает.
        logger.info("Коллеге #%s не удалось сообщить о записи", colleague.id)


@router.message(StateFilter(Application.price, Application.confirm))
async def application_expects_buttons(message: Message) -> None:
    await message.answer(texts.REG_USE_BUTTONS)


# --- Оплата: физлицо / юрлицо ------------------------------------------------------------------------------------

@router.callback_query(OrderCb.filter(F.action == OrderAction.INDIVIDUAL))
async def pay_as_individual(callback: CallbackQuery, callback_data: OrderCb, state: FSMContext,
                            session: AsyncSession, client: Client, settings: Settings) -> None:
    order = await OrderService(session).choose_payer_type(client.id, callback_data.order_id, PayerType.INDIVIDUAL)
    if order is None:
        await callback.answer(texts.ORDER_UNAVAILABLE, show_alert=True)  # чужой или отменённый заказ
        return
    await callback.answer()
    await state.clear()
    await show_screen(callback.message, render.pay_individual(order, client.email, settings.timezone))


@router.callback_query(OrderCb.filter(F.action == OrderAction.ORGANIZATION))
async def pay_as_organization(callback: CallbackQuery, callback_data: OrderCb, state: FSMContext,
                              session: AsyncSession, client: Client) -> None:
    order = await OrderService(session).choose_payer_type(client.id, callback_data.order_id, PayerType.ORGANIZATION)
    if order is None:
        await callback.answer(texts.ORDER_UNAVAILABLE, show_alert=True)
        return
    await callback.answer()
    await state.clear()
    await state.set_state(Application.requisites)
    await state.update_data({ORDER_KEY: order.id})
    await show_screen(callback.message, texts.PAY_ORGANIZATION.format(number=order.number))


@router.message(Application.requisites, F.document | F.photo)
async def on_requisites(message: Message, state: FSMContext, session: AsyncSession, client: Client) -> None:
    """Файл с реквизитами организации - любой документ (или фото карточки предприятия)."""
    data = await state.get_data()
    if message.document is not None:
        file_id, file_name = message.document.file_id, message.document.file_name
    else:
        file_id, file_name = message.photo[-1].file_id, None
    order = await OrderService(session).save_requisites(client.id, data.get(ORDER_KEY, 0), file_id, file_name)
    await state.clear()
    if order is None:
        await message.answer(texts.ORDER_UNAVAILABLE)
        return
    await message.answer(texts.REQUISITES_RECEIVED.format(number=order.number))


@router.message(Application.requisites)
async def requisites_expect_file(message: Message) -> None:
    await message.answer(texts.REQUISITES_FILE_ONLY)


# --- Кнопки под карточкой расписания ----------------------------------------------------------------------

@router.callback_query(ScheduleCb.filter(F.action == ScheduleAction.NOTIFY))
async def on_notify(callback: CallbackQuery, callback_data: ScheduleCb, session: AsyncSession, client: Client) -> None:
    if await ScheduleService(session).program(callback_data.item_id) is None:
        await callback.answer(texts.UNKNOWN_CALLBACK)
        return
    created = await EnrollmentService(session).subscribe_interest(client.id, callback_data.item_id)
    # Карусель остаётся на месте - ответ всплывающим окном.
    await callback.answer(texts.INTEREST_CREATED if created else texts.INTEREST_EXISTS, show_alert=True)


@router.callback_query(ScheduleCb.filter(F.action == ScheduleAction.APPLY))
async def on_apply(callback: CallbackQuery, callback_data: ScheduleCb, state: FSMContext, session: AsyncSession,
                   client: Client, settings: Settings) -> None:
    await callback.answer()
    message = callback.message
    await state.clear()

    has_consent = await ConsentService(session).has_current_consent(client.id, ConsentType.PERSONAL_DATA)
    step = await RegistrationService(session).next_step(client)
    if has_consent and step is RegistrationStep.DONE:
        await catalog_flow.start_application(message, state, session, client, local_today(settings.timezone),
                                             callback_data.item_id, callback_data.index)
        return

    # Сначала профиль (там reply-клавиатуры и ввод текста - новыми сообщениями), поток запоминаем:
    # после регистрации flows вернёт к записи.
    await delete_quietly(message)
    await state.update_data({PENDING_APPLY_KEY: callback_data.item_id, catalog_flow.CAROUSEL_INDEX_KEY: callback_data.index})
    await message.answer(texts.APPLY_NEEDS_PROFILE)
    if not has_consent:
        await flows.show_consent_form(message, state, session, client)
    else:
        await flows.show_registration_step(message, state, session, client, step)


# --- Мои записи ------------------------------------------------------------------------------------------------
# Карточки листаются стрелками. Плательщику - заказ, оплата, «Как будете оплачивать?»; отменить запись сам он
# может до даты за 2 недели до начала, позже (и коллеге из пары) - «Написать менеджеру».

CANCEL_CHOICES = {
    MyApplicationAction.TRANSFER: CancelChoice.TRANSFER,
    MyApplicationAction.ADVANCE: CancelChoice.ADVANCE,
    MyApplicationAction.REFUND: CancelChoice.REFUND,
}


@router.callback_query(CabinetCb.filter(F.action == CabinetAction.APPLICATIONS))
async def open_applications(callback: CallbackQuery, session: AsyncSession, client: Client,
                            settings: Settings) -> None:
    await callback.answer()
    await _show_application(callback.message, session, client, settings, index=0)


@router.callback_query(MyApplicationCb.filter(F.action.in_({MyApplicationAction.VIEW, MyApplicationAction.KEEP})))
async def view_application(callback: CallbackQuery, callback_data: MyApplicationCb, session: AsyncSession,
                           client: Client, settings: Settings) -> None:
    """Стрелки ◀️ ▶️, «Нет, оставить» и «К моим записям» - показать карточку записи номер index."""
    await callback.answer()
    await _show_application(callback.message, session, client, settings, callback_data.index)


@router.callback_query(MyApplicationCb.filter(F.action == MyApplicationAction.PAY))
async def choose_how_to_pay(callback: CallbackQuery, callback_data: MyApplicationCb, session: AsyncSession,
                            client: Client, settings: Settings) -> None:
    """«Как будете оплачивать?» под записью: тот же выбор «Физлицо / Юрлицо», что после записи."""
    app = await _find_application(session, client, settings, callback_data.enrollment_id)
    order = app.order if app is not None and app.is_payer else None
    if order is None or order.status not in PAYABLE_ORDER_STATUSES:
        await callback.answer(texts.ORDER_UNAVAILABLE, show_alert=True)
        return
    await callback.answer()
    await show_screen(callback.message, render.order_pay_choice(order, settings.timezone),
                      keyboards.payer_choice(order.id, back_index=callback_data.index))


@router.callback_query(MyApplicationCb.filter(F.action == MyApplicationAction.ASK_CANCEL))
async def ask_cancel_application(callback: CallbackQuery, callback_data: MyApplicationCb, session: AsyncSession,
                                 client: Client, settings: Settings) -> None:
    app = await _find_application(session, client, settings, callback_data.enrollment_id)
    if app is None or not app.self_cancel:
        await callback.answer(texts.APPLICATION_CANNOT_CANCEL, show_alert=True)
        return
    await callback.answer()
    if app.paid:
        # Оплата была: выбор, что сделать с деньгами, - он же подтверждение отмены.
        await show_screen(callback.message, render.cancel_paid_question(app, app.paid),
                          keyboards.cancel_paid_choice(app.enrollment.id, callback_data.index))
        return
    await show_screen(callback.message, render.cancel_question(app),
                      keyboards.confirm_cancel_application(app.enrollment.id, callback_data.index))


@router.callback_query(MyApplicationCb.filter(F.action.in_({MyApplicationAction.CANCEL, *CANCEL_CHOICES})))
async def cancel_application(callback: CallbackQuery, callback_data: MyApplicationCb, session: AsyncSession,
                             client: Client, settings: Settings, bot: Bot) -> None:
    # Карточку берём до отмены: отменённая пропадёт из списка, а название и даты нужны для ответа и коллеге.
    app = await _find_application(session, client, settings, callback_data.enrollment_id)
    if app is None:
        await callback.answer(texts.APPLICATION_CANNOT_CANCEL, show_alert=True)
        return
    # Своя ли запись, можно ли её отменить и была ли оплата - решает сервис под блокировкой заказа
    # (enrollment_id из кнопки может быть подделан, а оплата - прийти, пока человек думал).
    result = await EnrollmentService(session).cancel(client.id, app.enrollment.id, local_today(settings.timezone),
                                                     CANCEL_CHOICES.get(callback_data.action))
    if result.outcome is CancelOutcome.NOT_ALLOWED:
        await callback.answer(texts.APPLICATION_CANNOT_CANCEL, show_alert=True)
        return
    await callback.answer()
    if result.outcome is CancelOutcome.NEEDS_CHOICE:
        # Пока был открыт вопрос «Да / Нет», пришла оплата - сначала решить, что сделать с деньгами.
        await show_screen(callback.message, render.cancel_paid_question(app, result.paid),
                          keyboards.cancel_paid_choice(app.enrollment.id, callback_data.index))
        return
    refund_url = None
    if result.choice is CancelChoice.REFUND and settings.manager_url:
        refund_url = with_draft_text(settings.manager_url, render.manager_refund_draft(result.order))
    await show_screen(callback.message, render.cancelled(result, has_manager=bool(settings.manager_url)),
                      keyboards.cancelled_actions(callback_data.index, schedule=result.choice is CancelChoice.TRANSFER,
                                                  manager_url=refund_url))
    await _tell_colleagues_cancelled(bot, result, app, client)


async def _tell_colleagues_cancelled(bot: Bot, result: CancelResult, app: MyApplication, client: Client) -> None:
    """Коллегам из пары, которые уже в боте: записавший отменил запись."""
    for colleague in result.colleagues:
        if colleague.telegram_id is None:
            continue
        try:
            await bot.send_message(colleague.telegram_id, render.colleague_cancelled(app, client.full_name))
        except (TelegramForbiddenError, TelegramBadRequest):
            logger.info("Коллеге #%s не удалось сообщить об отмене записи", colleague.id)


async def _find_application(session: AsyncSession, client: Client, settings: Settings,
                            enrollment_id: int) -> MyApplication | None:
    """Своя запись из «Моих записей» (id из кнопки может быть подделан - ищем только среди своих)."""
    apps = await EnrollmentService(session).my_applications(client.id, local_today(settings.timezone))
    return next((app for app in apps if app.enrollment.id == enrollment_id), None)


async def _show_application(message: Message, session: AsyncSession, client: Client, settings: Settings,
                            index: int) -> None:
    apps = await EnrollmentService(session).my_applications(client.id, local_today(settings.timezone))
    if not apps:
        await show_screen(message, texts.MY_APPLICATIONS_EMPTY, keyboards.back_to_cabinet())
        return
    index = min(max(index, 0), len(apps) - 1)
    app = apps[index]
    manager_url = (with_draft_text(settings.manager_url, render.manager_cancel_draft(app))
                   if settings.manager_url else None)
    await show_screen(message, render.my_application_card(app, settings.timezone, datetime.now(UTC)),
                      keyboards.my_application_card(app, index, len(apps), manager_url))
