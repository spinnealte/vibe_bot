"""Онлайн-оплата в боте (полная версия, этап 1, шаг 7): платёж у провайдера, проверка итога, фоновая сверка.

Решает, можно ли платить и что записать, services/online_payments.py. Здесь - порядок шагов: каждый шаг - своя
короткая транзакция (как в tg/reminders.py), запрос к провайдеру - между ними, без открытой транзакции и блокировок.
1. start: строка платежа с ключом → коммит → создание у провайдера с тем же ключом (сбой сети - один повтор, ЮKassa
   вернёт тот же платёж) → номер платежа и ссылка в строку → коммит → ссылка клиенту. Не создался - строка закрыта,
   клиенту «не получилось»: ссылки у него нет, заплатить по ней нельзя.
2. check: статус у провайдера → apply под блокировкой → коммит → письма. Кнопка «Проверить оплату» и фоновая
   сверка одну оплату не задвоят: итог применяется один раз.
3. run_forever: раз в минуту - сверка платежей, которые ждут итога. Без HTTP-уведомлений от ЮKassa: серверу
   не нужен открытый адрес (их можно добавить позже, сверка останется запасным путём).
Запускается в __main__.py, если ONLINE_PAYMENTS не off.
"""

import asyncio
import logging
from dataclasses import dataclass, replace
from datetime import UTC, datetime

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stubbot.config import Settings
from stubbot.db.enums import PaymentState
from stubbot.payments import build_receipt, receipts_enabled
from stubbot.payments.fake import FakeGateway
from stubbot.payments.gateway import GatewayError, GatewayPayment, PaymentGateway, PaymentRequest
from stubbot.services.clients import ClientService
from stubbot.services.online_payments import NOT_CREATED, Applied, OnlinePaymentService, StartOutcome
from stubbot.tg import admin_orders_views, render, texts
from stubbot.tg.admin_notify import notify_admins

logger = logging.getLogger(__name__)

INTERVAL = 60  # секунд между сверками: оплату без нажатия «Проверить» бот заметит в течение минуты
BATCH = 20  # платежей за одну сверку; остальные - в следующую
RETRY_PAUSE = 1.0  # секунд перед повтором создания после сбоя сети


@dataclass(frozen=True)
class Started:
    """Что показать клиенту после «Оплатить онлайн»."""

    outcome: StartOutcome
    order_number: int = 0
    amount: int = 0  # копейки
    title: str = ""
    payment_id: int = 0
    url: str | None = None  # страница оплаты; у тестовой оплаты её нет


class OnlinePayments:
    def __init__(self, bot: Bot, session_factory: async_sessionmaker[AsyncSession], settings: Settings,
                 gateway: PaymentGateway, interval: float = INTERVAL) -> None:
        self.bot = bot
        self.session_factory = session_factory
        self.settings = settings
        self.gateway = gateway
        self.interval = interval

    @property
    def has_payment_page(self) -> bool:
        return self.gateway.has_payment_page

    def _service(self, session: AsyncSession) -> OnlinePaymentService:
        return OnlinePaymentService(session, self.gateway.provider)

    # --- «💳 Оплатить онлайн» --------------------------------------------------------------------------------------

    async def start(self, client_id: int, email: str | None, order_id: int, now: datetime | None = None) -> Started:
        now = now or datetime.now(UTC)
        receipts = receipts_enabled(self.settings)
        async with self.session_factory() as session, session.begin():
            prepared = await self._service(session).prepare(client_id, email, order_id, now, need_email=receipts)
        order, payment = prepared.order, prepared.payment
        if prepared.outcome is not StartOutcome.READY:
            return Started(prepared.outcome, order.number if order else 0)
        started = Started(StartOutcome.READY, order.number, payment.amount, prepared.title, payment.id,
                          payment.confirmation_url)
        if prepared.reused:
            return started
        request = PaymentRequest(
            key=payment.idempotency_key,
            amount=payment.amount,
            currency=payment.currency,
            description=texts.PAYMENT_DESCRIPTION.format(number=order.number, title=prepared.title),
            return_url=await self._return_url(),
            metadata={"order_id": str(order.id), "order_number": str(order.number), "payment_id": str(payment.id)},
            receipt=build_receipt(self.settings, email, texts.RECEIPT_ITEM.format(number=order.number,
                                                                                   title=prepared.title))
            if receipts and email else None,
        )
        try:
            remote = await self._create(request)
        except GatewayError as error:
            logger.warning("Онлайн-оплата заказа №%s: платёж #%s не создан - %s", order.number, payment.id, error)
            async with self.session_factory() as session, session.begin():
                await self._service(session).give_up(payment.id, NOT_CREATED, now)
            return Started(StartOutcome.ERROR, order.number)
        async with self.session_factory() as session, session.begin():
            await self._service(session).attach(payment.id, remote, now)
        return replace(started, url=remote.confirmation_url)

    async def _create(self, request: PaymentRequest) -> GatewayPayment:
        try:
            return await self.gateway.create(request)
        except GatewayError as error:
            if not error.retryable:
                raise
            await asyncio.sleep(RETRY_PAUSE)
            return await self.gateway.create(request)  # тот же ключ: если первый запрос дошёл, вернётся тот же платёж

    async def _return_url(self) -> str:
        """После оплаты ЮKassa возвращает человека в чат с ботом. Имя бота - только из API."""
        me = await self.bot.me()
        return f"https://t.me/{me.username}"

    # --- Итог оплаты -----------------------------------------------------------------------------------------------

    async def check(self, payment_id: int, client_id: int | None = None, now: datetime | None = None) -> Applied | None:
        """«🔄 Проверить оплату» (client_id - только свой платёж; экран покажет итог - письмо не нужно) или фоновая
        сверка (client_id=None; клиенту - письмо «Оплата получена»). None - платежа нет или он чужой."""
        now = now or datetime.now(UTC)
        async with self.session_factory() as session, session.begin():
            service = self._service(session)
            payment = await service.find(payment_id, client_id)
            if payment is None:
                return None
            if payment.state is not PaymentState.PENDING or payment.provider_payment_id is None:
                return await service.current(payment)  # итог уже есть или платёж ещё создаётся
            provider_id = payment.provider_payment_id
        return await self._sync(payment_id, provider_id, now, tell_client=client_id is None)

    async def finish_fake(self, payment_id: int, client_id: int, success: bool,
                          now: datetime | None = None) -> Applied | None:
        """Тестовая оплата: кнопка «🧪 Оплата прошла / не прошла» - вместо страницы оплаты."""
        if not isinstance(self.gateway, FakeGateway):
            return None
        async with self.session_factory() as session, session.begin():
            payment = await self._service(session).find(payment_id, client_id)
            provider_id = payment.provider_payment_id if payment is not None else None
        if provider_id is None:
            return None
        self.gateway.finish(provider_id, success)
        return await self.check(payment_id, client_id, now)

    async def _sync(self, payment_id: int, provider_id: str, now: datetime, tell_client: bool) -> Applied | None:
        try:
            remote = await self.gateway.get(provider_id)
        except GatewayError as error:
            logger.warning("Онлайн-оплата: статус платежа #%s не получен - %s", payment_id, error)
            async with self.session_factory() as session, session.begin():
                service = self._service(session)
                await service.touch(payment_id, now)
                payment = await service.find(payment_id)
                return await service.current(payment) if payment is not None else None
        async with self.session_factory() as session, session.begin():
            applied = await self._service(session).apply(payment_id, remote, now)
        # Письма - после коммита: оплата уже записана, сбой отправки её не откатит.
        if applied is not None and applied.changed and applied.payment.state is PaymentState.SUCCEEDED:
            await self._notify(applied, tell_client)
        return applied

    async def _notify(self, applied: Applied, tell_client: bool) -> None:
        telegram_id = applied.payer.telegram_id
        if tell_client and telegram_id is not None:
            try:
                await self.bot.send_message(telegram_id, render.online_payment_received(applied))
            except TelegramForbiddenError:
                async with self.session_factory() as session, session.begin():
                    await ClientService(session).mark_bot_blocked(telegram_id)
                logger.info("Клиенту #%s не доставлено «Оплата получена»: бот заблокирован", applied.payer.id)
            except TelegramBadRequest as error:
                logger.warning("Клиенту #%s не доставлено «Оплата получена»: %s", applied.payer.id, error.message)
        await notify_admins(self.bot, self.settings,
                            *admin_orders_views.notify_online_paid(applied, self.settings.timezone))

    # --- Фоновая сверка --------------------------------------------------------------------------------------------

    async def run_forever(self) -> None:
        logger.info("Онлайн-оплата (%s): сверка платежей каждые %s с", self.gateway.provider.value, self.interval)
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Онлайн-оплата: сверка не удалась - повторим через интервал")
            await asyncio.sleep(self.interval)

    async def tick(self, now: datetime | None = None) -> int:
        """Одна сверка. Сколько платежей проверено."""
        now = now or datetime.now(UTC)
        async with self.session_factory() as session, session.begin():
            due, closed = await self._service(session).due_for_check(now, BATCH)
        if closed:
            logger.info("Онлайн-оплата: %s платежей так и не создались у провайдера - закрыты", closed)
        for payment_id, provider_id in due:
            try:
                await self._sync(payment_id, provider_id, now, tell_client=True)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Онлайн-оплата: сверка платежа #%s не удалась", payment_id)
        return len(due)
