"""Личный кабинет: редактирование профиля, рассылки, заявки.

Один общий редактор на все простые поля (город, клиника, опыт, email, дата рождения): те же проверки, что при
регистрации. ФИО, телефон и справочники — свои короткие сценарии, но с теми же экранами и сервисами.
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
from stubbot.services.enrollments import CLIENT_CANCELLABLE, EnrollmentService
from stubbot.services.registration import OptionalField, PhoneResult, RegistrationService
from stubbot.tg import flows, keyboards, render, texts
from stubbot.tg.callbacks import (
    CabinetAction,
    CabinetCb,
    ConsentKind,
    EditControl,
    EditControlCb,
    EditField,
    EditFieldCb,
    MyApplicationAction,
    MyApplicationCb,
    NameAction,
    NameCb,
    SelectDoneCb,
    SelectGroup,
)
from stubbot.tg.formatting import safe
from stubbot.tg.handlers.consent import RETURN_TO_CABINET
from stubbot.tg.handlers.selection import selection_options
from stubbot.tg.states import EditProfile
from stubbot.utils.dates import format_range, local_today
from stubbot.utils.names import full_name, parse_full_name
from stubbot.utils.profile_fields import experience_years

router = Router(name="cabinet")


# --- Кнопки под профилем ---------------------------------------------------------------------------------------

@router.callback_query(CabinetCb.filter(F.action == CabinetAction.EDIT_MENU))
async def open_edit_menu(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.answer()
    await callback.message.answer(texts.EDIT_MENU, reply_markup=keyboards.edit_menu())


@router.callback_query(CabinetCb.filter(F.action == CabinetAction.BACK))
async def back_to_cabinet(callback: CallbackQuery, state: FSMContext, session: AsyncSession, client: Client,
                          settings: Settings, bot: Bot) -> None:
    await state.clear()
    await callback.answer()
    await callback.message.edit_reply_markup(reply_markup=None)
    await flows.show_cabinet(callback.message, session, client, settings, bot)


@router.callback_query(CabinetCb.filter(F.action == CabinetAction.APPLICATIONS))
async def open_applications(callback: CallbackQuery, session: AsyncSession, client: Client) -> None:
    await callback.answer()
    await _send_applications(callback.message, session, client)


@router.callback_query(MyApplicationCb.filter(F.action == MyApplicationAction.ASK_CANCEL))
async def ask_cancel_application(callback: CallbackQuery, callback_data: MyApplicationCb, session: AsyncSession,
                                 client: Client) -> None:
    rows = await EnrollmentService(session).my_applications(client.id)
    row = next((r for r in rows if r[0].id == callback_data.enrollment_id and r[0].status in CLIENT_CANCELLABLE), None)
    if row is None:
        await callback.answer(texts.APPLICATION_CANNOT_CANCEL, show_alert=True)
        return
    await callback.answer()
    _, course_session, program = row
    await callback.message.answer(
        texts.CANCEL_APPLICATION_CONFIRM.format(
            title=safe(course_session.title_override or program.title),
            dates=format_range(course_session.start_date, course_session.end_date),
        ),
        reply_markup=keyboards.confirm_cancel_application(callback_data.enrollment_id),
    )


@router.callback_query(MyApplicationCb.filter(F.action == MyApplicationAction.CANCEL))
async def cancel_application(callback: CallbackQuery, callback_data: MyApplicationCb, session: AsyncSession,
                             client: Client) -> None:
    cancelled = await EnrollmentService(session).cancel(client.id, callback_data.enrollment_id)
    if cancelled is None:
        await callback.answer(texts.APPLICATION_CANNOT_CANCEL, show_alert=True)
        return
    await callback.answer()
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(texts.APPLICATION_CANCELLED)
    await _send_applications(callback.message, session, client)


@router.callback_query(MyApplicationCb.filter(F.action == MyApplicationAction.KEEP))
async def keep_application(callback: CallbackQuery) -> None:
    await callback.answer()
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(texts.EDIT_CANCELLED)


async def _send_applications(message: Message, session: AsyncSession, client: Client) -> None:
    rows = await EnrollmentService(session).my_applications(client.id)
    if not rows:
        await message.answer(texts.MY_APPLICATIONS_EMPTY)
        return
    text, cancellable = render.my_applications(rows)
    await message.answer(text, reply_markup=keyboards.my_applications(cancellable))


@router.callback_query(CabinetCb.filter(F.action == CabinetAction.MARKETING))
async def toggle_marketing(callback: CallbackQuery, state: FSMContext, session: AsyncSession, client: Client,
                           settings: Settings) -> None:
    await callback.answer()
    service = ConsentService(session)
    if await service.has_any_consent(client.id, ConsentType.MARKETING):
        await service.revoke(client.id, ConsentType.MARKETING)
        await callback.message.edit_reply_markup(reply_markup=keyboards.cabinet_actions(marketing_on=False))
        await callback.message.answer(texts.MARKETING_TURNED_OFF)
        return
    # Включение — это новое согласие: показываем текст документа и спрашиваем, как при регистрации.
    await state.clear()
    await state.update_data(return_to=RETURN_TO_CABINET)
    await flows.show_consent(callback.message, state, session, settings, ConsentKind.MARKETING)


# --- Выбор поля ------------------------------------------------------------------------------------------------

@router.callback_query(EditFieldCb.filter())
async def choose_field(callback: CallbackQuery, callback_data: EditFieldCb, state: FSMContext,
                       session: AsyncSession, client: Client, settings: Settings) -> None:
    await callback.answer()
    await state.clear()
    message = callback.message
    match callback_data.field:
        case EditField.FULL_NAME:
            current = full_name(client.last_name, client.first_name, client.middle_name)
            await state.set_state(EditProfile.full_name)
            await message.answer(texts.EDIT_ASK_FULL_NAME + _current(current),
                                 reply_markup=keyboards.edit_controls(can_clear=False))
        case EditField.PHONE:
            await state.set_state(EditProfile.phone)
            await message.answer(texts.EDIT_ASK_PHONE + _current(client.phone), reply_markup=keyboards.share_phone())
        case EditField.SPECIALTIES | EditField.POSITIONS:
            await _ask_selection(message, state, session, client, callback_data.field)
        case _:
            field = OptionalField(callback_data.field.value)
            await state.set_state(EditProfile.optional_value)
            await state.update_data(field=field.value)
            current = _optional_display(client, field, local_today(settings.timezone))
            await message.answer(texts.OPTIONAL_PROMPTS[field.value] + _current(current),
                                 reply_markup=keyboards.edit_controls(can_clear=current is not None))


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
    await message.answer(prompt, reply_markup=keyboards.multiselect(group, await selection_options(session, group),
                                                                    selected))


# --- Отмена / очистка ------------------------------------------------------------------------------------------

@router.callback_query(StateFilter(EditProfile), EditControlCb.filter(F.action == EditControl.CANCEL))
async def cancel_edit(callback: CallbackQuery, state: FSMContext, session: AsyncSession, client: Client,
                      settings: Settings, bot: Bot) -> None:
    await callback.answer()
    await callback.message.edit_reply_markup(reply_markup=None)
    await _finish(callback.message, state, session, client, settings, bot, texts.EDIT_CANCELLED)


@router.message(StateFilter(EditProfile), F.text == texts.BTN_CANCEL)
async def cancel_edit_by_keyboard(message: Message, state: FSMContext, session: AsyncSession, client: Client,
                                  settings: Settings, bot: Bot) -> None:
    await _finish(message, state, session, client, settings, bot, texts.EDIT_CANCELLED)


@router.callback_query(EditProfile.optional_value, EditControlCb.filter(F.action == EditControl.CLEAR))
async def clear_optional(callback: CallbackQuery, state: FSMContext, session: AsyncSession, client: Client,
                         settings: Settings, bot: Bot) -> None:
    await callback.answer()
    await callback.message.edit_reply_markup(reply_markup=None)
    field = OptionalField((await state.get_data())["field"])
    RegistrationService.save_optional(client, field, None)
    await _finish(callback.message, state, session, client, settings, bot, texts.EDIT_CLEARED)


# --- Простые поля ----------------------------------------------------------------------------------------------

@router.message(EditProfile.optional_value, F.text)
async def on_optional_value(message: Message, state: FSMContext, session: AsyncSession, client: Client,
                            settings: Settings, bot: Bot) -> None:
    data = await state.get_data()
    field = OptionalField(data["field"])
    value = RegistrationService.parse_optional(field, message.text, local_today(settings.timezone))
    if value is None:
        await message.answer(texts.OPTIONAL_ERRORS[field.value] + texts.EDIT_ERROR_CANCEL_HINT,
                             reply_markup=keyboards.edit_controls(can_clear=False))
        return
    RegistrationService.save_optional(client, field, value)
    await _finish(message, state, session, client, settings, bot, texts.EDIT_SAVED)


# --- ФИО -------------------------------------------------------------------------------------------------------

@router.message(EditProfile.full_name, F.text)
async def on_full_name(message: Message, state: FSMContext) -> None:
    name = parse_full_name(message.text)
    if name is None:
        await message.answer(texts.REG_NAME_INVALID + texts.EDIT_ERROR_CANCEL_HINT,
                             reply_markup=keyboards.edit_controls(can_clear=False))
        return
    await state.update_data(last_name=name.last_name, first_name=name.first_name, middle_name=name.middle_name)
    await flows.ask_confirm_name(message, state, confirm_state=EditProfile.confirm_name)


@router.callback_query(EditProfile.confirm_name, NameCb.filter(F.action == NameAction.FIX))
async def on_fix_name(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await callback.message.edit_reply_markup(reply_markup=None)
    await state.set_state(EditProfile.full_name)
    await callback.message.answer(texts.EDIT_ASK_FULL_NAME, reply_markup=keyboards.edit_controls(can_clear=False))


@router.callback_query(EditProfile.confirm_name, NameCb.filter(F.action == NameAction.CONFIRM))
async def on_confirm_name(callback: CallbackQuery, state: FSMContext, session: AsyncSession, client: Client,
                          settings: Settings, bot: Bot) -> None:
    await callback.answer()
    await callback.message.edit_reply_markup(reply_markup=None)
    data = await state.get_data()
    if data.get("last_name") and data.get("first_name"):
        RegistrationService(session).save_name(client, data["last_name"], data["first_name"], data.get("middle_name"))
        await _finish(callback.message, state, session, client, settings, bot, texts.EDIT_SAVED)
    else:
        await _finish(callback.message, state, session, client, settings, bot, texts.EDIT_CANCELLED)


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
        case PhoneResult.TAKEN:
            await _finish(message, state, session, client, settings, bot, texts.REG_PHONE_TAKEN)
        case PhoneResult.OK:
            await _finish(message, state, session, client, settings, bot, texts.EDIT_SAVED)


@router.message(EditProfile.phone)
async def phone_expects_button(message: Message) -> None:
    await message.answer(texts.REG_PHONE_USE_BUTTON, reply_markup=keyboards.share_phone())


# --- Специальности и должности ---------------------------------------------------------------------------------

@router.callback_query(StateFilter(EditProfile.specialties, EditProfile.positions), SelectDoneCb.filter())
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
    await callback.message.edit_reply_markup(reply_markup=None)
    await _finish(callback.message, state, session, client, settings, bot, texts.EDIT_SAVED)


# --- Неподходящий ввод -----------------------------------------------------------------------------------------

@router.message(StateFilter(EditProfile.full_name, EditProfile.optional_value))
async def edit_expects_text(message: Message) -> None:
    await message.answer(texts.REG_TEXT_ONLY)


@router.message(StateFilter(EditProfile.confirm_name, EditProfile.specialties, EditProfile.positions))
async def edit_expects_buttons(message: Message) -> None:
    await message.answer(texts.REG_USE_BUTTONS)


# --- Вспомогательное -------------------------------------------------------------------------------------------

async def _finish(message: Message, state: FSMContext, session: AsyncSession, client: Client, settings: Settings,
                  bot: Bot, notice: str) -> None:
    """Итог правки + обновлённый кабинет. Возвращает постоянную клавиатуру меню (после «Поделиться номером»)."""
    await state.clear()
    await message.answer(notice, reply_markup=keyboards.main_menu())
    await flows.show_cabinet(message, session, client, settings, bot)


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
