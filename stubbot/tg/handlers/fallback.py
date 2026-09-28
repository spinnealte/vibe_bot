"""Последний роутер: то, что не подошло ни одному хендлеру. Подключается строго последним."""

from aiogram import Router
from aiogram.types import CallbackQuery, Message

from stubbot.tg import keyboards, texts

router = Router(name="fallback")


@router.callback_query()
async def unknown_callback(callback: CallbackQuery) -> None:
    # Без answer() у пользователя бесконечно крутятся «часики» на кнопке.
    await callback.answer(texts.UNKNOWN_CALLBACK)


@router.message()
async def unknown_message(message: Message) -> None:
    await message.answer(texts.UNKNOWN_MESSAGE, reply_markup=keyboards.main_menu())
