"""Кнопки главного меню. Срабатывают в любом состоянии и выводят из незаконченного сценария."""

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from stubbot.config import Settings
from stubbot.db.enums import ClientEventType
from stubbot.db.models import Client
from stubbot.repositories.analytics import AnalyticsRepository
from stubbot.tg import catalog_flow, flows, keyboards, texts
from stubbot.utils.dates import local_today

router = Router(name="menu")


@router.message(F.text == texts.BTN_SCHEDULE)
async def open_schedule(message: Message, state: FSMContext, session: AsyncSession, client: Client,
                        settings: Settings) -> None:
    await state.clear()
    AnalyticsRepository(session).add_event(client.id, ClientEventType.SCHEDULE_OPENED)
    await catalog_flow.show_course(message, session, local_today(settings.timezone), 0, settings.timezone,
                                   in_place=False)


@router.message(F.text == texts.BTN_CABINET)
async def open_cabinet(message: Message, state: FSMContext, session: AsyncSession, client: Client,
                       settings: Settings, bot: Bot) -> None:
    await flows.enter_cabinet(message, state, session, client, settings, bot)


@router.message(F.text == texts.BTN_ABOUT)
async def open_about(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(texts.ABOUT, reply_markup=keyboards.main_menu())
