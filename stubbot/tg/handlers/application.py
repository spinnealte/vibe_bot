"""Заявки: «Оставить заявку» и «Сообщить о наборе» под карточкой расписания, шаги заявки
(тариф → число мест → комментарий → подтверждение), «Мои заявки» в кабинете. Все проверки — в EnrollmentService.

В MVP-афише выключено (APPLICATIONS_ENABLED=false): роутер не подключается, кнопки не показываются.
Шаги по кнопкам меняют одно и то же сообщение; после комментария текстом подтверждение приходит новым сообщением.
"""

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from stubbot.config import Settings
from stubbot.db.enums import ConsentType
from stubbot.db.models import Client
from stubbot.services.consents import ConsentService
from stubbot.services.enrollments import MAX_COMMENT_LENGTH, ApplyOutcome, EnrollmentService
from stubbot.services.registration import RegistrationService, RegistrationStep
from stubbot.services.schedule import ScheduleService
from stubbot.tg import catalog_flow, flows, keyboards, render, texts
from stubbot.tg.callbacks import (
    ApplyAction,
    ApplyCb,
    ApplyPriceCb,
    ApplySeatsCb,
    CabinetAction,
    CabinetCb,
    ConsentKind,
    MyApplicationAction,
    MyApplicationCb,
    ScheduleAction,
    ScheduleCb,
)
from stubbot.tg.catalog_flow import CAROUSEL_INDEX_KEY
from stubbot.tg.flows import with_notice
from stubbot.tg.screen import delete_quietly, show_screen
from stubbot.tg.states import PENDING_APPLY_KEY, Application
from stubbot.utils.dates import local_today

router = Router(name="application")


@router.callback_query(StateFilter(Application), ApplyCb.filter(F.action == ApplyAction.CANCEL))
async def cancel_application(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    index = (await state.get_data()).get(CAROUSEL_INDEX_KEY, 0)
    await state.clear()
    await show_screen(callback.message, texts.APPLY_CANCELLED, keyboards.to_schedule(index))


@router.callback_query(Application.price, ApplyPriceCb.filter())
async def on_price(callback: CallbackQuery, callback_data: ApplyPriceCb, state: FSMContext, session: AsyncSession,
                   client: Client, settings: Settings) -> None:
    await callback.answer()
    data = await state.get_data()
    card, _ = await EnrollmentService(session).availability(client, data.get(PENDING_APPLY_KEY),
                                                            local_today(settings.timezone))
    price = next((p for p in card.active_prices if p.id == callback_data.price_id), None) if card else None
    if price is None:
        await state.clear()
        await show_screen(callback.message, texts.APPLY_EXPIRED, keyboards.to_schedule(data.get(CAROUSEL_INDEX_KEY, 0)))
        return
    await state.update_data(price_id=price.id)
    await catalog_flow.ask_seats(callback.message, state, price)


@router.callback_query(Application.seats, ApplySeatsCb.filter())
async def on_seats(callback: CallbackQuery, callback_data: ApplySeatsCb, state: FSMContext) -> None:
    await callback.answer()
    await state.update_data(seats=callback_data.seats)  # диапазон ещё раз проверит сервис при отправке
    await catalog_flow.ask_comment(callback.message, state)


@router.message(Application.comment, F.text)
async def on_comment(message: Message, state: FSMContext, session: AsyncSession, client: Client,
                     settings: Settings) -> None:
    if len(message.text) > MAX_COMMENT_LENGTH:
        await message.answer(texts.APPLY_COMMENT_TOO_LONG)
        return
    await state.update_data(comment=message.text.strip())
    await catalog_flow.show_confirm(message, state, session, client, local_today(settings.timezone), in_place=False)


@router.callback_query(Application.comment, ApplyCb.filter(F.action == ApplyAction.NO_COMMENT))
async def on_no_comment(callback: CallbackQuery, state: FSMContext, session: AsyncSession, client: Client,
                        settings: Settings) -> None:
    await callback.answer()
    await state.update_data(comment=None)
    await catalog_flow.show_confirm(callback.message, state, session, client, local_today(settings.timezone))


@router.callback_query(Application.confirm, ApplyCb.filter(F.action == ApplyAction.SEND))
async def on_send(callback: CallbackQuery, state: FSMContext, session: AsyncSession, client: Client,
                  settings: Settings) -> None:
    await callback.answer()
    data = await state.get_data()
    await state.clear()
    back = keyboards.to_schedule(data.get(CAROUSEL_INDEX_KEY, 0))
    if PENDING_APPLY_KEY not in data:
        await show_screen(callback.message, texts.APPLY_EXPIRED, back)
        return
    result = await EnrollmentService(session).apply(
        client,
        session_id=data[PENDING_APPLY_KEY],
        price_option_id=data.get("price_id"),
        seats=data.get("seats", 1),
        comment=data.get("comment"),
        today=local_today(settings.timezone),
    )
    match result.outcome:
        case ApplyOutcome.CREATED:
            text = texts.APPLY_CREATED
        case ApplyOutcome.WAITLIST:
            text = texts.APPLY_WAITLISTED
        case ApplyOutcome.DUPLICATE:
            status = result.enrollment.status.value if result.enrollment else "application"
            text = texts.APPLY_DUPLICATE.format(status=texts.ENROLLMENT_STATUS_LABELS[status])
        case ApplyOutcome.CLOSED:
            text = texts.APPLY_CLOSED
        case _:
            text = texts.APPLY_EXPIRED
    await show_screen(callback.message, text, back)


@router.message(Application.comment)
async def comment_expects_text(message: Message) -> None:
    await message.answer(texts.REG_TEXT_ONLY)


@router.message(StateFilter(Application.price, Application.seats, Application.confirm))
async def application_expects_buttons(message: Message) -> None:
    await message.answer(texts.REG_USE_BUTTONS)


# --- Кнопки под карточкой расписания ----------------------------------------------------------------------

@router.callback_query(ScheduleCb.filter(F.action == ScheduleAction.NOTIFY))
async def on_notify(callback: CallbackQuery, callback_data: ScheduleCb, session: AsyncSession, client: Client) -> None:
    if await ScheduleService(session).program(callback_data.item_id) is None:
        await callback.answer(texts.UNKNOWN_CALLBACK)
        return
    created = await EnrollmentService(session).subscribe_interest(client.id, callback_data.item_id)
    # Карусель остаётся на месте — ответ всплывающим окном.
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

    # Сначала профиль (там reply-клавиатуры и ввод текста — новыми сообщениями), поток запоминаем:
    # после регистрации flows вернёт к заявке.
    await delete_quietly(message)
    await state.update_data({PENDING_APPLY_KEY: callback_data.item_id, catalog_flow.CAROUSEL_INDEX_KEY: callback_data.index})
    await message.answer(texts.APPLY_NEEDS_PROFILE)
    if not has_consent:
        await flows.show_consent(message, state, session, settings, ConsentKind.PD)
    else:
        await flows.show_registration_step(message, state, session, client, step)


# --- Мои заявки ------------------------------------------------------------------------------------------------

@router.callback_query(CabinetCb.filter(F.action == CabinetAction.APPLICATIONS))
async def open_applications(callback: CallbackQuery, session: AsyncSession, client: Client) -> None:
    await callback.answer()
    await _show_application(callback.message, session, client, index=0)


@router.callback_query(MyApplicationCb.filter(F.action.in_({MyApplicationAction.VIEW, MyApplicationAction.KEEP})))
async def view_application(callback: CallbackQuery, callback_data: MyApplicationCb, session: AsyncSession,
                           client: Client) -> None:
    """Стрелки ◀️ ▶️ и «Нет, оставить» — показать карточку заявки номер index."""
    await callback.answer()
    await _show_application(callback.message, session, client, callback_data.index)


@router.callback_query(MyApplicationCb.filter(F.action == MyApplicationAction.ASK_CANCEL))
async def ask_cancel_application(callback: CallbackQuery, callback_data: MyApplicationCb, session: AsyncSession,
                                 client: Client) -> None:
    apps = await EnrollmentService(session).my_applications(client.id)
    app = next((a for a in apps if a.enrollment.id == callback_data.enrollment_id and a.cancellable), None)
    if app is None:
        await callback.answer(texts.APPLICATION_CANNOT_CANCEL, show_alert=True)
        return
    await callback.answer()
    await show_screen(callback.message, render.cancel_application_question(app),
                      keyboards.confirm_cancel_application(app.enrollment.id, callback_data.index))


@router.callback_query(MyApplicationCb.filter(F.action == MyApplicationAction.CANCEL))
async def cancel_application(callback: CallbackQuery, callback_data: MyApplicationCb, session: AsyncSession,
                             client: Client) -> None:
    # Своя ли заявка и можно ли её отменить — проверяет сервис (enrollment_id из кнопки может быть подделан).
    if await EnrollmentService(session).cancel(client.id, callback_data.enrollment_id) is None:
        await callback.answer(texts.APPLICATION_CANNOT_CANCEL, show_alert=True)
        return
    await callback.answer()
    # Отменённая пропадает из списка — на её месте окажется следующая (или предыдущая, если была последней).
    await _show_application(callback.message, session, client, callback_data.index,
                            notice=texts.APPLICATION_CANCELLED)


async def _show_application(message: Message, session: AsyncSession, client: Client, index: int,
                            notice: str | None = None) -> None:
    apps = await EnrollmentService(session).my_applications(client.id)
    if not apps:
        await show_screen(message, with_notice(notice, texts.MY_APPLICATIONS_EMPTY), keyboards.back_to_cabinet())
        return
    index = min(max(index, 0), len(apps) - 1)
    app = apps[index]
    await show_screen(message, with_notice(notice, render.my_application_card(app)),
                      keyboards.my_application_card(app.enrollment.id, index, len(apps), app.cancellable))
