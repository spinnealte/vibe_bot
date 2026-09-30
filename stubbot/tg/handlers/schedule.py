"""Расписание-афиша: карусель курсов с фото. Все кнопки меняют то же сообщение (tg/screen.py).
Кнопки заявки и «Сообщить о наборе» — в handlers/application.py."""

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from stubbot.config import Settings
from stubbot.db.enums import ClientEventType
from stubbot.db.models import Client
from stubbot.repositories.analytics import AnalyticsRepository
from stubbot.tg import catalog_flow, keyboards, texts
from stubbot.tg.callbacks import ScheduleAction, ScheduleCb
from stubbot.tg.screen import delete_quietly
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


@router.callback_query(ScheduleCb.filter(F.action == ScheduleAction.CLOSE))
async def on_close(callback: CallbackQuery, state: FSMContext) -> None:
    """«⬅️ Назад» под карточкой: убираем карусель, возвращаем главное меню."""
    await callback.answer()
    await state.clear()
    await delete_quietly(callback.message)
    await callback.message.answer(texts.SCHEDULE_CLOSED, reply_markup=keyboards.main_menu())


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
