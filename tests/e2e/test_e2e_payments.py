"""Сквозные сценарии онлайн-оплаты (тестовая оплата fake - тот же путь, что с ЮKassa, без страницы оплаты).

Сверено с правилами для платёжных интеграций (OWASP Third-Party Payment Gateway Integration, требования ЮKassa):
- сумма считается на сервере из заказа, клиент её не задаёт; платёж создаётся с ключом идемпотентности;
- итог берётся у провайдера запросом статуса (не со слов клиента) и применяется один раз - кнопка, повтор, фоновая
  сверка и параллельные проверки не задваивают оплату и уведомления;
- чужой заказ и чужой платёж (подделанная кнопка) недоступны;
- деньги, пришедшие на отменённый или уже оплаченный заказ, не теряются: записаны и видны админу.
"""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from stubbot.db.enums import CancelChoice, EnrollmentStatus, OrderStatus, PaymentMethod, PaymentProvider, PaymentState, \
    PaymentStatus
from stubbot.db.models import Advance, Enrollment, Order, Payment
from stubbot.payments.gateway import GatewayError
from stubbot.services.admin_orders import AdminOrderService, OrderCategory
from stubbot.services.online_payments import NOT_CREATED
from stubbot.tg import texts
from stubbot.tg.callbacks import PayAction, PayCb
from stubbot.utils.money import format_rub
from tests.e2e.harness import ADMIN_ID, OWNER_ID, RUB, Harness, Person, fragment


async def _setup(h: Harness, user_id: int = 6001, phone: str = "+79990006001",
                 name: str = "Онлайнова Ольга Олеговна") -> tuple[Person, Order]:
    await h.owner().start()
    await h.admin().start()
    person = h.person(user_id, "Клиент")
    await person.register(phone, name)
    await person.enroll("Имплантация", "Один участник")
    await person.click(texts.BTN_PAY_INDIVIDUAL)
    order = await h.scalar(select(Order).order_by(Order.id.desc()))
    return person, order


async def _payments(h: Harness) -> list[Payment]:
    return await h.scalars(select(Payment).order_by(Payment.id))


async def _order(h: Harness, order_id: int) -> Order:
    return await h.scalar(select(Order).where(Order.id == order_id))


def _admin_saw(h: Harness, part: str) -> int:
    return sum(part in (m.plain or "") for m in h.tg.messages(OWNER_ID))


def test_online_payment_success_secures_the_seat(e2e) -> None:
    async def scenario(h: Harness) -> None:
        olga, order = await _setup(h)
        await olga.click(texts.BTN_PAY_ONLINE)
        [payment] = await _payments(h)
        assert payment.state is PaymentState.PENDING and payment.provider is PaymentProvider.FAKE
        assert payment.amount == 45_000 * RUB and payment.idempotency_key.startswith("online:")  # сумма - из заказа
        assert payment.provider_payment_id is not None
        await olga.click(texts.BTN_PAY_FAKE_OK)
        assert olga.sees(texts.PAYMENT_RECEIVED)
        [payment] = await _payments(h)
        assert payment.state is PaymentState.SUCCEEDED and payment.method is PaymentMethod.CARD_ONLINE
        order = await _order(h, order.id)
        assert order.status is OrderStatus.PAID and order.paid_at is not None
        enrollment = await h.scalar(select(Enrollment))
        assert enrollment.status is EnrollmentStatus.CONFIRMED and enrollment.payment_status is PaymentStatus.PAID
        assert _admin_saw(h, "№100001") >= 2 and _admin_saw(h, fragment(texts.ADMIN_ONLINE_TEST)) == 1
        # Повторные нажатия «прошла» и «Проверить» - тот же итог, без второго платежа и второго письма админам.
        before = len(h.tg.messages(OWNER_ID))
        await olga.press(PayCb(action=PayAction.FAKE_OK, item_id=payment.id).pack())
        await olga.press(PayCb(action=PayAction.CHECK, item_id=payment.id).pack())
        assert len(h.tg.messages(OWNER_ID)) == before and len(await _payments(h)) == 1
        # Оплачено - «Оплатить онлайн» больше не создаёт платёж.
        await olga.press(PayCb(action=PayAction.START, item_id=order.id).pack())
        assert len(await _payments(h)) == 1 and not olga.got_error()
        await olga.send(texts.BTN_CABINET)
        await olga.click(texts.BTN_MY_APPLICATIONS)
        assert not olga.has_button(texts.BTN_PAY_ONLINE, olga.last)

    e2e.run(scenario)


def test_declined_payment_and_retry(e2e) -> None:
    async def scenario(h: Harness) -> None:
        olga, order = await _setup(h)
        await olga.click(texts.BTN_PAY_ONLINE)
        await olga.click(texts.BTN_PAY_FAKE_FAIL)
        assert olga.sees(texts.PAY_ONLINE_FAILED)
        [failed] = await _payments(h)
        assert failed.state is PaymentState.CANCELED and failed.cancel_reason == "fake_declined"
        assert (await _order(h, order.id)).status is OrderStatus.AWAITING_PAYMENT
        await olga.click(texts.BTN_PAY_ONLINE_RETRY)
        await olga.click(texts.BTN_PAY_FAKE_OK)
        payments = await _payments(h)
        assert [p.state for p in payments] == [PaymentState.CANCELED, PaymentState.SUCCEEDED]
        assert payments[0].idempotency_key != payments[1].idempotency_key  # новая попытка - новый ключ
        assert (await _order(h, order.id)).status is OrderStatus.PAID

    e2e.run(scenario)


def test_same_link_is_given_again_within_the_hour(e2e) -> None:
    async def scenario(h: Harness) -> None:
        olga, order = await _setup(h)
        await olga.click(texts.BTN_PAY_ONLINE)
        first = olga.last.message_id
        await olga.press(PayCb(action=PayAction.START, item_id=order.id).pack())
        await olga.send(texts.BTN_CABINET)
        await olga.click(texts.BTN_MY_APPLICATIONS)
        await olga.click(texts.BTN_PAY_ONLINE)
        assert len(await _payments(h)) == 1  # одна ссылка - один платёж
        assert first
        # Ссылка старше 50 минут - уже новая (у ЮKassa на оплату час).
        [payment] = await _payments(h)
        async with h.factory() as session, session.begin():
            row = await session.get(Payment, payment.id)
            row.created_at = datetime.now(UTC) - timedelta(minutes=55)
        await olga.press(PayCb(action=PayAction.START, item_id=order.id).pack())
        assert len(await _payments(h)) == 2

    e2e.run(scenario)


def test_double_tap_creates_one_payment(e2e) -> None:
    async def scenario(h: Harness) -> None:
        olga, order = await _setup(h)
        data = PayCb(action=PayAction.START, item_id=order.id).pack()
        # Два нажатия почти одновременно: обращения одного человека идут по очереди.
        await asyncio.gather(olga.press(data), olga.press(data))
        assert len(await _payments(h)) == 1

    e2e.run(scenario)


def test_background_check_tells_once(e2e) -> None:
    async def scenario(h: Harness) -> None:
        olga, order = await _setup(h)
        await olga.click(texts.BTN_PAY_ONLINE)
        [payment] = await _payments(h)
        assert await h.payments.tick() == 0  # только что проверяли при создании - рано
        # Клиент оплатил на странице провайдера и не вернулся в бот.
        h.gateway.finish(payment.provider_payment_id, success=True)
        later = datetime.now(UTC) + timedelta(minutes=2)
        mark = olga.mark()
        assert await h.payments.tick(later) == 1
        assert any(fragment(texts.PAYMENT_RECEIVED) in m.plain for m in olga.since(mark))  # письмо клиенту
        assert (await _order(h, order.id)).status is OrderStatus.PAID
        # Следующая сверка и кнопка «Проверить» - без повторных писем.
        mark, admin_before = olga.mark(), len(h.tg.messages(OWNER_ID))
        assert await h.payments.tick(later + timedelta(minutes=5)) == 0
        await olga.press(PayCb(action=PayAction.CHECK, item_id=payment.id).pack())
        assert len([m for m in olga.since(mark) if m.from_bot]) <= 1  # только экран на месте кнопки
        assert len(h.tg.messages(OWNER_ID)) == admin_before

    e2e.run(scenario)


def test_parallel_checks_apply_the_result_once(e2e) -> None:
    async def scenario(h: Harness) -> None:
        olga, order = await _setup(h)
        await olga.click(texts.BTN_PAY_ONLINE)
        [payment] = await _payments(h)
        h.gateway.finish(payment.provider_payment_id, success=True)
        # Кнопка и фоновая сверка одновременно, каждая - своим соединением с базой (блокировки строк настоящие).
        results = await asyncio.gather(*(h.payments.check(payment.id) for _ in range(4)))
        assert sum(result.changed for result in results) == 1
        paid = await h.scalar(select(func.sum(Payment.amount)).where(Payment.state == PaymentState.SUCCEEDED))
        assert paid == 45_000 * RUB
        assert _admin_saw(h, fragment(texts.ADMIN_ONLINE_TEST)) == 1

    e2e.run(scenario)


def test_someone_else_cannot_pay_or_confirm_my_order(e2e) -> None:
    async def scenario(h: Harness) -> None:
        olga, order = await _setup(h)
        await olga.click(texts.BTN_PAY_ONLINE)
        [payment] = await _payments(h)
        thief = h.person(6099, "Чужой")
        await thief.register("+79990006099", "Чужаков Иван Иванович")
        guest = h.person(6098, "Гость")  # даже без регистрации
        await guest.start()
        for person in (thief, guest):
            for action, item in ((PayAction.START, order.id), (PayAction.CHECK, payment.id),
                                 (PayAction.FAKE_OK, payment.id), (PayAction.FAKE_FAIL, payment.id)):
                mark = person.mark()
                await person.press(PayCb(action=action, item_id=item).pack())
                shown = " ".join(m.plain or "" for m in person.since(mark))
                assert "№100001" not in shown and format_rub(45_000 * RUB) not in shown, (action, shown)  # ничего о чужом заказе
        [payment] = await _payments(h)
        assert payment.state is PaymentState.PENDING  # чужое «Оплата прошла» не сработало
        assert (await _order(h, order.id)).status is OrderStatus.AWAITING_PAYMENT
        # Чужие нажатия не создали платежей и не тронули выбор плательщика.
        assert len(await _payments(h)) == 1
        # Несуществующие номера - тоже спокойно.
        for item in (0, -1, 999_999):
            await thief.press(PayCb(action=PayAction.CHECK, item_id=item).pack())
            assert not thief.got_error()

    e2e.run(scenario)


def test_link_amount_is_the_rest_after_a_partial_payment(e2e) -> None:
    async def scenario(h: Harness) -> None:
        olga, order = await _setup(h)
        async with h.factory() as session, session.begin():
            await AdminOrderService(session, ADMIN_ID).mark_payment(order.id, 10_000 * RUB, PaymentMethod.CASH, "t1")
        await olga.click(texts.BTN_PAY_ONLINE)
        online = (await _payments(h))[-1]
        assert online.amount == 35_000 * RUB  # весь остаток, не больше
        await olga.click(texts.BTN_PAY_FAKE_OK)
        assert (await _order(h, order.id)).status is OrderStatus.PAID

    e2e.run(scenario)


def test_paid_by_invoice_while_the_link_was_open(e2e) -> None:
    """Пока ссылка открыта, админ отметил оплату по счёту, а клиент всё равно заплатил онлайн: деньги записаны,
    заказ не «переоплачен» молча - админам предупреждение о переплате."""

    async def scenario(h: Harness) -> None:
        olga, order = await _setup(h)
        await olga.click(texts.BTN_PAY_ONLINE)
        async with h.factory() as session, session.begin():
            await AdminOrderService(session, ADMIN_ID).mark_payment(order.id, 45_000 * RUB, PaymentMethod.INVOICE, "t2")
        await olga.click(texts.BTN_PAY_FAKE_OK)
        paid = await h.scalar(select(func.sum(Payment.amount)).where(Payment.state == PaymentState.SUCCEEDED))
        assert paid == 90_000 * RUB
        assert _admin_saw(h, "Переплата") == 1
        assert olga.sees(texts.PAYMENT_RECEIVED_OVERPAID)

    e2e.run(scenario)


def test_payment_after_the_order_was_cancelled(e2e) -> None:
    async def scenario(h: Harness) -> None:
        olga, order = await _setup(h)
        await olga.click(texts.BTN_PAY_ONLINE)
        fake_ok = olga.find_button(texts.BTN_PAY_FAKE_OK)
        await olga.send(texts.BTN_CABINET)
        await olga.click(texts.BTN_MY_APPLICATIONS)
        await olga.click(texts.BTN_CANCEL_APPLICATION)
        await olga.click(texts.BTN_CANCEL_APPLICATION_YES)
        assert (await _order(h, order.id)).status is OrderStatus.CANCELLED
        await olga.press(fake_ok[1].callback_data, fake_ok[0])  # оплатил по старой ссылке
        assert olga.sees(texts.PAYMENT_RECEIVED_AFTER_CANCEL)
        assert (await h.scalar(select(Enrollment))).status is EnrollmentStatus.CANCELLED_BY_CLIENT  # место не вернулось
        async with h.factory() as session:
            rows = await AdminOrderService(session, ADMIN_ID).category(OrderCategory.MONEY, datetime.now(UTC).date(),
                                                                       datetime.now(UTC))
        assert [o.id for o, _ in rows] == [order.id]  # админ видит деньги после отмены

    e2e.run(scenario)


@pytest.mark.xfail(strict=True, reason="БАГ (найден 28.10.2026): «деньги распределены» - по факту любого зачисления на "
                                       "аванс, а не по сумме: поздняя онлайн-оплата пропадает из «Деньги после отмены»")
def test_late_online_payment_after_the_money_was_settled(e2e) -> None:
    """Заказ частично оплачен, клиент отменил с «на аванс», админ зачислил аванс. Потом клиент всё-таки оплатил
    остаток по старой ссылке: эти деньги тоже должны дойти до «Деньги после отмены»."""

    async def scenario(h: Harness) -> None:
        olga, order = await _setup(h)
        async with h.factory() as session, session.begin():
            await AdminOrderService(session, ADMIN_ID).mark_payment(order.id, 10_000 * RUB, PaymentMethod.CASH, "t3")
        await olga.click(texts.BTN_PAY_ONLINE)
        fake_ok = olga.find_button(texts.BTN_PAY_FAKE_OK)
        await olga.send(texts.BTN_CABINET)
        await olga.click(texts.BTN_MY_APPLICATIONS)
        await olga.click(texts.BTN_CANCEL_APPLICATION)
        await olga.click(texts.BTN_CANCEL_ADVANCE)
        assert (await _order(h, order.id)).cancel_choice is CancelChoice.ADVANCE
        async with h.factory() as session, session.begin():
            result = await AdminOrderService(session, ADMIN_ID).credit_advance(order.id)
            assert result.amount == 10_000 * RUB
        await olga.press(fake_ok[1].callback_data, fake_ok[0])
        paid = await h.scalar(select(func.sum(Payment.amount)).where(Payment.state == PaymentState.SUCCEEDED))
        assert paid == 45_000 * RUB
        async with h.factory() as session:
            rows = await AdminOrderService(session, ADMIN_ID).category(OrderCategory.MONEY, datetime.now(UTC).date(),
                                                                       datetime.now(UTC))
            credited = await session.scalar(select(func.sum(Advance.amount)).where(Advance.order_id == order.id))
        assert credited == 10_000 * RUB
        assert [o.id for o, _ in rows] == [order.id], "35 000 ₽ после зачисления аванса не видны в «Деньги после отмены»"

    e2e.run(scenario)


def test_rows_that_never_reached_the_provider_are_closed(e2e) -> None:
    async def scenario(h: Harness) -> None:
        olga, order = await _setup(h)
        async with h.factory() as session, session.begin():
            session.add(Payment(order_id=order.id, amount=45_000 * RUB, method=PaymentMethod.CARD_ONLINE,
                                provider=PaymentProvider.FAKE, state=PaymentState.PENDING,
                                idempotency_key="online:lost", created_at=datetime.now(UTC) - timedelta(minutes=11)))
        await h.payments.tick()
        [payment] = await _payments(h)
        assert payment.state is PaymentState.CANCELED and payment.cancel_reason == NOT_CREATED

    e2e.run(scenario)


def test_provider_errors(e2e) -> None:
    async def scenario(h: Harness) -> None:
        olga, order = await _setup(h)
        calls = []

        async def broken(request):
            calls.append(request.key)
            raise GatewayError("нет ответа", retryable=True)

        h.gateway.create = broken
        await olga.click(texts.BTN_PAY_ONLINE)
        assert olga.sees(texts.PAY_ONLINE_ERROR) and olga.has_button(texts.BTN_PAY_ONLINE_RETRY)
        assert len(calls) == 2 and calls[0] == calls[1]  # один повтор - с тем же ключом
        [payment] = await _payments(h)
        assert payment.state is PaymentState.CANCELED and payment.cancel_reason == NOT_CREATED
        # Статус спросить не удалось - платёж ждёт, экран не падает.
        del h.gateway.create
        await olga.click(texts.BTN_PAY_ONLINE_RETRY)
        live = (await _payments(h))[-1]

        async def unavailable(provider_id):
            raise GatewayError("ЮKassa: ошибка 503", retryable=True)

        h.gateway.get = unavailable
        await olga.click(texts.BTN_PAY_ONLINE_CHECK)
        assert olga.sees(texts.PAY_ONLINE_PENDING)
        assert (await h.scalar(select(Payment).where(Payment.id == live.id))).state is PaymentState.PENDING

    e2e.run(scenario)


def test_payment_reminder_has_the_online_button(e2e) -> None:
    async def scenario(h: Harness) -> None:
        from stubbot.tg.reminders import PaymentReminders

        olga, order = await _setup(h)
        reminders = PaymentReminders(h.bot, h.factory, h.settings)
        mark = olga.mark()
        assert await reminders.tick(datetime.now(UTC) + timedelta(hours=25)) == 1
        [letter] = olga.since(mark)
        assert any(b.text == texts.BTN_PAY_ONLINE for b in letter.buttons())
        assert await reminders.tick(datetime.now(UTC) + timedelta(hours=26)) == 0  # не повторяется
        await olga.click(texts.BTN_PAY_ONLINE, letter)
        await olga.click(texts.BTN_PAY_FAKE_OK)
        assert await reminders.tick(datetime.now(UTC) + timedelta(hours=80)) == 0  # оплачен - «просрочено» нет

    e2e.run(scenario)
