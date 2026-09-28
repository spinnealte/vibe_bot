"""Расписание: карусель курсов с фото. Все кнопки меняют то же сообщение (tg/screen.py)."""

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from stubbot.config import Settings
from stubbot.db.enums import ClientEventType, ConsentType
from stubbot.db.models import Client
from stubbot.repositories.analytics import AnalyticsRepository
from stubbot.services.consents import ConsentService
from stubbot.services.enrollments import EnrollmentService
from stubbot.services.registration import RegistrationService, RegistrationStep
from stubbot.services.schedule import ScheduleService
from stubbot.tg import catalog_flow, flows, texts
from stubbot.tg.callbacks import ConsentKind, ScheduleAction, ScheduleCb
from stubbot.tg.screen import delete_quietly
from stubbot.tg.states import PENDING_APPLY_KEY
from stubbot.utils.dates import local_today

router = Router(name="schedule")


@router.callback_query(ScheduleCb.filter(F.action == ScheduleAction.NOOP))
async def on_counter(callback: CallbackQuery) -> None:
    await callback.answer()


@router.callback_query(ScheduleCb.filter(F.action == ScheduleAction.SLIDE))
async def on_slide(callback: CallbackQuery, callback_data: ScheduleCb, state: FSMContext, session: AsyncSession,
                   settings: Settings) -> None:
    await callback.answer()
    await state.clear()  # «К расписанию» из заявки — выходим из её шагов
    await catalog_flow.show_course(callback.message, session, local_today(settings.timezone), callback_data.index,
                                   settings.timezone)


@router.callback_query(ScheduleCb.filter(F.action == ScheduleAction.DETAILS))
async def on_details(callback: CallbackQuery, callback_data: ScheduleCb, session: AsyncSession, client: Client,
                     settings: Settings) -> None:
    today = local_today(settings.timezone)
    if not await catalog_flow.show_details(callback.message, session, today, callback_data.item_id,
                                           callback_data.index, settings.timezone):
        await callback.answer(texts.SESSION_GONE)
        await catalog_flow.show_course(callback.message, session, today, callback_data.index, settings.timezone)
        return
    await callback.answer()
    AnalyticsRepository(session).add_event(client.id, ClientEventType.SESSION_VIEWED,
                                           {"session_id": callback_data.item_id})


@router.callback_query(ScheduleCb.filter(F.action == ScheduleAction.PROGRAM))
async def on_program(callback: CallbackQuery, callback_data: ScheduleCb, session: AsyncSession, client: Client) -> None:
    if not await catalog_flow.show_program_page(callback.message, session, callback_data.item_id,
                                                callback_data.index, callback_data.page):
        await callback.answer(texts.PROGRAM_TEXT_EMPTY)
        return
    await callback.answer()
    if callback_data.page == 0:
        AnalyticsRepository(session).add_event(client.id, ClientEventType.PROGRAM_OPENED,
                                               {"program_id": callback_data.item_id})


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
