"""Личный кабинет: редактирование профиля, рассылки. «Мои заявки» — в handlers/application.py.

Кнопки меняют то же сообщение: кабинет → меню полей → вопрос → снова кабинет с отметкой «Сохранено».
Если ответ приходит текстом, кабинет присылается новым сообщением, а у вопроса убираются кнопки.
Телефон — исключение: нужна reply-кнопка «Поделиться номером», её правкой сообщения не прикрепить.

Один общий редактор на все простые поля (город, клиника, опыт, email, дата рождения): те же проверки, что при
регистрации.
"""

from datetime import date

from aiogram import Bot, F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from stubbot.config import Settings
from stubbot.db.enums import ConsentType
from stubbot.db.models import Client
from stubbot.services.consents import ConsentService
from stubbot.services.registration import OptionalField, PhoneResult, RegistrationService
from stubbot.tg import flows, keyboards, texts
from stubbot.tg.callbacks import (
    CabinetAction,
    CabinetCb,
    ConsentKind,
    EditControl,
    EditControlCb,
    EditField,
    EditFieldCb,
    NameAction,
    NameCb,
    SelectDoneCb,
    SelectGroup,
)
from stubbot.tg.flows import PROMPT_MESSAGE_KEY
from stubbot.tg.formatting import safe
from stubbot.tg.handlers.consent import RETURN_TO_CABINET
from stubbot.tg.handlers.selection import group_matches_state, selection_options
from stubbot.tg.screen import delete_quietly, show_screen, strip_keyboard
from stubbot.tg.states import FULL_NAME_KEY, EditProfile
from stubbot.utils.dates import local_today
from stubbot.utils.names import parse_full_name
from stubbot.utils.profile_fields import experience_years

router = Router(name="cabinet")


# --- Кнопки под профилем ---------------------------------------------------------------------------------------

@router.callback_query(CabinetCb.filter(F.action == CabinetAction.EDIT_MENU))
async def open_edit_menu(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.answer()
    await show_screen(callback.message, texts.EDIT_MENU, keyboards.edit_menu())


@router.callback_query(CabinetCb.filter(F.action == CabinetAction.BACK))
async def back_to_cabinet(callback: CallbackQuery, state: FSMContext, session: AsyncSession, client: Client,
                          settings: Settings, bot: Bot) -> None:
    await state.clear()
    await callback.answer()
    await flows.show_cabinet(callback.message, session, client, settings, bot, in_place=True)


@router.callback_query(CabinetCb.filter(F.action == CabinetAction.MARKETING))
async def toggle_marketing(callback: CallbackQuery, state: FSMContext, session: AsyncSession, client: Client,
                           settings: Settings, bot: Bot) -> None:
    await callback.answer()
    service = ConsentService(session)
    if await service.has_any_consent(client.id, ConsentType.MARKETING):
        await service.revoke(client.id, ConsentType.MARKETING)
        await flows.show_cabinet(callback.message, session, client, settings, bot,
                                 notice=texts.MARKETING_TURNED_OFF, in_place=True)
        return
    # Включение — это новое согласие: показываем текст документа вместо кабинета, после ответа — кабинет обратно.
    await state.clear()
    await state.update_data(return_to=RETURN_TO_CABINET)
    await flows.show_consent(callback.message, state, session, settings, ConsentKind.MARKETING, in_place=True)


# --- Выбор поля ------------------------------------------------------------------------------------------------

@router.callback_query(EditFieldCb.filter())
async def choose_field(callback: CallbackQuery, callback_data: EditFieldCb, state: FSMContext,
                       session: AsyncSession, client: Client, settings: Settings) -> None:
    await callback.answer()
    await state.clear()
    message = callback.message
    match callback_data.field:
        case EditField.FULL_NAME:
            await state.set_state(EditProfile.full_name)
            await _prompt(message, state, texts.EDIT_ASK_FULL_NAME + _current(client.full_name), can_clear=False)
        case EditField.PHONE:
            await state.set_state(EditProfile.phone)
            await delete_quietly(message)
            await message.answer(texts.EDIT_ASK_PHONE + _current(client.phone), reply_markup=keyboards.share_phone())
        case EditField.SPECIALTIES | EditField.POSITIONS:
            await _ask_selection(message, state, session, client, callback_data.field)
        case _:
            field = OptionalField(callback_data.field.value)
            await state.set_state(EditProfile.optional_value)
            await state.update_data(field=field.value)
            current = _optional_display(client, field, local_today(settings.timezone))
            await _prompt(message, state, texts.OPTIONAL_PROMPTS[field.value] + _current(current),
                          can_clear=current is not None)


async def _prompt(message: Message, state: FSMContext, text: str, can_clear: bool) -> None:
    """Вопрос вместо меню полей; запоминаем его, чтобы убрать кнопки, когда ответ придёт текстом."""
    prompt = await show_screen(message, text, keyboards.edit_controls(can_clear=can_clear))
    await state.update_data({PROMPT_MESSAGE_KEY: prompt.message_id})


async def _ask_selection(message: Message, state: FSMContext, session: AsyncSession, client: Client,
                         field: EditField) -> None:
    service = RegistrationService(session)
    if field is EditField.SPECIALTIES:
        group, prompt, new_state = SelectGroup.SPECIALTIES, texts.EDIT_ASK_SPECIALTIES, EditProfile.specialties
        selected = await service.selected_specialty_ids(client.id)
    else:
        group, prompt, new_state = SelectGroup.POSITIONS, texts.EDIT_ASK_POSITIONS, EditProfile.positions
        selected = await service.selected_position_ids(client.id)
    await state.set_state(new_state)
    await state.update_data(selected=selected)
    await show_screen(message, prompt, keyboards.multiselect(group, await selection_options(session, group), selected))


# --- Отмена / очистка ------------------------------------------------------------------------------------------

@router.callback_query(StateFilter(EditProfile), EditControlCb.filter(F.action == EditControl.CANCEL))
async def cancel_edit(callback: CallbackQuery, state: FSMContext, session: AsyncSession, client: Client,
                      settings: Settings, bot: Bot) -> None:
    await callback.answer()
    await state.clear()
    await flows.show_cabinet(callback.message, session, client, settings, bot, notice=texts.EDIT_CANCELLED,
                             in_place=True)


@router.message(StateFilter(EditProfile), F.text == texts.BTN_CANCEL)
async def cancel_edit_by_keyboard(message: Message, state: FSMContext, session: AsyncSession, client: Client,
                                  settings: Settings, bot: Bot) -> None:
    # «Отмена» с reply-клавиатуры телефона: возвращаем клавиатуру меню и кабинет новым сообщением.
    await state.clear()
    await message.answer(texts.EDIT_CANCELLED, reply_markup=keyboards.main_menu())
    await flows.show_cabinet(message, session, client, settings, bot)


@router.callback_query(EditProfile.optional_value, EditControlCb.filter(F.action == EditControl.CLEAR))
async def clear_optional(callback: CallbackQuery, state: FSMContext, session: AsyncSession, client: Client,
                         settings: Settings, bot: Bot) -> None:
    await callback.answer()
    field = OptionalField((await state.get_data())["field"])
    RegistrationService.save_optional(client, field, None)
    await state.clear()
    await flows.show_cabinet(callback.message, session, client, settings, bot, notice=texts.EDIT_CLEARED,
                             in_place=True)


# --- Простые поля ----------------------------------------------------------------------------------------------

@router.message(EditProfile.optional_value, F.text)
async def on_optional_value(message: Message, state: FSMContext, session: AsyncSession, client: Client,
                            settings: Settings, bot: Bot) -> None:
    data = await state.get_data()
    field = OptionalField(data["field"])
    value = RegistrationService.parse_optional(field, message.text, local_today(settings.timezone))
    if value is None:
        await message.answer(texts.OPTIONAL_ERRORS[field.value] + texts.EDIT_ERROR_CANCEL_HINT)
        return
    RegistrationService.save_optional(client, field, value)
    await _saved_after_text(message, state, session, client, settings, bot)


# --- ФИО -------------------------------------------------------------------------------------------------------

@router.message(EditProfile.full_name, F.text)
async def on_full_name(message: Message, state: FSMContext) -> None:
    name = parse_full_name(message.text)
    if name is None:
        await message.answer(texts.REG_NAME_INVALID + texts.EDIT_ERROR_CANCEL_HINT)
        return
    await _strip_prompt(message, state)
    await state.update_data({FULL_NAME_KEY: name})
    await flows.ask_confirm_name(message, state, confirm_state=EditProfile.confirm_name)


@router.callback_query(EditProfile.confirm_name, NameCb.filter(F.action == NameAction.FIX))
async def on_fix_name(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.set_state(EditProfile.full_name)
    await _prompt(callback.message, state, texts.EDIT_ASK_FULL_NAME, can_clear=False)


@router.callback_query(EditProfile.confirm_name, NameCb.filter(F.action == NameAction.CONFIRM))
async def on_confirm_name(callback: CallbackQuery, state: FSMContext, session: AsyncSession, client: Client,
                          settings: Settings, bot: Bot) -> None:
    await callback.answer()
    data = await state.get_data()
    await state.clear()
    notice = texts.EDIT_CANCELLED
    if data.get(FULL_NAME_KEY):
        RegistrationService(session).save_name(client, data[FULL_NAME_KEY])
        notice = texts.EDIT_SAVED
    await flows.show_cabinet(callback.message, session, client, settings, bot, notice=notice, in_place=True)


# --- Телефон ---------------------------------------------------------------------------------------------------

@router.message(EditProfile.phone, F.contact)
async def on_contact(message: Message, state: FSMContext, session: AsyncSession, client: Client,
                     settings: Settings, bot: Bot) -> None:
    if message.contact.user_id != message.from_user.id:
        await message.answer(texts.REG_PHONE_FOREIGN_CONTACT)
        return
    match await RegistrationService(session).save_phone(client, message.contact.phone_number):
        case PhoneResult.INVALID:
            await message.answer(texts.REG_PHONE_INVALID)
            return
        case PhoneResult.TAKEN:
            notice = texts.REG_PHONE_TAKEN
        case _:
            notice = texts.EDIT_SAVED
    await state.clear()
    # Новым сообщением: убираем кнопку «Поделиться номером», возвращаем клавиатуру меню.
    await message.answer(notice, reply_markup=keyboards.main_menu())
    await flows.show_cabinet(message, session, client, settings, bot)


@router.message(EditProfile.phone)
async def phone_expects_button(message: Message) -> None:
    await message.answer(texts.REG_PHONE_USE_BUTTON, reply_markup=keyboards.share_phone())


# --- Специальности и должности ---------------------------------------------------------------------------------

@router.callback_query(StateFilter(EditProfile.specialties, EditProfile.positions), SelectDoneCb.filter(),
                       group_matches_state)
async def on_select_done(callback: CallbackQuery, callback_data: SelectDoneCb, state: FSMContext,
                         session: AsyncSession, client: Client, settings: Settings, bot: Bot) -> None:
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
    await state.clear()
    await flows.show_cabinet(callback.message, session, client, settings, bot, notice=texts.EDIT_SAVED,
                             in_place=True)


# --- Неподходящий ввод -----------------------------------------------------------------------------------------

@router.message(StateFilter(EditProfile.full_name, EditProfile.optional_value))
async def edit_expects_text(message: Message) -> None:
    await message.answer(texts.REG_TEXT_ONLY)


@router.message(StateFilter(EditProfile.confirm_name, EditProfile.specialties, EditProfile.positions))
async def edit_expects_buttons(message: Message) -> None:
    await message.answer(texts.REG_USE_BUTTONS)


# --- Вспомогательное -------------------------------------------------------------------------------------------

async def _strip_prompt(message: Message, state: FSMContext) -> None:
    prompt_id = (await state.get_data()).get(PROMPT_MESSAGE_KEY)
    if prompt_id:
        await strip_keyboard(message, prompt_id)


async def _saved_after_text(message: Message, state: FSMContext, session: AsyncSession, client: Client,
                            settings: Settings, bot: Bot) -> None:
    """Ответ пришёл текстом: у вопроса убираем кнопки, обновлённый кабинет — новым сообщением под ответом."""
    await _strip_prompt(message, state)
    await state.clear()
    await flows.show_cabinet(message, session, client, settings, bot, notice=texts.EDIT_SAVED)


def _current(value: str | None) -> str:
    return texts.EDIT_CURRENT_VALUE.format(value=safe(value, texts.NOT_SET))


def _optional_display(client: Client, field: OptionalField, today: date) -> str | None:
    match field:
        case OptionalField.EXPERIENCE:
            years = experience_years(client.practice_since_year, today)
            return None if years is None else str(years)
        case OptionalField.BIRTH_DATE:
            return client.birth_date.strftime("%d.%m.%Y") if client.birth_date else None
        case _:
            return getattr(client, field.value)
