"""Всё - в приложении (WEBAPP_ONLY=true): вместо кнопочных разделов бота - этот роутер, последним после /start.
Бот здесь - вход в приложение и уведомления. Код разделов не удалён: с WEBAPP_ONLY=false всё как было."""

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove

from stubbot.config import Settings
from stubbot.tg import keyboards, render, texts
from stubbot.utils.links import with_draft_text

router = Router(name="app_only")

# Кнопки старых reply-клавиатур (главное меню, шаги регистрации): нажатие убирает клавиатуру.
OLD_KEYBOARD = {texts.BTN_SCHEDULE, texts.BTN_CABINET, texts.BTN_ABOUT, texts.BTN_MANAGER, texts.BTN_BACK,
                texts.BTN_CANCEL}


@router.callback_query()
async def old_button(callback: CallbackQuery) -> None:
    """Кнопка из сообщения, присланного до перехода на приложение."""
    await callback.answer(texts.APP_OLD_BUTTON, show_alert=True)


@router.message(F.contact)
async def contact_from_app(message: Message) -> None:
    """Номер, которым поделились в приложении, приходит в чат контактом - его сохраняет само приложение."""


@router.message(F.text == texts.BTN_ADMIN)
async def old_admin_button(message: Message, settings: Settings) -> None:
    text = texts.APP_ADMIN_MOVED if settings.is_admin(message.chat.id) else texts.APP_MENU_MOVED
    await message.answer(text, reply_markup=ReplyKeyboardRemove())


@router.message(F.text.in_(OLD_KEYBOARD))
async def old_menu_button(message: Message) -> None:
    # Одним сообщением нельзя и убрать клавиатуру, и дать inline-кнопку: путь в приложение - кнопка меню чата.
    await message.answer(texts.APP_MENU_MOVED, reply_markup=ReplyKeyboardRemove())


@router.message()
async def anything_else(message: Message, settings: Settings) -> None:
    # Текст (не команда) - скорее всего вопрос: рядом с приложением - менеджер с этим вопросом в черновике.
    question = bool(settings.manager_url and message.text and not message.text.startswith("/"))
    manager_url = with_draft_text(settings.manager_url, render.question_draft(message.text)) if question else None
    await message.answer(texts.APP_HINT + (texts.APP_HINT_MANAGER if question else ""),
                         reply_markup=keyboards.app_hint(settings.webapp_url, manager_url))
