"""Что бот ждёт от платёжного сервиса: создать платёж и сказать его статус. Деньги - в копейках, как в базе.

Реализации: yookassa.py (ЮKassa, API v3) и fake.py (тестовая оплата без сервиса). Новый провайдер - ещё один
класс с теми же двумя методами и значение в PaymentProvider (миграцией).
"""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from stubbot.db.enums import PaymentProvider


class GatewayStatus(StrEnum):
    """Статус платежа у провайдера (названия - как в ЮKassa). Окончательные - succeeded и canceled."""

    PENDING = "pending"  # создан, ждём, пока человек оплатит (у ЮKassa на это 1 час)
    WAITING_FOR_CAPTURE = "waiting_for_capture"  # деньги заморожены - у нас не бывает: платёж с автосписанием
    SUCCEEDED = "succeeded"
    CANCELED = "canceled"


@dataclass(frozen=True)
class Receipt:
    """Чек 54-ФЗ: одна строка «Оплата заказа №…» на всю сумму. Значения ставки и признаков - из настроек."""

    email: str
    item: str  # название позиции в чеке, до 128 символов
    vat_code: int
    payment_mode: str
    payment_subject: str
    tax_system_code: int | None = None


@dataclass(frozen=True)
class PaymentRequest:
    key: str  # ключ от повторного создания: тот же ключ - тот же платёж (ЮKassa помнит 24 часа)
    amount: int  # копейки
    currency: str
    description: str  # видно человеку на странице оплаты и в личном кабинете ЮKassa, до 128 символов
    return_url: str  # куда вернуть человека после оплаты - в чат с ботом
    metadata: dict[str, str] = field(default_factory=dict)  # номер заказа и платежа - для сверки в кабинете ЮKassa
    receipt: Receipt | None = None


@dataclass(frozen=True)
class GatewayPayment:
    id: str  # номер платежа у провайдера
    status: GatewayStatus
    amount: int  # копейки, сколько платит человек
    confirmation_url: str | None = None  # страница оплаты; у тестовой оплаты её нет
    method_type: str | None = None  # чем заплатили: bank_card, sbp, sberbank, tinkoff_bank, yoo_money…
    cancel_reason: str | None = None  # почему отменён: expired_on_confirmation, insufficient_funds…
    test: bool = False  # платёж тестового магазина


class GatewayError(Exception):
    """Провайдер не ответил или отказал. retryable - можно повторить с тем же ключом (сбой сети, 5xx, 429)."""

    def __init__(self, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class PaymentGateway(Protocol):
    provider: PaymentProvider
    # Есть ли страница оплаты: у тестовой оплаты её нет - бот показывает кнопки «Оплата прошла / не прошла».
    has_payment_page: bool

    async def create(self, request: PaymentRequest) -> GatewayPayment: ...

    async def get(self, payment_id: str) -> GatewayPayment: ...

    async def close(self) -> None: ...
