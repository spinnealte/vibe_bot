"""Согласия. Ответ на кнопку меняет то же сообщение: текст согласия → «принято» / следующий вопрос.

Согласие на обработку ПД — галочка «Я согласен» и «Продолжить» (требование заказчика 16.10.2026): без галочки
дальше не пускает, данные профиля бот не спрашивает и не сохраняет. Рекламные рассылки — отдельный вопрос
«Да / Нет» после него, по желанию.
"""

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from stubbot.config import Settings
from stubbot.db.enums import ConsentType
from stubbot.db.models import Client
from stubbot.services.clients import ClientService
from stubbot.services.consents import ConsentService
from stubbot.services.registration import RegistrationService
from stubbot.tg import flows, keyboards, texts
from stubbot.tg.callbacks import ConsentCb, ConsentKind, PdConsentAction, PdConsentCb
from stubbot.tg.flows import PD_CHECKED_KEY, PD_DOCUMENT_KEY
from stubbot.tg.middlewares.client import telegram_profile
from stubbot.tg.screen import show_screen
from stubbot.tg.states import Consent

router = Router(name="consent")

RETURN_TO_CABINET = "cabinet"


@router.callback_query(Consent.personal_data, PdConsentCb.filter(F.action == PdConsentAction.TOGGLE))
async def on_pd_toggle(callback: CallbackQuery, state: FSMContext) -> None:
    """Галочка «Я согласен»: каждое нажатие ставит или снимает её; меняются только кнопки."""
    checked = not (await state.get_data()).get(PD_CHECKED_KEY, False)
    await state.update_data({PD_CHECKED_KEY: checked})
    await callback.answer()
    try:
        await callback.message.edit_reply_markup(reply_markup=keyboards.pd_consent(checked))
    except TelegramBadRequest:
        pass  # кнопки уже такие (два нажатия подряд) — менять нечего


@router.callback_query(Consent.personal_data, PdConsentCb.filter(F.action == PdConsentAction.CONTINUE))
async def on_pd_continue(callback: CallbackQuery, state: FSMContext, session: AsyncSession, client: Client) -> None:
    message = callback.message
    data = await state.get_data()
    if not data.get(PD_CHECKED_KEY):
        await callback.answer(texts.CONSENT_PD_CHECK_REQUIRED, show_alert=True)
        return
    await callback.answer()

    service = ConsentService(session)
    document = await service.current_document(ConsentType.PERSONAL_DATA)
    if document is None:
        await state.clear()
        await show_screen(message, texts.DOCUMENT_UNAVAILABLE)
        return
    if document.id != data.get(PD_DOCUMENT_KEY):
        # Пока человек читал, вышла новая версия документов — соглашаться надо с ней: показываем заново.
        await flows.show_consent(message, state, session, ConsentKind.PD, in_place=True,
                                 notice=texts.CONSENT_PD_UPDATED)
        return
    await service.grant(client.id, ConsentType.PERSONAL_DATA, document)
    # Согласие есть — теперь можно запомнить имя и @username из Telegram (до согласия их не храним).
    ClientService(session).remember_profile(client, telegram_profile(callback.from_user))
    await state.set_data({k: v for k, v in data.items() if k not in (PD_CHECKED_KEY, PD_DOCUMENT_KEY)})

    # Рекламное согласие спрашиваем один раз — если человек ещё не отвечал «да». Тем же сообщением.
    if not await service.has_any_consent(client.id, ConsentType.MARKETING):
        await flows.show_consent(message, state, session, ConsentKind.MARKETING, in_place=True,
                                 notice=texts.CONSENT_PD_ACCEPTED)
        return
    await show_screen(message, texts.CONSENT_PD_ACCEPTED)
    await _continue_registration(message, state, session, client)


@router.callback_query(Consent.marketing, ConsentCb.filter(F.kind == ConsentKind.MARKETING))
async def on_marketing_consent(callback: CallbackQuery, callback_data: ConsentCb, state: FSMContext,
                               session: AsyncSession, client: Client, settings: Settings, bot: Bot) -> None:
    message = callback.message
    await callback.answer()
    if callback_data.accept:
        service = ConsentService(session)
        document = await service.current_document(ConsentType.MARKETING)
        if document is not None:
            await service.grant(client.id, ConsentType.MARKETING, document)
    answer = texts.CONSENT_MARKETING_ACCEPTED if callback_data.accept else texts.CONSENT_MARKETING_DECLINED

    if (await state.get_data()).get("return_to") == RETURN_TO_CABINET:
        # Включали рассылки из кабинета — возвращаем кабинет в то же сообщение.
        await state.clear()
        await flows.show_cabinet(message, session, client, settings, bot, notice=answer, in_place=True)
        return
    await show_screen(message, answer)
    await _continue_registration(message, state, session, client)


@router.message(Consent.personal_data)
@router.message(Consent.marketing)
async def consent_expects_buttons(message: Message) -> None:
    await message.answer(texts.REG_USE_BUTTONS)


async def _continue_registration(message: Message, state: FSMContext, session: AsyncSession, client: Client) -> None:
    # Следующий шаг (телефон) — с reply-клавиатурой, поэтому новым сообщением.
    step = await RegistrationService(session).next_step(client)
    await flows.show_registration_step(message, state, session, client, step)
