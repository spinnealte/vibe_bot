"""Фоновая задача «напоминания об оплате» (полная версия, этап 1, шаг 5): раз в INTERVAL секунд - проверка заказов.

Что и когда напоминать, решает services/reminders.py. Здесь: короткая транзакция «выбрать и отметить» (тексты
собираются в ней же, пока данные под рукой), потом отправка - уже без открытой транзакции. Ошибка проверки или
отправки одного письма не останавливает ни остальные, ни бота: пишется в лог, следующая проверка - через интервал.
Запускается в __main__.py вместе с записью на курсы (APPLICATIONS_ENABLED).
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import InlineKeyboardMarkup
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stubbot.config import Settings
from stubbot.db.enums import PayerType
from stubbot.services.clients import ClientService
from stubbot.services.reminders import ReminderJob, ReminderKind, ReminderService
from stubbot.tg import admin_orders_views, keyboards, render, texts
from stubbot.utils.links import with_draft_text

logger = logging.getLogger(__name__)

INTERVAL = 300  # секунд между проверками: напоминание приходит не позже чем через 5 минут после срока
SEND_PAUSE = 0.05  # между письмами - не упереться в лимит Telegram (30 сообщений в секунду)


@dataclass(frozen=True)
class Letter:
    chat_id: int
    text: str
    markup: InlineKeyboardMarkup | None = None
    client_telegram_id: int | None = None  # письмо клиенту: заблокировал бота - отметить это у клиента


def letters(job: ReminderJob, settings: Settings) -> list[Letter]:
    """Письма по одному заказу: плательщику (если он в боте) и каждому админу."""
    order, payer = job.order, job.payer
    overdue = job.kind is ReminderKind.OVERDUE
    manager_url = (with_draft_text(settings.manager_url, texts.MANAGER_DRAFT_PAYMENT.format(number=order.number))
                   if settings.manager_url else None)
    result = []
    if payer.telegram_id is not None:
        text = render.payment_reminder(job.session, order, overdue, bool(manager_url), settings.timezone)
        # Ещё не выбрал «Физлицо / Юрлицо» - кнопки выбора прямо в напоминании (решение 22.10.2026). Физлицу
        # с известной суммой - «Оплатить онлайн», если онлайн-оплата включена (шаг 7).
        online = (settings.online_payments_enabled and order.payer_type is PayerType.INDIVIDUAL
                  and order.total_amount is not None)
        markup = keyboards.payment_reminder(order.id, order.payer_type is None and not overdue, manager_url, online)
        result.append(Letter(payer.telegram_id, text, markup, payer.telegram_id))
    admin_text, admin_markup = admin_orders_views.reminder_notice(job, settings.timezone)
    result += [Letter(chat_id, admin_text, admin_markup) for chat_id in settings.admin_chat_ids]
    return result


class PaymentReminders:
    def __init__(self, bot: Bot, session_factory: async_sessionmaker[AsyncSession], settings: Settings,
                 interval: float = INTERVAL) -> None:
        self.bot = bot
        self.session_factory = session_factory
        self.settings = settings
        self.interval = interval

    async def run_forever(self) -> None:
        logger.info("Напоминания об оплате: проверка каждые %s с", self.interval)
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Напоминания об оплате: проверка не удалась - повторим через интервал")
            await asyncio.sleep(self.interval)

    async def tick(self, now: datetime | None = None) -> int:
        """Одна проверка. Сколько заказов получили напоминание."""
        now = now or datetime.now(UTC)
        today = now.astimezone(ZoneInfo(self.settings.timezone)).date()
        async with self.session_factory() as session, session.begin():
            jobs = await ReminderService(session).claim(now, today)
            outbox = [letter for job in jobs for letter in letters(job, self.settings)]
        # Отметки уже в базе: дальше только отправка, перезапуск посреди неё не пришлёт то же второй раз.
        if jobs:
            logger.info("Напоминания об оплате: %s", ", ".join(f"№{job.order.number} ({job.kind.value})"
                                                             for job in jobs))
        for letter in outbox:
            await self._send(letter)
            await asyncio.sleep(SEND_PAUSE)
        return len(jobs)

    async def _send(self, letter: Letter) -> None:
        try:
            await self.bot.send_message(letter.chat_id, letter.text, reply_markup=letter.markup)
        except TelegramForbiddenError:
            if letter.client_telegram_id is not None:
                await self._mark_blocked(letter.client_telegram_id)
            logger.info("Напоминание об оплате не доставлено: бот заблокирован (чат %s)", letter.chat_id)
        except TelegramBadRequest as error:
            logger.warning("Напоминание об оплате не доставлено (чат %s): %s", letter.chat_id, error.message)

    async def _mark_blocked(self, telegram_id: int) -> None:
        async with self.session_factory() as session, session.begin():
            await ClientService(session).mark_bot_blocked(telegram_id)
