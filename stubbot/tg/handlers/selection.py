"""Общий мультивыбор (специальности, должности): переключение галочек. Выбор хранится в FSM data["selected"].

Что делать по «Готово», решает сценарий (регистрация или кабинет) — там свои хендлеры SelectDoneCb
с тем же фильтром group_matches_state.
"""

from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from stubbot.services.registration import RegistrationService
from stubbot.tg import keyboards, texts
from stubbot.tg.callbacks import SelectDoneCb, SelectGroup, ToggleCb
from stubbot.tg.states import SELECTION_GROUP_BY_STATE

router = Router(name="selection")


def group_matches_state(_: CallbackQuery, callback_data: ToggleCb | SelectDoneCb, raw_state: str | None) -> bool:
    """Кнопка из клавиатуры текущего шага. Нажатие на старую клавиатуру (специальности, когда человек уже
    выбирает должности) не подходит ни одному хендлеру и уходит в fallback: «Эта кнопка уже неактуальна»."""
    return SELECTION_GROUP_BY_STATE.get(raw_state) is callback_data.group


@router.callback_query(ToggleCb.filter(), group_matches_state)
async def on_toggle(callback: CallbackQuery, callback_data: ToggleCb, state: FSMContext, session: AsyncSession) -> None:
    options = await selection_options(session, callback_data.group)
    if callback_data.item_id not in {item_id for item_id, _ in options}:
        # Записи нет в справочнике: её убрали в архив после показа клавиатуры или кнопка подделана.
        await callback.answer(texts.UNKNOWN_CALLBACK)
        return
    selected = set((await state.get_data()).get("selected", []))
    selected ^= {callback_data.item_id}
    await state.update_data(selected=sorted(selected))
    await callback.message.edit_reply_markup(
        reply_markup=keyboards.multiselect(callback_data.group, options, selected)
    )
    await callback.answer()


async def selection_options(session: AsyncSession, group: SelectGroup) -> list[tuple[int, str]]:
    service = RegistrationService(session)
    items = await (service.specialties() if group is SelectGroup.SPECIALTIES else service.positions())
    return [(item.id, item.title) for item in items]
