"""Заявка на поток: тариф → число мест → комментарий → подтверждение. Все проверки — в EnrollmentService."""

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from stubbot.config import Settings
from stubbot.db.models import Client
from stubbot.services.enrollments import MAX_COMMENT_LENGTH, ApplyOutcome, EnrollmentService
from stubbot.tg import catalog_flow, keyboards, texts
from stubbot.tg.callbacks import ApplyAction, ApplyCb, ApplyPriceCb, ApplySeatsCb
from stubbot.tg.states import PENDING_APPLY_KEY, Application
from stubbot.utils.dates import local_today

router = Router(name="application")


@router.callback_query(StateFilter(Application), ApplyCb.filter(F.action == ApplyAction.CANCEL))
async def cancel_application(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await callback.message.edit_reply_markup(reply_markup=None)
    await state.clear()
    await callback.message.answer(texts.APPLY_CANCELLED, reply_markup=keyboards.main_menu())


@router.callback_query(Application.price, ApplyPriceCb.filter())
async def on_price(callback: CallbackQuery, callback_data: ApplyPriceCb, state: FSMContext, session: AsyncSession,
                   client: Client, settings: Settings) -> None:
    await callback.answer()
    session_id = (await state.get_data()).get(PENDING_APPLY_KEY)
    card, _ = await EnrollmentService(session).availability(client, session_id, local_today(settings.timezone))
    price = next((p for p in card.active_prices if p.id == callback_data.price_id), None) if card else None
    if price is None:
        await state.clear()
        await callback.message.answer(texts.APPLY_EXPIRED, reply_markup=keyboards.main_menu())
        return
    await callback.message.edit_reply_markup(reply_markup=None)
    await state.update_data(price_id=price.id)
    await catalog_flow.ask_seats(callback.message, state, price)


@router.callback_query(Application.seats, ApplySeatsCb.filter())
async def on_seats(callback: CallbackQuery, callback_data: ApplySeatsCb, state: FSMContext) -> None:
    await callback.answer()
    await callback.message.edit_reply_markup(reply_markup=None)
    await state.update_data(seats=callback_data.seats)  # диапазон ещё раз проверит сервис при отправке
    await catalog_flow.ask_comment(callback.message, state)


@router.message(Application.comment, F.text)
async def on_comment(message: Message, state: FSMContext, session: AsyncSession, client: Client,
                     settings: Settings) -> None:
    if len(message.text) > MAX_COMMENT_LENGTH:
        await message.answer(texts.APPLY_COMMENT_TOO_LONG, reply_markup=keyboards.apply_comment())
        return
    await state.update_data(comment=message.text.strip())
    await catalog_flow.show_confirm(message, state, session, client, local_today(settings.timezone))


@router.callback_query(Application.comment, ApplyCb.filter(F.action == ApplyAction.NO_COMMENT))
async def on_no_comment(callback: CallbackQuery, state: FSMContext, session: AsyncSession, client: Client,
                        settings: Settings) -> None:
    await callback.answer()
    await callback.message.edit_reply_markup(reply_markup=None)
    await state.update_data(comment=None)
    await catalog_flow.show_confirm(callback.message, state, session, client, local_today(settings.timezone))


@router.callback_query(Application.confirm, ApplyCb.filter(F.action == ApplyAction.SEND))
async def on_send(callback: CallbackQuery, state: FSMContext, session: AsyncSession, client: Client,
                  settings: Settings) -> None:
    await callback.answer()
    await callback.message.edit_reply_markup(reply_markup=None)
    data = await state.get_data()
    await state.clear()
    if PENDING_APPLY_KEY not in data:
        await callback.message.answer(texts.APPLY_EXPIRED, reply_markup=keyboards.main_menu())
        return
    result = await EnrollmentService(session).apply(
        client,
        session_id=data[PENDING_APPLY_KEY],
        price_option_id=data.get("price_id"),
        seats=data.get("seats", 1),
        comment=data.get("comment"),
        today=local_today(settings.timezone),
    )
    match result.outcome:
        case ApplyOutcome.CREATED:
            text = texts.APPLY_CREATED
        case ApplyOutcome.WAITLIST:
            text = texts.APPLY_WAITLISTED
        case ApplyOutcome.DUPLICATE:
            status = result.enrollment.status.value if result.enrollment else "application"
            text = texts.APPLY_DUPLICATE.format(status=texts.ENROLLMENT_STATUS_LABELS[status])
        case ApplyOutcome.CLOSED:
            text = texts.APPLY_CLOSED
        case _:
            text = texts.APPLY_EXPIRED
    await callback.message.answer(text, reply_markup=keyboards.main_menu())


@router.message(Application.comment)
async def comment_expects_text(message: Message) -> None:
    await message.answer(texts.REG_TEXT_ONLY, reply_markup=keyboards.apply_comment())


@router.message(StateFilter(Application.price, Application.seats, Application.confirm))
async def application_expects_buttons(message: Message) -> None:
    await message.answer(texts.REG_USE_BUTTONS)
