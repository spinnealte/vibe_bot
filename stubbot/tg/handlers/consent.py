from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from stubbot.config import Settings
from stubbot.db.enums import ConsentType
from stubbot.db.models import Client
from stubbot.services.consents import ConsentService
from stubbot.services.registration import RegistrationService
from stubbot.tg import flows, keyboards, texts
from stubbot.tg.callbacks import ConsentCb, ConsentKind
from stubbot.tg.states import Consent

router = Router(name="consent")


@router.callback_query(Consent.personal_data, ConsentCb.filter(F.kind == ConsentKind.PD))
async def on_pd_consent(callback: CallbackQuery, callback_data: ConsentCb, state: FSMContext,
                        session: AsyncSession, client: Client, settings: Settings) -> None:
    message = callback.message
    await callback.answer()
    await message.edit_reply_markup(reply_markup=None)
    if not callback_data.accept:
        await state.clear()
        await message.answer(texts.CONSENT_PD_DECLINED, reply_markup=keyboards.main_menu())
        return

    service = ConsentService(session)
    # Документ берём заново: между показом и нажатием могла выйти новая версия — тогда показываем её.
    document = await service.current_document(ConsentType.PERSONAL_DATA)
    if document is None:
        await state.clear()
        await message.answer(texts.DOCUMENT_UNAVAILABLE, reply_markup=keyboards.main_menu())
        return
    await service.grant(client.id, ConsentType.PERSONAL_DATA, document)
    await message.answer(texts.CONSENT_PD_ACCEPTED)

    # Рекламное согласие спрашиваем один раз — если человек ещё не отвечал «да».
    if not await service.has_any_consent(client.id, ConsentType.MARKETING):
        await flows.show_consent(message, state, session, settings, ConsentKind.MARKETING)
        return
    await _continue_registration(message, state, session, client)


@router.callback_query(Consent.marketing, ConsentCb.filter(F.kind == ConsentKind.MARKETING))
async def on_marketing_consent(callback: CallbackQuery, callback_data: ConsentCb, state: FSMContext,
                               session: AsyncSession, client: Client) -> None:
    message = callback.message
    await callback.answer()
    await message.edit_reply_markup(reply_markup=None)
    if callback_data.accept:
        service = ConsentService(session)
        document = await service.current_document(ConsentType.MARKETING)
        if document is not None:
            await service.grant(client.id, ConsentType.MARKETING, document)
        await message.answer(texts.CONSENT_MARKETING_ACCEPTED)
    else:
        await message.answer(texts.CONSENT_MARKETING_DECLINED)
    await _continue_registration(message, state, session, client)


@router.message(Consent.personal_data)
@router.message(Consent.marketing)
async def consent_expects_buttons(message: Message) -> None:
    await message.answer(texts.REG_USE_BUTTONS)


async def _continue_registration(message: Message, state: FSMContext, session: AsyncSession, client: Client) -> None:
    step = await RegistrationService(session).next_step(client)
    await flows.show_registration_step(message, state, session, client, step)
