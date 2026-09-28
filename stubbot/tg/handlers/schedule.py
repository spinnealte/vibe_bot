"""Расписание: список потоков, карточки, каталог курсов, «Сообщить о наборе», вход в заявку."""

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
from stubbot.tg import catalog_flow, flows, render, texts
from stubbot.tg.callbacks import ConsentKind, ScheduleAction, ScheduleCb
from stubbot.tg.states import PENDING_APPLY_KEY
from stubbot.utils.dates import local_today

router = Router(name="schedule")


@router.callback_query(ScheduleCb.filter(F.action == ScheduleAction.PAGE))
async def on_page(callback: CallbackQuery, callback_data: ScheduleCb, session: AsyncSession,
                  settings: Settings) -> None:
    await callback.answer()
    await catalog_flow.show_schedule(callback.message, session, local_today(settings.timezone),
                                     callback_data.page, edit=True)


@router.callback_query(ScheduleCb.filter(F.action == ScheduleAction.SESSION))
async def on_session(callback: CallbackQuery, callback_data: ScheduleCb, session: AsyncSession, client: Client,
                     settings: Settings) -> None:
    await callback.answer()
    shown = await catalog_flow.show_session(callback.message, session, local_today(settings.timezone),
                                            callback_data.item_id, callback_data.page, settings.timezone)
    if shown:
        AnalyticsRepository(session).add_event(client.id, ClientEventType.SESSION_VIEWED,
                                               {"session_id": callback_data.item_id})


@router.callback_query(ScheduleCb.filter(F.action == ScheduleAction.PROGRAMS))
async def on_programs(callback: CallbackQuery, session: AsyncSession) -> None:
    await callback.answer()
    await catalog_flow.show_programs(callback.message, session, edit=True)


@router.callback_query(ScheduleCb.filter(F.action == ScheduleAction.PROGRAM))
async def on_program(callback: CallbackQuery, callback_data: ScheduleCb, session: AsyncSession,
                     settings: Settings) -> None:
    await callback.answer()
    if not await catalog_flow.show_program(callback.message, session, local_today(settings.timezone),
                                           callback_data.item_id, edit=True):
        await catalog_flow.show_programs(callback.message, session)


@router.callback_query(ScheduleCb.filter(F.action == ScheduleAction.PROGRAM_TEXT))
async def on_program_text(callback: CallbackQuery, callback_data: ScheduleCb, session: AsyncSession,
                          client: Client) -> None:
    await callback.answer()
    program = await ScheduleService(session).program(callback_data.item_id)
    if program is None or not program.program_html:
        await callback.message.answer(texts.PROGRAM_TEXT_EMPTY)
        return
    AnalyticsRepository(session).add_event(client.id, ClientEventType.PROGRAM_OPENED, {"program_id": program.id})
    # program_html заполняет админ в HTML-разметке Telegram — отправляем как есть, частями по лимиту.
    for chunk in render.split_html(f"📖 <b>{render.short(program.title, 200)}</b>\n\n{program.program_html}"):
        await callback.message.answer(chunk)


@router.callback_query(ScheduleCb.filter(F.action == ScheduleAction.NOTIFY))
async def on_notify(callback: CallbackQuery, callback_data: ScheduleCb, session: AsyncSession, client: Client) -> None:
    if await ScheduleService(session).program(callback_data.item_id) is None:
        await callback.answer(texts.UNKNOWN_CALLBACK)
        return
    created = await EnrollmentService(session).subscribe_interest(client.id, callback_data.item_id)
    await callback.answer()
    await callback.message.answer(texts.INTEREST_CREATED if created else texts.INTEREST_EXISTS)


@router.callback_query(ScheduleCb.filter(F.action == ScheduleAction.APPLY))
async def on_apply(callback: CallbackQuery, callback_data: ScheduleCb, state: FSMContext, session: AsyncSession,
                   client: Client, settings: Settings) -> None:
    await callback.answer()
    message = callback.message
    session_id = callback_data.item_id
    await state.clear()

    has_consent = await ConsentService(session).has_current_consent(client.id, ConsentType.PERSONAL_DATA)
    step = await RegistrationService(session).next_step(client)
    if has_consent and step is RegistrationStep.DONE:
        await catalog_flow.start_application(message, state, session, client, local_today(settings.timezone),
                                             session_id)
        return

    # Сначала профиль; поток запоминаем — после регистрации flows вернёт к заявке.
    await state.update_data({PENDING_APPLY_KEY: session_id})
    await message.answer(texts.APPLY_NEEDS_PROFILE)
    if not has_consent:
        await flows.show_consent(message, state, session, settings, ConsentKind.PD)
    else:
        await flows.show_registration_step(message, state, session, client, step)
