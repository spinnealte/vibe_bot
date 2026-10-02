"""Кнопки главного меню. Срабатывают в любом состоянии и выводят из незаконченного сценария."""

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from stubbot.config import Settings
from stubbot.db.enums import ClientEventType
from stubbot.db.models import Client
from stubbot.repositories.analytics import AnalyticsRepository
from stubbot.services.schedule import ScheduleService
from stubbot.tg import catalog_flow, flows, keyboards, render, texts
from stubbot.utils.dates import local_today
from stubbot.utils.links import with_draft_text

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
async def open_about(message: Message, state: FSMContext, session: AsyncSession, settings: Settings) -> None:
    await state.clear()
    venues = await ScheduleService(session).venues()
    manager_url = settings.manager_url
    text = render.about_text(venues, with_manager=manager_url is not None)
    # Есть менеджер — под текстом его кнопка; иначе возвращаем клавиатуру меню (могли прийти из сценария).
    markup = (keyboards.contact_manager(with_draft_text(manager_url, texts.MANAGER_DRAFT_ABOUT))
              if manager_url else keyboards.main_menu(message.chat.id))
    await message.answer(text, reply_markup=markup)
