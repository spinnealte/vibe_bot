"""Экраны пользовательских сценариев: что отправить и в какое состояние перейти.

Хендлеры решают «что произошло», flows — «что показать дальше». Так один экран вызывается из разных мест
(меню, после согласия, «Назад») без циклических импортов между роутерами.
"""

from html import escape

from aiogram import Bot
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from stubbot.config import Settings
from stubbot.db.enums import ConsentType
from stubbot.db.models import Client
from stubbot.services.clients import ClientService
from stubbot.services.consents import ConsentService
from stubbot.services.legal import read_document_text
from stubbot.services.registration import (
    OPTIONAL_FIELDS_ORDER,
    OptionalField,
    RegistrationService,
    RegistrationStep,
)
from stubbot.tg import keyboards, texts
from stubbot.tg.callbacks import ConsentKind, SelectGroup
from stubbot.tg.formatting import safe, safe_join
from stubbot.tg.states import OPTIONAL_STATES, Consent, Registration
from stubbot.utils.dates import local_today
from stubbot.utils.names import full_name
from stubbot.utils.profile_fields import experience_years

# Лимит текста сообщения Telegram — 4096; оставляем запас под вступление.
MAX_DOCUMENT_TEXT = 3800


async def enter_cabinet(message: Message, state: FSMContext, session: AsyncSession, client: Client,
                        settings: Settings, bot: Bot) -> None:
    """Вход в «Личный кабинет»: согласие → регистрация → кабинет, с того места, где человек остановился."""
    await state.clear()
    if not await ConsentService(session).has_current_consent(client.id, ConsentType.PERSONAL_DATA):
        await show_consent(message, state, session, settings, ConsentKind.PD)
        return
    step = await RegistrationService(session).next_step(client)
    if step is not RegistrationStep.DONE:
        await show_registration_step(message, state, session, client, step)
        return
    await show_cabinet(message, session, client, settings, bot)


async def show_consent(message: Message, state: FSMContext, session: AsyncSession, settings: Settings,
                       kind: ConsentKind) -> None:
    consent_type = ConsentType.PERSONAL_DATA if kind is ConsentKind.PD else ConsentType.MARKETING
    document = await ConsentService(session).current_document(consent_type)
    text = read_document_text(settings.legal_docs_dir, document) if document else None
    if text is None:
        await state.clear()
        await message.answer(texts.DOCUMENT_UNAVAILABLE, reply_markup=keyboards.main_menu())
        return
    if len(text) > MAX_DOCUMENT_TEXT:
        # Полные юридические тексты длиннее лимита сообщения: показываем начало, ссылка на полный текст — в url.
        tail = f'\n\n<a href="{escape(document.url)}">Полный текст документа</a>' if document.url else ""
        text = text[:MAX_DOCUMENT_TEXT].rsplit("\n", 1)[0] + "\n…" + tail
    if kind is ConsentKind.PD:
        await message.answer(texts.CONSENT_PD_INTRO)
        await state.set_state(Consent.personal_data)
    else:
        await state.set_state(Consent.marketing)
    await message.answer(text, reply_markup=keyboards.consent(kind))


async def show_registration_step(message: Message, state: FSMContext, session: AsyncSession, client: Client,
                                 step: RegistrationStep) -> None:
    service = RegistrationService(session)
    match step:
        case RegistrationStep.PHONE:
            await state.set_state(Registration.phone)
            await message.answer(texts.REG_ASK_PHONE, reply_markup=keyboards.share_phone())
        case RegistrationStep.NAME:
            await ask_full_name(message, state)
        case RegistrationStep.SPECIALTIES:
            await state.set_state(Registration.specialties)
            options = [(s.id, s.title) for s in await service.specialties()]
            selected = await service.selected_specialty_ids(client.id)
            await state.update_data(selected=selected)
            await message.answer(texts.REG_ASK_SPECIALTIES, reply_markup=keyboards.multiselect(
                SelectGroup.SPECIALTIES, options, selected))
        case RegistrationStep.POSITIONS:
            await state.set_state(Registration.positions)
            options = [(p.id, p.title) for p in await service.positions()]
            selected = await service.selected_position_ids(client.id)
            await state.update_data(selected=selected)
            await message.answer(texts.REG_ASK_POSITIONS, reply_markup=keyboards.multiselect(
                SelectGroup.POSITIONS, options, selected))
        case RegistrationStep.DONE:
            if await service.complete(client):
                # Обязательная часть пройдена только что — предлагаем необязательные поля.
                await message.answer(texts.REG_MANDATORY_DONE, reply_markup=keyboards.registration_nav())
                await ask_optional(message, state, OPTIONAL_FIELDS_ORDER[0])
            else:
                await state.clear()
                await message.answer(texts.REG_COMPLETED, reply_markup=keyboards.main_menu())


async def ask_full_name(message: Message, state: FSMContext) -> None:
    await state.set_state(Registration.full_name)
    await message.answer(texts.REG_ASK_FULL_NAME, reply_markup=keyboards.registration_nav())


async def ask_confirm_name(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    await state.set_state(Registration.confirm_name)
    await message.answer(
        texts.REG_CONFIRM_NAME.format(
            last_name=safe(data.get("last_name")),
            first_name=safe(data.get("first_name")),
            middle_name=safe(data.get("middle_name"), placeholder="нет"),
        ),
        reply_markup=keyboards.confirm_name(),
    )


async def ask_optional(message: Message, state: FSMContext, field: OptionalField) -> None:
    await state.set_state(OPTIONAL_STATES[field])
    await message.answer(texts.OPTIONAL_PROMPTS[field.value], reply_markup=keyboards.skip_optional(field.value))


async def ask_next_optional(message: Message, state: FSMContext, current: OptionalField) -> None:
    """Следующее необязательное поле или завершение, если это было последнее."""
    index = OPTIONAL_FIELDS_ORDER.index(current)
    if index + 1 < len(OPTIONAL_FIELDS_ORDER):
        await ask_optional(message, state, OPTIONAL_FIELDS_ORDER[index + 1])
        return
    await state.clear()
    await message.answer(texts.REG_COMPLETED, reply_markup=keyboards.main_menu())


async def show_cabinet(message: Message, session: AsyncSession, client: Client, settings: Settings,
                       bot: Bot) -> None:
    profile = await RegistrationService(session).load_profile(client.id)
    marketing = await ConsentService(session).has_any_consent(client.id, ConsentType.MARKETING)
    referrals = await ClientService(session).count_referrals(client.id)
    me = await bot.me()  # имя бота только из API, не хардкодом; aiogram кэширует ответ
    years = experience_years(profile.practice_since_year, local_today(settings.timezone))
    await message.answer(
        texts.CABINET.format(
            name=safe(full_name(profile.last_name, profile.first_name, profile.middle_name)),
            phone=safe(profile.phone, texts.NOT_SET),
            email=safe(profile.email, texts.NOT_SET),
            birth_date=profile.birth_date.strftime("%d.%m.%Y") if profile.birth_date else texts.NOT_SET,
            city=safe(profile.city, texts.NOT_SET),
            workplace=safe(profile.workplace_raw, texts.NOT_SET),
            experience=_years_text(years) if years is not None else texts.NOT_SET,
            specialties=safe_join([s.title for s in profile.specialties], texts.NOT_SET),
            positions=safe_join([p.title for p in profile.positions], texts.NOT_SET),
            marketing=texts.CABINET_MARKETING_ON if marketing else texts.CABINET_MARKETING_OFF,
            referral_link=safe(f"https://t.me/{me.username}?start=ref_{profile.referral_code}"),
            referrals=referrals,
        ),
        reply_markup=keyboards.main_menu(),
    )


def _years_text(years: int) -> str:
    if years % 10 == 1 and years % 100 != 11:
        word = "год"
    elif 2 <= years % 10 <= 4 and not 12 <= years % 100 <= 14:
        word = "года"
    else:
        word = "лет"
    return f"{years} {word}"
