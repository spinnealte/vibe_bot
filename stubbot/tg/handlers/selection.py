"""Общий мультивыбор (специальности, должности): переключение галочек. Выбор хранится в FSM data["selected"].

Что делать по «Готово», решает сценарий (регистрация или кабинет) — там свои хендлеры SelectDoneCb.
"""

from aiogram import Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from stubbot.services.registration import RegistrationService
from stubbot.tg import keyboards
from stubbot.tg.callbacks import SelectGroup, ToggleCb
from stubbot.tg.states import SELECTION_STATES

router = Router(name="selection")


@router.callback_query(StateFilter(*SELECTION_STATES), ToggleCb.filter())
async def on_toggle(callback: CallbackQuery, callback_data: ToggleCb, state: FSMContext, session: AsyncSession) -> None:
    selected = set((await state.get_data()).get("selected", []))
    selected ^= {callback_data.item_id}
    await state.update_data(selected=sorted(selected))
    options = await selection_options(session, callback_data.group)
    await callback.message.edit_reply_markup(
        reply_markup=keyboards.multiselect(callback_data.group, options, selected)
    )
    await callback.answer()


async def selection_options(session: AsyncSession, group: SelectGroup) -> list[tuple[int, str]]:
    service = RegistrationService(session)
    items = await (service.specialties() if group is SelectGroup.SPECIALTIES else service.positions())
    return [(item.id, item.title) for item in items]
