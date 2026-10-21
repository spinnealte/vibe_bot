"""Заказы, платежи и аванс (полная версия, этап 1): схема без БД. Ограничения на живой базе проверены при миграции."""

from stubbot.db.enums import AdvanceReason, CancelChoice, OrderStatus, PaymentMethod, PaymentState
from stubbot.db.models import Advance, Enrollment, Order, Payment
from stubbot.db.models.orders import ORDER_NUMBER_SEQUENCE, PAYMENT_HOURS


def _checks(table) -> dict[str, str]:
    return {c.name: str(c.sqltext) for c in table.constraints if c.__class__.__name__ == "CheckConstraint"}


def test_order_number_is_six_digits_in_order() -> None:
    seq = ORDER_NUMBER_SEQUENCE
    assert (seq.start, seq.minvalue, seq.maxvalue) == (100001, 100001, 999999)
    number = Order.__table__.c.number
    assert number.unique and "order_number_seq" in str(number.server_default.arg)
    assert "number BETWEEN 100000 AND 999999" in _checks(Order.__table__).values()


def test_order_defaults_and_deadline() -> None:
    table = Order.__table__
    assert table.c.status.server_default.arg == OrderStatus.AWAITING_PAYMENT.value
    assert table.c.total_amount.nullable  # «стоимость уточняется» - сумму потом впишет админ
    assert table.c.payer_type.nullable  # «Физлицо / Юрлицо» выбирают после записи
    assert not table.c.due_at.nullable and PAYMENT_HOURS == 72


def test_payments_are_parts_of_an_order() -> None:
    table = Payment.__table__
    assert {fk.column.table.name for fk in table.foreign_keys} == {"orders"}
    assert table.c.state.server_default.arg == PaymentState.SUCCEEDED.value  # ручную оплату отмечает админ
    assert "amount > 0" in _checks(table).values()
    assert PaymentMethod.ADVANCE.value == "advance"  # засчитанный аванс - тоже платёж по заказу


def test_advance_is_a_journal_changed_only_by_admin() -> None:
    table = Advance.__table__
    assert "updated_at" not in table.c  # строки не правятся: ошибку исправляет новая строка
    assert not table.c.created_by_telegram_id.nullable and table.c.order_id.nullable
    assert "amount <> 0" in _checks(table).values()
    assert {r.value for r in AdvanceReason} == {"cancelled_order", "applied_to_order", "refund", "correction"}


def test_enrollment_points_to_its_order() -> None:
    order_id = Enrollment.__table__.c.order_id
    assert order_id.nullable  # у листа ожидания заказа нет
    fk = next(iter(order_id.foreign_keys))
    assert fk.column.table.name == "orders" and fk.ondelete == "RESTRICT"


def test_cancel_choice_and_who_enrolled() -> None:
    """Шаг 3: выбор клиента при отмене оплаченной записи и «кто записал» коллегу из пары."""
    assert Order.__table__.c.cancel_choice.nullable
    assert "ck_orders_cancel_choice" in {c.name for c in Order.__table__.constraints}
    assert {c.value for c in CancelChoice} == {"transfer", "advance", "refund"}
    enrolled_by = Enrollment.__table__.c.enrolled_by_client_id
    fk = next(iter(enrolled_by.foreign_keys))
    assert enrolled_by.nullable and fk.column.table.name == "clients" and fk.ondelete == "RESTRICT"
