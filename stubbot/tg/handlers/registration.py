"""Регистрация.

Обязательно: телефон → ФИО одним сообщением → подтверждение ФИО → специальности → должности.
Необязательно (после регистрации, каждое можно пропустить): город → место работы → опыт → email → дата рождения.

Порядок хендлеров важен: сначала «Назад»/«Отмена», затем ожидаемые ответы, в конце — подсказки на
неподходящий ввод (фото, стикеры, голос, текст вместо кнопки).
"""

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from stubbot.config import Settings
from stubbot.db.models import Client
from stubbot.services.registration import (
    OPTIONAL_FIELDS_ORDER,
    OptionalField,
    PhoneResult,
    RegistrationService,
    RegistrationStep,
)
from stubbot.tg import flows, keyboards, texts
from stubbot.tg.callbacks import NameAction, NameCb, SelectDoneCb, SelectGroup, SkipOptionalCb
from stubbot.tg.handlers.selection import group_matches_state
from stubbot.tg.screen import delete_quietly, strip_keyboard
from stubbot.tg.states import (
    FIELD_BY_STATE,
    FULL_NAME_KEY,
    OPTIONAL_STATES,
    REGISTRATION_BUTTON_STATES,
    REGISTRATION_TEXT_STATES,
    Registration,
)
from stubbot.utils.dates import local_today
from stubbot.utils.names import parse_full_name

router = Router(name="registration")


# --- Навигация -------------------------------------------------------------------------------------------------

@router.message(StateFilter(Registration), F.text == texts.BTN_CANCEL)
async def cancel_registration(message: Message, state: FSMContext) -> None:
    in_optional = await state.get_state() in FIELD_BY_STATE
    await state.clear()
    text = texts.REG_OPTIONAL_CANCELLED if in_optional else texts.REG_CANCELLED
    await message.answer(text, reply_markup=keyboards.main_menu())


@router.message(StateFilter(Registration), F.text == texts.BTN_BACK)
async def step_back(message: Message, state: FSMContext, session: AsyncSession, client: Client) -> None:
    current = await state.get_state()
    if current in FIELD_BY_STATE:
        index = OPTIONAL_FIELDS_ORDER.index(FIELD_BY_STATE[current])
        if index == 0:
            # Обязательная часть уже сохранена; назад из первого необязательного — к должностям.
            await flows.show_registration_step(message, state, session, client, RegistrationStep.POSITIONS)
        else:
            await flows.ask_optional(message, state, OPTIONAL_FIELDS_ORDER[index - 1])
        return
    match current:
        case Registration.full_name:
            await flows.show_registration_step(message, state, session, client, RegistrationStep.PHONE)
        case Registration.confirm_name | Registration.specialties:
            await flows.ask_full_name(message, state)
        case Registration.positions:
            await flows.show_registration_step(message, state, session, client, RegistrationStep.SPECIALTIES)
        case _:
            await cancel_registration(message, state)


# --- Телефон ---------------------------------------------------------------------------------------------------

@router.message(Registration.phone, F.contact)
async def on_contact(message: Message, state: FSMContext, session: AsyncSession, client: Client) -> None:
    contact = message.contact
    if contact.user_id != message.from_user.id:
        await message.answer(texts.REG_PHONE_FOREIGN_CONTACT)
        return
    service = RegistrationService(session)
    match await service.save_phone(client, contact.phone_number):
        case PhoneResult.INVALID:
            await message.answer(texts.REG_PHONE_INVALID)
        case PhoneResult.TAKEN:
            await state.clear()
            await message.answer(texts.REG_PHONE_TAKEN, reply_markup=keyboards.main_menu())
        case PhoneResult.OK:
            await flows.show_registration_step(message, state, session, client, await service.next_step(client))


@router.message(Registration.phone)
async def phone_expects_button(message: Message) -> None:
    await message.answer(texts.REG_PHONE_USE_BUTTON, reply_markup=keyboards.share_phone())


# --- ФИО -------------------------------------------------------------------------------------------------------

@router.message(Registration.full_name, F.text)
async def on_full_name(message: Message, state: FSMContext) -> None:
    name = parse_full_name(message.text)
    if name is None:
        await message.answer(texts.REG_NAME_INVALID)
        return
    await state.update_data({FULL_NAME_KEY: name})
    await flows.ask_confirm_name(message, state)


@router.callback_query(Registration.confirm_name, NameCb.filter(F.action == NameAction.FIX))
async def on_fix_name(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    # Вопрос про ФИО — с reply-клавиатурой «Назад/Отмена», поэтому новым сообщением, а подтверждение убираем.
    await delete_quietly(callback.message)
    await flows.ask_full_name(callback.message, state)


@router.callback_query(Registration.confirm_name, NameCb.filter(F.action == NameAction.CONFIRM))
async def on_confirm_name(callback: CallbackQuery, state: FSMContext, session: AsyncSession, client: Client) -> None:
    await callback.answer()
    data = await state.get_data()
    if not data.get(FULL_NAME_KEY):
        # Данные шага потерялись (например, перезапуск Redis) — просим ФИО заново.
        await delete_quietly(callback.message)
        await flows.ask_full_name(callback.message, state)
        return
    service = RegistrationService(session)
    service.save_name(client, data[FULL_NAME_KEY])
    await _next_step_in_place(callback.message, state, session, client, await service.next_step(client))


# --- Специальности и должности (общий мультивыбор) -------------------------------------------------------------

# Переключение галочек — общий хендлер в handlers/selection.py.
@router.callback_query(StateFilter(Registration.specialties, Registration.positions), SelectDoneCb.filter(),
                       group_matches_state)
async def on_select_done(callback: CallbackQuery, callback_data: SelectDoneCb, state: FSMContext,
                         session: AsyncSession, client: Client) -> None:
    selected = list((await state.get_data()).get("selected", []))
    service = RegistrationService(session)
    if callback_data.group is SelectGroup.SPECIALTIES:
        saved = await service.save_specialties(client, selected)
    else:
        saved = await service.save_positions(client, selected)
    if not saved:
        await callback.answer(texts.REG_SELECT_AT_LEAST_ONE, show_alert=True)
        return
    await callback.answer()
    await _next_step_in_place(callback.message, state, session, client, await service.next_step(client))


async def _next_step_in_place(message: Message, state: FSMContext, session: AsyncSession, client: Client,
                              step: RegistrationStep) -> None:
    """Следующий шаг с inline-кнопками — в том же сообщении; финал регистрации — новыми (там reply-клавиатуры)."""
    if step in (RegistrationStep.SPECIALTIES, RegistrationStep.POSITIONS):
        await flows.show_registration_step(message, state, session, client, step, in_place=True)
        return
    await strip_keyboard(message, message.message_id)
    await flows.show_registration_step(message, state, session, client, step)


# --- Необязательные поля ---------------------------------------------------------------------------------------

@router.message(StateFilter(*OPTIONAL_STATES.values()), F.text)
async def on_optional_value(message: Message, state: FSMContext, client: Client, settings: Settings) -> None:
    field = FIELD_BY_STATE[await state.get_state()]
    value = RegistrationService.parse_optional(field, message.text, local_today(settings.timezone))
    if value is None:
        await message.answer(texts.OPTIONAL_ERRORS[field.value] + texts.OPTIONAL_ERROR_SKIP_HINT,
                             reply_markup=keyboards.skip_optional(field.value))
        return
    RegistrationService.save_optional(client, field, value)
    await flows.ask_next_optional(message, state, field)


@router.callback_query(StateFilter(*OPTIONAL_STATES.values()), SkipOptionalCb.filter())
async def on_skip_optional(callback: CallbackQuery, callback_data: SkipOptionalCb, state: FSMContext) -> None:
    await callback.answer()
    current = FIELD_BY_STATE[await state.get_state()]
    if callback_data.field != current.value:
        # Кнопка «Пропустить» от предыдущего вопроса — не сдвигаемся дважды.
        return
    await flows.ask_next_optional(callback.message, state, current, in_place=True)


# --- Неподходящий ввод -----------------------------------------------------------------------------------------

@router.message(StateFilter(*REGISTRATION_TEXT_STATES))
async def text_step_expects_text(message: Message) -> None:
    # Сюда попадают фото, стикеры, голосовые — всё, что не F.text.
    await message.answer(texts.REG_TEXT_ONLY)


@router.message(StateFilter(*REGISTRATION_BUTTON_STATES))
async def button_step_expects_buttons(message: Message) -> None:
    await message.answer(texts.REG_USE_BUTTONS)
