"""Экраны пользовательских сценариев: что отправить и в какое состояние перейти.

Хендлеры решают «что произошло», flows — «что показать дальше». Так один экран вызывается из разных мест
(меню, после согласия, «Назад») без циклических импортов между роутерами.

in_place=True — экран заменяет сообщение, в котором нажали inline-кнопку (tg/screen.py); False — новое сообщение
(ответ на текст пользователя или экран с reply-клавиатурой: её нельзя прикрепить правкой сообщения).
"""

from html import escape

from aiogram import Bot
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State
from aiogram.types import InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from stubbot.config import Settings, get_settings
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
from stubbot.tg import catalog_flow, keyboards, texts
from stubbot.tg.callbacks import ConsentKind, SelectGroup
from stubbot.tg.formatting import safe, safe_join
from stubbot.tg.screen import send_screen, show_screen, strip_keyboard
from stubbot.tg.states import FULL_NAME_KEY, OPTIONAL_STATES, PENDING_APPLY_KEY, Consent, Registration
from stubbot.utils.dates import local_today
from stubbot.utils.profile_fields import experience_years

# Лимит текста сообщения Telegram — 4096; оставляем запас под вступление.
MAX_DOCUMENT_TEXT = 3600
# id сообщения с вопросом, у которого надо убрать кнопки, когда ответ придёт текстом.
PROMPT_MESSAGE_KEY = "prompt_message_id"


async def _screen(message: Message, text: str, markup: InlineKeyboardMarkup | None, in_place: bool) -> Message:
    return await (show_screen if in_place else send_screen)(message, text, markup)


def with_notice(notice: str | None, text: str) -> str:
    return f"{notice}\n\n{text}" if notice else text


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
                       kind: ConsentKind, in_place: bool = False, notice: str | None = None) -> None:
    consent_type = ConsentType.PERSONAL_DATA if kind is ConsentKind.PD else ConsentType.MARKETING
    document = await ConsentService(session).current_document(consent_type)
    text = read_document_text(settings.legal_docs_dir, document) if document else None
    if text is None:
        await state.clear()
        await _screen(message, texts.DOCUMENT_UNAVAILABLE, None, in_place)
        return
    if len(text) > MAX_DOCUMENT_TEXT:
        # Полные юридические тексты длиннее лимита сообщения: показываем начало, ссылка на полный текст — в url.
        tail = f'\n\n<a href="{escape(document.url)}">Полный текст документа</a>' if document.url else ""
        text = text[:MAX_DOCUMENT_TEXT].rsplit("\n", 1)[0] + "\n…" + tail
    if kind is ConsentKind.PD:
        text = f"{texts.CONSENT_PD_INTRO}\n\n{text}"
        await state.set_state(Consent.personal_data)
    else:
        await state.set_state(Consent.marketing)
    await _screen(message, with_notice(notice, text), keyboards.consent(kind), in_place)


async def show_registration_step(message: Message, state: FSMContext, session: AsyncSession, client: Client,
                                 step: RegistrationStep, in_place: bool = False) -> None:
    """in_place действует только на шаги с inline-кнопками (специальности, должности)."""
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
            await _screen(message, texts.REG_ASK_SPECIALTIES,
                          keyboards.multiselect(SelectGroup.SPECIALTIES, options, selected), in_place)
        case RegistrationStep.POSITIONS:
            await state.set_state(Registration.positions)
            options = [(p.id, p.title) for p in await service.positions()]
            selected = await service.selected_position_ids(client.id)
            await state.update_data(selected=selected)
            await _screen(message, texts.REG_ASK_POSITIONS,
                          keyboards.multiselect(SelectGroup.POSITIONS, options, selected), in_place)
        case RegistrationStep.DONE:
            just_completed = await service.complete(client)
            data = await state.get_data()
            if data.get(PENDING_APPLY_KEY):
                # Регистрацию проходили ради заявки — сразу к ней; необязательные поля заполнят в кабинете.
                await message.answer(texts.APPLY_PROFILE_READY, reply_markup=keyboards.main_menu(message.chat.id))
                await catalog_flow.start_application(
                    message, state, session, client, local_today(get_settings().timezone), data[PENDING_APPLY_KEY],
                    index=data.get(catalog_flow.CAROUSEL_INDEX_KEY, 0), in_place=False,
                )
            elif just_completed:
                # Обязательная часть пройдена только что — предлагаем необязательные поля.
                await message.answer(texts.REG_MANDATORY_DONE, reply_markup=keyboards.registration_nav())
                await ask_optional(message, state, OPTIONAL_FIELDS_ORDER[0])
            else:
                await state.clear()
                await message.answer(texts.REG_COMPLETED, reply_markup=keyboards.main_menu(message.chat.id))


async def ask_full_name(message: Message, state: FSMContext) -> None:
    await state.set_state(Registration.full_name)
    await message.answer(texts.REG_ASK_FULL_NAME, reply_markup=keyboards.registration_nav())


async def ask_confirm_name(message: Message, state: FSMContext, confirm_state: State = Registration.confirm_name) -> None:
    """Подтверждение ФИО: общий экран для регистрации и правки в кабинете (там своё состояние)."""
    data = await state.get_data()
    await state.set_state(confirm_state)
    await message.answer(
        texts.REG_CONFIRM_NAME.format(full_name=safe(data.get(FULL_NAME_KEY))),
        reply_markup=keyboards.confirm_name(),
    )


async def ask_optional(message: Message, state: FSMContext, field: OptionalField, in_place: bool = False) -> None:
    """Вопрос необязательного поля. По «Пропустить» — в том же сообщении; после ответа текстом — новым,
    а у прошлого вопроса убираем кнопку «Пропустить»."""
    if not in_place:
        await _strip_previous_prompt(message, state)
    await state.set_state(OPTIONAL_STATES[field])
    prompt = await _screen(message, texts.OPTIONAL_PROMPTS[field.value], keyboards.skip_optional(field.value), in_place)
    await state.update_data({PROMPT_MESSAGE_KEY: prompt.message_id})


async def ask_next_optional(message: Message, state: FSMContext, current: OptionalField, in_place: bool = False) -> None:
    """Следующее необязательное поле или завершение, если это было последнее."""
    index = OPTIONAL_FIELDS_ORDER.index(current)
    if index + 1 < len(OPTIONAL_FIELDS_ORDER):
        await ask_optional(message, state, OPTIONAL_FIELDS_ORDER[index + 1], in_place)
        return
    if in_place:
        await strip_keyboard(message, message.message_id)
    else:
        await _strip_previous_prompt(message, state)
    await state.clear()
    # Новым сообщением: нужно вернуть reply-клавиатуру главного меню вместо «Назад/Отмена».
    await message.answer(texts.REG_COMPLETED, reply_markup=keyboards.main_menu(message.chat.id))


async def _strip_previous_prompt(message: Message, state: FSMContext) -> None:
    prompt_id = (await state.get_data()).get(PROMPT_MESSAGE_KEY)
    if prompt_id:
        await strip_keyboard(message, prompt_id)


# --- Кабинет ---------------------------------------------------------------------------------------------------

async def cabinet_view(session: AsyncSession, client: Client, settings: Settings,
                       bot: Bot) -> tuple[str, InlineKeyboardMarkup]:
    profile = await RegistrationService(session).load_profile(client.id)
    marketing = await ConsentService(session).has_any_consent(client.id, ConsentType.MARKETING)
    referrals = await ClientService(session).count_referrals(client.id)
    me = await bot.me()  # имя бота только из API, не хардкодом; aiogram кэширует ответ
    years = experience_years(profile.practice_since_year, local_today(settings.timezone))
    text = texts.CABINET.format(
        name=safe(profile.full_name),
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
    )
    return text, keyboards.cabinet_actions(marketing_on=marketing, applications=settings.applications_enabled)


async def show_cabinet(message: Message, session: AsyncSession, client: Client, settings: Settings, bot: Bot,
                       notice: str | None = None, in_place: bool = False) -> None:
    text, markup = await cabinet_view(session, client, settings, bot)
    await _screen(message, with_notice(notice, text), markup, in_place)


def _years_text(years: int) -> str:
    if years % 10 == 1 and years % 100 != 11:
        word = "год"
    elif 2 <= years % 10 <= 4 and not 12 <= years % 100 <= 14:
        word = "года"
    else:
        word = "лет"
    return f"{years} {word}"
