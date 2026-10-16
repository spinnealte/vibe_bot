"""Согласия. Ответ на кнопку меняет то же сообщение: экран согласий → «принято» → следующий шаг регистрации.

При регистрации (требование заказчика 16.10.2026) - две отдельные галочки и «Продолжить»: 1 - политика и обработка
ПД, обязательна (без неё дальше не пускает, данные профиля бот не спрашивает и не сохраняет); 2 - рекламные
рассылки, по желанию. Из кабинета рассылки включаются отдельно - текст согласия и «Да / Нет».
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
from stubbot.tg.callbacks import ConsentCb, ConsentFormAction, ConsentFormCb, ConsentKind
from stubbot.tg.flows import ADS_CHECKED_KEY, ADS_DOCUMENT_KEY, CONSENT_FORM_KEYS, PD_CHECKED_KEY, PD_DOCUMENT_KEY
from stubbot.tg.middlewares.client import telegram_profile
from stubbot.tg.screen import show_screen
from stubbot.tg.states import Consent

router = Router(name="consent")

RETURN_TO_CABINET = "cabinet"


@router.callback_query(Consent.personal_data,
                       ConsentFormCb.filter(F.action.in_({ConsentFormAction.PD, ConsentFormAction.ADS})))
async def on_consent_toggle(callback: CallbackQuery, callback_data: ConsentFormCb, state: FSMContext) -> None:
    """Галочка 1 или 2: каждое нажатие ставит или снимает её; меняются только кнопки."""
    data = await state.get_data()
    with_ads = data.get(ADS_DOCUMENT_KEY) is not None
    if callback_data.action is ConsentFormAction.ADS and not with_ads:
        await callback.answer(texts.UNKNOWN_CALLBACK)  # второй галочки на этом экране нет
        return
    key = PD_CHECKED_KEY if callback_data.action is ConsentFormAction.PD else ADS_CHECKED_KEY
    data[key] = not data.get(key, False)
    await state.update_data({key: data[key]})
    await callback.answer()
    markup = keyboards.consent_form(pd=bool(data.get(PD_CHECKED_KEY)), ads=bool(data.get(ADS_CHECKED_KEY)),
                                    with_ads=with_ads)
    try:
        await callback.message.edit_reply_markup(reply_markup=markup)
    except TelegramBadRequest:
        pass  # кнопки уже такие (два нажатия подряд) - менять нечего


@router.callback_query(Consent.personal_data, ConsentFormCb.filter(F.action == ConsentFormAction.CONTINUE))
async def on_consent_continue(callback: CallbackQuery, state: FSMContext, session: AsyncSession,
                              client: Client) -> None:
    message = callback.message
    data = await state.get_data()
    if not data.get(PD_CHECKED_KEY):
        await callback.answer(texts.CONSENT_PD_CHECK_REQUIRED, show_alert=True)
        return
    await callback.answer()

    service = ConsentService(session)
    pd_document = await service.current_document(ConsentType.PERSONAL_DATA)
    ads_document = await service.current_document(ConsentType.MARKETING)
    if pd_document is None:
        await state.clear()
        await show_screen(message, texts.DOCUMENT_UNAVAILABLE)
        return
    shown_ads = data.get(ADS_DOCUMENT_KEY)
    if pd_document.id != data.get(PD_DOCUMENT_KEY) or (shown_ads is not None and (
            ads_document is None or ads_document.id != shown_ads)):
        # Пока человек читал, вышла новая версия документов - соглашаться надо с ней: показываем заново.
        await flows.show_consent_form(message, state, session, client, in_place=True, notice=texts.CONSENT_PD_UPDATED)
        return

    await service.grant(client.id, ConsentType.PERSONAL_DATA, pd_document)
    # Согласие есть - теперь можно запомнить имя и @username из Telegram (до согласия их не храним).
    ClientService(session).remember_profile(client, telegram_profile(callback.from_user))
    answer = texts.CONSENT_PD_ACCEPTED
    if shown_ads is not None:
        if data.get(ADS_CHECKED_KEY):
            await service.grant(client.id, ConsentType.MARKETING, ads_document)
            answer += "\n\n" + texts.CONSENT_MARKETING_ACCEPTED
        else:
            # Галочка стояла (соглашался раньше), а её сняли - это отказ от рассылок.
            if await service.has_any_consent(client.id, ConsentType.MARKETING):
                await service.revoke(client.id, ConsentType.MARKETING)
            answer += "\n\n" + texts.CONSENT_MARKETING_DECLINED
    await state.set_data({k: v for k, v in data.items() if k not in CONSENT_FORM_KEYS})
    await show_screen(message, answer)
    await _continue_registration(message, state, session, client)


@router.callback_query(Consent.marketing, ConsentCb.filter(F.kind == ConsentKind.MARKETING))
async def on_marketing_consent(callback: CallbackQuery, callback_data: ConsentCb, state: FSMContext,
                               session: AsyncSession, client: Client, settings: Settings, bot: Bot) -> None:
    """«Да / Нет» на включение рассылок из кабинета."""
    message = callback.message
    await callback.answer()
    if callback_data.accept:
        service = ConsentService(session)
        document = await service.current_document(ConsentType.MARKETING)
        if document is not None:
            await service.grant(client.id, ConsentType.MARKETING, document)
    answer = texts.CONSENT_MARKETING_ACCEPTED if callback_data.accept else texts.CONSENT_MARKETING_DECLINED

    if (await state.get_data()).get("return_to") == RETURN_TO_CABINET:
        # Включали рассылки из кабинета - возвращаем кабинет в то же сообщение.
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
    # Следующий шаг (телефон) - с reply-клавиатурой, поэтому новым сообщением.
    step = await RegistrationService(session).next_step(client)
    await flows.show_registration_step(message, state, session, client, step)
