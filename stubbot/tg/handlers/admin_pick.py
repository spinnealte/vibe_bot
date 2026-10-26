"""Пронумерованные списки админки (tg/picker.py): ◀️ ▶️ по страницам и номер строки сообщением.

Только для админов: IsAdmin на весь роутер. Подключается после admin.router - «⚙️ Админка» посреди выбора
открывает меню, а не считается номером. Что открыть по номеру - решает открывалка вида списка (picker.OPENERS).
"""

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from stubbot.config import Settings
from stubbot.db.models import Client
from stubbot.tg import picker, texts
from stubbot.tg.callbacks import PickCb
from stubbot.tg.filters import IsAdmin
from stubbot.tg.screen import show_screen
from stubbot.tg.states import AdminPick

router = Router(name="admin_pick")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


@router.callback_query(PickCb.filter())
async def on_page(callback: CallbackQuery, callback_data: PickCb, state: FSMContext) -> None:
    pick = await picker.current(state)
    if pick is None or pick.token != callback_data.token:
        await callback.answer(texts.PICK_EXPIRED, show_alert=True)  # этот список уже не ждёт номер
        return
    await callback.answer()
    if callback_data.page < 0:
        return  # счётчик «2/5»
    await show_screen(callback.message, *picker.render(pick, callback_data.page))


@router.message(AdminPick.number, F.text)
async def on_number(message: Message, state: FSMContext, session: AsyncSession, client: Client,
                    settings: Settings) -> None:
    pick = await picker.current(state)
    if pick is None:
        await state.clear()
        await message.answer(texts.PICK_EXPIRED)
        return
    number = picker.parse_number(message.text, len(pick.ids))
    if number is None:
        if pick.context.get("requery") and pick.kind in picker.REQUERIES:
            # Поиск: написан не номер из списка - это новый запрос.
            await picker.REQUERIES[pick.kind](message, state, session, client, settings, message.text, pick.context)
            return
        await message.answer(texts.PICK_INVALID.format(count=len(pick.ids)))
        return
    # page - на какой странице стоит выбранная строка: «Назад» из карточки вернёт туда.
    context = {**pick.context, "page": (number - 1) // picker.PAGE_SIZE}
    await picker.OPENERS[pick.kind](message, state, session, client, settings, pick.ids[number - 1], context)


@router.message(AdminPick.number)
async def number_expects_text(message: Message, state: FSMContext) -> None:
    pick = await picker.current(state)
    await message.answer(texts.PICK_INVALID.format(count=len(pick.ids) if pick else 1))
