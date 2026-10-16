"""Последний роутер: то, что не подошло ни одному хендлеру. Подключается строго последним."""

from aiogram import Router
from aiogram.types import CallbackQuery, Message

from stubbot.config import Settings
from stubbot.tg import keyboards, render, texts
from stubbot.utils.links import with_draft_text

router = Router(name="fallback")


@router.callback_query()
async def unknown_callback(callback: CallbackQuery) -> None:
    # Без answer() у пользователя бесконечно крутятся «часики» на кнопке.
    await callback.answer(texts.UNKNOWN_CALLBACK)


@router.message()
async def unknown_message(message: Message, settings: Settings) -> None:
    await message.answer(texts.UNKNOWN_MESSAGE, reply_markup=keyboards.main_menu(message.chat.id))
    # Человек написал боту текстом - скорее всего, это вопрос, а на вопросы бот не отвечает. Предлагаем
    # менеджера отдельным сообщением: в первом - клавиатура меню, а inline-кнопку вместе с ней не прислать.
    # На стикеры, фото и команды не предлагаем - это не вопросы.
    if settings.manager_url and message.text and not message.text.startswith("/"):
        url = with_draft_text(settings.manager_url, render.question_draft(message.text))
        await message.answer(texts.UNKNOWN_MESSAGE_MANAGER, reply_markup=keyboards.contact_manager(url))
