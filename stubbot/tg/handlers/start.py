from aiogram import Router
from aiogram.filters import CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from stubbot.config import Settings
from stubbot.db.models import Client
from stubbot.services.clients import ClientService
from stubbot.services.deeplinks import parse_start_payload
from stubbot.tg import keyboards, texts

router = Router(name="start")


# Без StateFilter - срабатывает в любом состоянии: /start всегда выводит из незаконченного сценария.
@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject, state: FSMContext, session: AsyncSession,
                    client: Client, client_created: bool, settings: Settings) -> None:
    await state.clear()
    await ClientService(session).process_start(client, client_created, parse_start_payload(command.args))
    if settings.webapp_only:
        # Всё - в приложении: одно приветствие и одна кнопка. Нижнего меню нет - и не присылаем.
        await message.answer(texts.WELCOME_APP_NEW if client_created else texts.WELCOME_APP_BACK,
                             reply_markup=keyboards.open_webapp(settings.webapp_url))
        return
    await message.answer(texts.WELCOME_NEW if client_created else texts.WELCOME_BACK,
                         reply_markup=keyboards.main_menu(message.chat.id))
    if settings.webapp_url:
        # Нижнее меню - reply-клавиатура, inline-кнопку к тому же сообщению не прикрепить: вторым сообщением.
        await message.answer(texts.WEBAPP_HINT, reply_markup=keyboards.open_webapp(settings.webapp_url))
