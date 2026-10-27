"""ЮKassa, API v3: создать платёж со страницей оплаты (POST /payments) и узнать его статус (GET /payments/{id}).

- Вход - HTTP Basic: shopId магазина и секретный ключ (тестового или боевого магазина - какой вписан в настройки).
- Каждое создание - с заголовком Idempotence-Key: повтор с тем же ключом (сбой сети, ответ 500) не создаст второй
  платёж, ЮKassa вернёт первый.
- Платёж одностадийный (capture: true): после оплаты деньги списываются сразу, статус - succeeded.
- Способ оплаты человек выбирает на странице ЮKassa: карта, СБП, SberPay и т. д. - что подключено в договоре.
- Чек 54-ФЗ - объект receipt в том же запросе (если включён в настройках), e-mail покупателя обязателен.

В лог - только номера платежей и коды ошибок ЮKassa: без ключей, сумм на человека и e-mail.
"""

import asyncio
import logging
from decimal import Decimal

import aiohttp
from pydantic import SecretStr

from stubbot.db.enums import PaymentProvider
from stubbot.payments.gateway import GatewayError, GatewayPayment, GatewayStatus, PaymentRequest, Receipt

logger = logging.getLogger(__name__)

API_URL = "https://api.yookassa.ru/v3"
TIMEOUT = 15  # секунд на запрос; ЮKassa советует ждать ответа до 30 с, но человек у экрана столько не ждёт
MAX_DESCRIPTION = 128  # лимит ЮKassa для description и названия позиции в чеке


def money(amount: int) -> str:
    """Копейки → «45000.00» (сумма в API ЮKassa - строкой с точкой)."""
    return f"{amount // 100}.{amount % 100:02d}"


def kopecks(value: str) -> int:
    return int(Decimal(value) * 100)


def request_body(request: PaymentRequest) -> dict:
    body = {
        "amount": {"value": money(request.amount), "currency": request.currency},
        "capture": True,
        "confirmation": {"type": "redirect", "return_url": request.return_url},
        "description": request.description[:MAX_DESCRIPTION],
        "metadata": request.metadata,
    }
    if request.receipt is not None:
        body["receipt"] = receipt_body(request.receipt, request.amount, request.currency)
    return body


def receipt_body(receipt: Receipt, amount: int, currency: str) -> dict:
    """Одна позиция на всю сумму платежа: сумма позиций обязана совпасть с суммой платежа."""
    body = {
        "customer": {"email": receipt.email},
        "items": [{
            "description": receipt.item[:MAX_DESCRIPTION],
            "quantity": 1,
            "amount": {"value": money(amount), "currency": currency},
            "vat_code": receipt.vat_code,
            "payment_mode": receipt.payment_mode,
            "payment_subject": receipt.payment_subject,
        }],
    }
    if receipt.tax_system_code is not None:
        body["tax_system_code"] = receipt.tax_system_code
    return body


def parse_payment(data: dict) -> GatewayPayment:
    """Объект платежа ЮKassa → то, что нужно боту."""
    try:
        status = GatewayStatus(data["status"])
    except ValueError as error:
        raise GatewayError(f"ЮKassa: неизвестный статус платежа {data.get('status')!r}") from error
    confirmation = data.get("confirmation") or {}
    method = data.get("payment_method") or {}
    cancellation = data.get("cancellation_details") or {}
    return GatewayPayment(
        id=data["id"],
        status=status,
        amount=kopecks(data["amount"]["value"]),
        confirmation_url=confirmation.get("confirmation_url"),
        method_type=method.get("type"),
        cancel_reason=cancellation.get("reason"),
        test=bool(data.get("test")),
    )


class YooKassaGateway:
    provider = PaymentProvider.YOOKASSA
    has_payment_page = True

    def __init__(self, shop_id: str, secret_key: SecretStr, api_url: str = API_URL) -> None:
        # Заголовок HTTP Basic «shopId:ключ» - собирается один раз и в лог не попадает.
        self._headers = {"Authorization": aiohttp.encode_basic_auth(shop_id, secret_key.get_secret_value())}
        self._api_url = api_url.rstrip("/")
        self._http: aiohttp.ClientSession | None = None

    async def create(self, request: PaymentRequest) -> GatewayPayment:
        data = await self._call("POST", "/payments", json=request_body(request),
                                headers={"Idempotence-Key": request.key})
        payment = parse_payment(data)
        logger.info("ЮKassa: создан платёж %s (%s)%s", payment.id, payment.status.value,
                    ", тестовый магазин" if payment.test else "")
        return payment

    async def get(self, payment_id: str) -> GatewayPayment:
        return parse_payment(await self._call("GET", f"/payments/{payment_id}"))

    async def close(self) -> None:
        if self._http is not None:
            await self._http.close()
            self._http = None

    async def _call(self, method: str, path: str, **kwargs) -> dict:
        if self._http is None:
            self._http = aiohttp.ClientSession(headers=self._headers, timeout=aiohttp.ClientTimeout(total=TIMEOUT))
        try:
            async with self._http.request(method, self._api_url + path, **kwargs) as response:
                data = await response.json(content_type=None)
                status = response.status
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as error:
            # Ответа нет или он не JSON: платёж мог и создаться - повтор с тем же ключом это выяснит.
            raise GatewayError(f"ЮKassa: нет ответа ({type(error).__name__})", retryable=True) from error
        if status >= 400:
            # Тело ошибки ЮKassa: code (invalid_request, invalid_credentials, forbidden…) и parameter - имя поля.
            data = data if isinstance(data, dict) else {}
            message = f"ЮKassa: ошибка {status} {data.get('code', '')} {data.get('parameter', '')}".rstrip()
            raise GatewayError(message, retryable=status >= 500 or status == 429)
        return data
