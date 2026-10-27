"""Тестовая оплата без платёжного сервиса (ONLINE_PAYMENTS=fake): проверить весь путь в боте до договора с ЮKassa.

Страницы оплаты нет: вместо неё бот показывает кнопки «🧪 Оплата прошла / не прошла», и платёж получает этот
статус - дальше всё как с настоящей оплатой (место закрепляется, админам уведомление). Настоящих денег нет, поэтому
create_gateway пускает этот режим только на локальной базе: на сервере клиент не «оплатит» курс кнопкой.

Платежи хранятся в памяти: после перезапуска бота незавершённая тестовая оплата считается отменённой.
"""

import secrets

from stubbot.db.enums import PaymentProvider
from stubbot.payments.gateway import GatewayPayment, GatewayStatus, PaymentRequest

RESTARTED = "fake_restarted"  # причина отмены: бот перезапускали, тестовый платёж забыт


class FakeGateway:
    provider = PaymentProvider.FAKE
    has_payment_page = False

    def __init__(self) -> None:
        self._payments: dict[str, GatewayPayment] = {}
        self._by_key: dict[str, str] = {}

    async def create(self, request: PaymentRequest) -> GatewayPayment:
        # Как у ЮKassa: тот же ключ - тот же платёж.
        if request.key in self._by_key:
            return self._payments[self._by_key[request.key]]
        payment = GatewayPayment(id=f"fake-{secrets.token_hex(8)}", status=GatewayStatus.PENDING,
                                 amount=request.amount, test=True)
        self._payments[payment.id] = payment
        self._by_key[request.key] = payment.id
        return payment

    async def get(self, payment_id: str) -> GatewayPayment:
        payment = self._payments.get(payment_id)
        if payment is None:
            return GatewayPayment(id=payment_id, status=GatewayStatus.CANCELED, amount=0, cancel_reason=RESTARTED,
                                  test=True)
        return payment

    def finish(self, payment_id: str, success: bool) -> bool:
        """Итог «оплаты» - кнопка в боте. False - такого платежа нет или итог уже есть."""
        payment = self._payments.get(payment_id)
        if payment is None or payment.status is not GatewayStatus.PENDING:
            return False
        status = GatewayStatus.SUCCEEDED if success else GatewayStatus.CANCELED
        self._payments[payment_id] = GatewayPayment(
            id=payment.id, status=status, amount=payment.amount, test=True,
            method_type="bank_card" if success else None, cancel_reason=None if success else "fake_declined",
        )
        return True

    async def close(self) -> None:
        return None
