"""Клиент ЮKassa (stubbot/payments/yookassa.py) против локального сервера, который отвечает как API v3 ЮKassa
(формат запросов и ответов - по документации yookassa.ru/developers/api: POST /payments, GET /payments/{id},
HTTP Basic, Idempotence-Key, объект ошибки type/code/parameter). Настоящий HTTP-запрос, настоящий разбор ответа -
и весь путь в боте: «Оплатить онлайн» → ссылка → проверка → место закреплено.

Здесь нет сети и нет настоящего магазина: перед запуском - проверка с тестовым магазином ЮKassa (раздел 3.6
docs/03_online_payments.md)."""

import asyncio
import base64
import re
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from aiohttp import web
from pydantic import SecretStr
from sqlalchemy import select

from stubbot.config import get_settings
from stubbot.db.enums import OrderStatus, PaymentMethod, PaymentProvider, PaymentState
from stubbot.db.models import Client, Order, Payment
from stubbot.payments import yookassa
from stubbot.payments.gateway import GatewayError, GatewayStatus, PaymentRequest, Receipt
from stubbot.payments.yookassa import YooKassaGateway
from stubbot.tg import texts
from tests.e2e.harness import RUB, Harness

SHOP_ID, SECRET = "123456", "test_secret_key_e2e"
_MONEY = re.compile(r"^\d+\.\d{2}$")


class FakeYooKassa:
    """Сервер с поведением API ЮKassa, которое важно боту. fail - очередь ответов-сбоев перед нормальным ответом."""

    def __init__(self) -> None:
        self.payments: dict[str, dict] = {}
        self.by_key: dict[str, str] = {}
        self.requests: list[tuple[str, dict, dict | None]] = []  # метод, заголовки, тело
        self.fail: list[int | str] = []
        self.delay = 0.0
        app = web.Application()
        app.router.add_post("/v3/payments", self.create)
        app.router.add_get("/v3/payments/{payment_id}", self.get)
        self.app = app

    @staticmethod
    def _error(status: int, code: str, parameter: str | None = None) -> web.Response:
        body = {"type": "error", "id": str(uuid.uuid4()), "code": code, "description": code}
        if parameter:
            body["parameter"] = parameter
        return web.json_response(body, status=status)

    def _auth(self, request: web.Request) -> bool:
        expected = "Basic " + base64.b64encode(f"{SHOP_ID}:{SECRET}".encode()).decode()
        return request.headers.get("Authorization") == expected

    async def _maybe_fail(self) -> web.Response | None:
        if self.delay:
            await asyncio.sleep(self.delay)
        if not self.fail:
            return None
        failure = self.fail.pop(0)
        if failure == "not-json":
            return web.Response(text="<html>Bad gateway</html>", status=200, content_type="text/html")
        return self._error(failure, {500: "internal_server_error", 429: "too_many_requests"}.get(failure, "error"))

    async def create(self, request: web.Request) -> web.Response:
        body = await request.json()
        self.requests.append(("POST", dict(request.headers), body))
        if not self._auth(request):
            return self._error(401, "invalid_credentials")
        key = request.headers.get("Idempotence-Key")
        if not key or len(key) > 64:
            return self._error(400, "invalid_request", "Idempotence-Key")
        if (failure := await self._maybe_fail()) is not None:
            return failure
        if key in self.by_key:  # повтор с тем же ключом - тот же платёж
            return web.json_response(self.payments[self.by_key[key]])
        amount = body.get("amount", {})
        if not _MONEY.match(str(amount.get("value", ""))) or amount.get("currency") != "RUB":
            return self._error(400, "invalid_request", "amount")
        if body.get("capture") is not True or body.get("confirmation", {}).get("type") != "redirect":
            return self._error(400, "invalid_request", "confirmation")
        if not str(body["confirmation"].get("return_url", "")).startswith("https://"):
            return self._error(400, "invalid_request", "confirmation.return_url")
        if len(body.get("description", "")) > 128 or len(body.get("metadata", {})) > 16:
            return self._error(400, "invalid_request", "description")
        receipt = body.get("receipt")
        if receipt is not None:
            items = receipt.get("items", [])
            total = sum(int(float(i["amount"]["value"]) * 100) * int(i["quantity"]) for i in items)
            if not receipt.get("customer", {}).get("email") or total != int(float(amount["value"]) * 100):
                return self._error(400, "invalid_request", "receipt")
            if any(i.get("vat_code") not in range(1, 13) for i in items):
                return self._error(400, "invalid_request", "receipt.items.vat_code")
        payment_id = str(uuid.uuid4())
        self.payments[payment_id] = {
            "id": payment_id, "status": "pending", "paid": False, "amount": amount, "test": True,
            "confirmation": {"type": "redirect", "confirmation_url": f"https://yoomoney.ru/checkout/payments/v2/"
                                                                     f"contract?orderId={payment_id}"},
            "created_at": datetime.now(UTC).isoformat(), "description": body.get("description"),
            "metadata": body.get("metadata", {}), "recipient": {"account_id": SHOP_ID, "gateway_id": "1"},
            "refundable": False,
        }
        self.by_key[key] = payment_id
        return web.json_response(self.payments[payment_id])

    async def get(self, request: web.Request) -> web.Response:
        self.requests.append(("GET", dict(request.headers), None))
        if not self._auth(request):
            return self._error(401, "invalid_credentials")
        if (failure := await self._maybe_fail()) is not None:
            return failure
        payment = self.payments.get(request.match_info["payment_id"])
        if payment is None:
            return self._error(404, "not_found")
        return web.json_response(payment)

    def pay(self, payment_id: str, method: str = "bank_card") -> None:
        """Человек оплатил на странице ЮKassa."""
        self.payments[payment_id].update(status="succeeded", paid=True, payment_method={"type": method, "id": "pm"},
                                         captured_at=datetime.now(UTC).isoformat())

    def decline(self, payment_id: str, reason: str = "insufficient_funds") -> None:
        self.payments[payment_id].update(status="canceled", cancellation_details={"party": "payment_network",
                                                                                   "reason": reason})


async def _serve(fake: FakeYooKassa) -> tuple[web.AppRunner, str]:
    runner = web.AppRunner(fake.app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 0).start()
    port = runner.addresses[0][1]
    return runner, f"http://127.0.0.1:{port}/v3"


def _request(key: str = "online:k1", amount: int = 45_000 * RUB, receipt: Receipt | None = None) -> PaymentRequest:
    return PaymentRequest(key=key, amount=amount, currency="RUB", description="Заказ №100001 · Имплантация",
                          return_url="https://t.me/aquastom_e2e_bot",
                          metadata={"order_id": "1", "order_number": "100001", "payment_id": "1"}, receipt=receipt)


def test_gateway_contract() -> None:
    async def scenario() -> None:
        fake = FakeYooKassa()
        runner, url = await _serve(fake)
        gateway = YooKassaGateway(SHOP_ID, SecretStr(SECRET), api_url=url)
        try:
            created = await gateway.create(_request())
            assert created.status is GatewayStatus.PENDING and created.amount == 45_000 * RUB
            assert created.confirmation_url.startswith("https://yoomoney.ru/") and created.test
            method, headers, body = fake.requests[-1]
            assert headers["Idempotence-Key"] == "online:k1" and body["amount"] == {"value": "45000.00",
                                                                                    "currency": "RUB"}
            assert SECRET not in str(body)
            again = await gateway.create(_request())  # повтор с тем же ключом
            assert again.id == created.id and len(fake.payments) == 1
            fake.pay(created.id, "sbp")
            paid = await gateway.get(created.id)
            assert paid.status is GatewayStatus.SUCCEEDED and paid.method_type == "sbp"
            other = await gateway.create(_request("online:k2", amount=12_345))
            fake.decline(other.id)
            declined = await gateway.get(other.id)
            assert declined.status is GatewayStatus.CANCELED and declined.cancel_reason == "insufficient_funds"
            assert declined.amount == 12_345  # 123.45 ₽ - копейки не теряются
            # Чек 54-ФЗ: одна позиция на всю сумму, e-mail покупателя.
            receipt = Receipt(email="anna@example.ru", item="Курс «Имплантация»", vat_code=1,
                              payment_mode="full_prepayment", payment_subject="service")
            with_receipt = await gateway.create(_request("online:k3", receipt=receipt))
            assert with_receipt.status is GatewayStatus.PENDING
            assert fake.requests[-1][2]["receipt"]["items"][0]["amount"]["value"] == "45000.00"
        finally:
            await gateway.close()
            await runner.cleanup()

    asyncio.run(scenario())


@pytest.mark.parametrize(("failure", "retryable"), [(500, True), (429, True), ("not-json", True), (401, False),
                                                    (404, False)])
def test_gateway_errors(failure, retryable) -> None:
    async def scenario() -> None:
        fake = FakeYooKassa()
        runner, url = await _serve(fake)
        secret = SECRET if failure != 401 else "wrong"
        gateway = YooKassaGateway(SHOP_ID, SecretStr(secret), api_url=url)
        try:
            if failure not in (401, 404):
                fake.fail.append(failure)
            with pytest.raises(GatewayError) as error:
                if failure == 404:
                    await gateway.get("no-such-payment")
                else:
                    await gateway.create(_request())
            assert error.value.retryable is retryable
            assert secret not in str(error.value)  # ключ не попадает в текст ошибки (и в лог)
        finally:
            await gateway.close()
            await runner.cleanup()

    asyncio.run(scenario())


def test_gateway_no_connection_and_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        dead = YooKassaGateway(SHOP_ID, SecretStr(SECRET), api_url="http://127.0.0.1:9/v3")  # порт без сервера
        with pytest.raises(GatewayError) as error:
            await dead.create(_request())
        assert error.value.retryable
        await dead.close()
        fake = FakeYooKassa()
        fake.delay = 2.0
        runner, url = await _serve(fake)
        monkeypatch.setattr(yookassa, "TIMEOUT", 0.3)
        slow = YooKassaGateway(SHOP_ID, SecretStr(SECRET), api_url=url)
        try:
            with pytest.raises(GatewayError) as error:
                await slow.create(_request())
            assert error.value.retryable  # ответа не дождались - платёж мог создаться: повтор с тем же ключом
        finally:
            await slow.close()
            await runner.cleanup()

    asyncio.run(scenario())


def test_bot_pays_through_yookassa(e2e, monkeypatch: pytest.MonkeyPatch) -> None:
    """Весь путь с клиентом ЮKassa: сбой на создании (повтор с тем же ключом), ссылка на страницу оплаты кнопкой,
    «Проверить оплату» до и после оплаты, фоновая сверка, чек на e-mail, который бот спросил у клиента."""
    for key, value in {"ONLINE_PAYMENTS": "yookassa", "YOOKASSA_SHOP_ID": SHOP_ID, "YOOKASSA_SECRET_KEY": SECRET,
                       "YOOKASSA_RECEIPTS": "true", "YOOKASSA_VAT_CODE": "1"}.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()

    async def scenario(h: Harness) -> None:
        fake = FakeYooKassa()
        runner, url = await _serve(fake)
        h.gateway = YooKassaGateway(SHOP_ID, SecretStr(SECRET), api_url=url)
        h.payments.gateway = h.gateway
        h.pii.update({SECRET})
        try:
            await h.owner().start()
            await h.admin().start()
            anna = h.person(9501, "Анна")
            await anna.register("+79990009501", "Юкассова Анна Ивановна")
            await anna.enroll("Имплантация", "Один участник")
            await anna.click(texts.BTN_PAY_INDIVIDUAL)
            await anna.click(texts.BTN_PAY_ONLINE)
            assert anna.sees(texts.PAY_ONLINE_ASK_EMAIL)  # чек - на e-mail, а его нет в профиле
            await anna.send_sticker_like()
            await anna.send("не почта")
            assert anna.sees(texts.PAY_ONLINE_EMAIL_INVALID)
            fake.fail.append(500)  # первый запрос создания - сбой на стороне ЮKassa
            await anna.send("anna@example.ru")
            assert (await h.scalar(select(Client).where(Client.telegram_id == 9501))).email == "anna@example.ru"
            posts = [r for r in fake.requests if r[0] == "POST"]
            assert len(posts) == 2 and posts[0][1]["Idempotence-Key"] == posts[1][1]["Idempotence-Key"]
            assert posts[1][2]["receipt"]["customer"]["email"] == "anna@example.ru"
            assert len(fake.payments) == 1
            [payment] = await h.scalars(select(Payment))
            assert payment.provider is PaymentProvider.YOOKASSA and payment.provider_payment_id in fake.payments
            _, go = anna.find_button(texts.BTN_PAY_ONLINE_GO)
            assert go.url == fake.payments[payment.provider_payment_id]["confirmation"]["confirmation_url"]
            assert not anna.has_button(texts.BTN_PAY_FAKE_OK)  # кнопок тестовой оплаты нет
            await anna.click(texts.BTN_PAY_ONLINE_CHECK)
            assert anna.sees(texts.PAY_ONLINE_PENDING)
            fake.pay(payment.provider_payment_id)
            mark = anna.mark()
            assert await h.payments.tick(datetime.now(UTC) + timedelta(minutes=2)) == 1
            assert anna.since(mark) and (await h.scalar(select(Order))).status is OrderStatus.PAID
            row = await h.scalar(select(Payment))
            assert row.state is PaymentState.SUCCEEDED and row.method is PaymentMethod.CARD_ONLINE
            assert all(SECRET not in r.getMessage() for r in h.logs)
        finally:
            await h.gateway.close()
            await runner.cleanup()

    e2e.run(scenario)
