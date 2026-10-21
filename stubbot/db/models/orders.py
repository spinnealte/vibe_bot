"""Заказы, платежи и аванс (полная версия, этап 1; решение 18 в docs/01_db_plan.md).

Заказ - одна запись на проведение: сам человек или пара по тарифу «Для двух коллег». Участники - строки enrollments
со ссылкой на заказ (своя строка у каждого, у коллеги тоже), платит плательщик заказа. Сумма фиксируется при записи
и больше не меняется, даже если тариф подорожает.

Оплата бывает частями: каждая поступившая сумма - строка payments, оплачено = сумма успешных платежей. Пока платежи
отмечает админ вручную; поля под онлайн-оплату (провайдер, его номер платежа) - на будущее, после договора.

Аванс клиента - журнал advances: + зачисление (оплаченное по отменённой записи), - списание (в оплату другого заказа,
возврат деньгами), правки админа. Баланс - сумма строк. Меняет аванс только админ, сам по себе он не тратится.
"""

from datetime import datetime

from sqlalchemy import BigInteger, CHAR, CheckConstraint, ForeignKey, Integer, Sequence, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from stubbot.db.base import Base, CreatedAtMixin, IntPK, TimestampMixin, str_enum
from stubbot.db.enums import AdvanceReason, OrderStatus, PayerType, PaymentMethod, PaymentProvider, PaymentState

# Номер заказа для людей - шесть цифр по порядку, с 100001 (решение пользователя 19.10.2026).
ORDER_NUMBER_SEQUENCE = Sequence("order_number_seq", start=100001, minvalue=100001, maxvalue=999999,
                                 metadata=Base.metadata)
PAYMENT_HOURS = 72  # срок оплаты с формирования заказа


class Order(TimestampMixin, Base):
    __tablename__ = "orders"
    __table_args__ = (
        CheckConstraint("number BETWEEN 100000 AND 999999", name="number_six_digits"),
        CheckConstraint("total_amount >= 0", name="total_amount_non_negative"),
    )
    # Номер и статус задаёт база: забираем их сразу при вставке - номер нужен в сообщении «Вы записаны».
    __mapper_args__ = {"eager_defaults": True}

    id: Mapped[IntPK]
    number: Mapped[int] = mapped_column(Integer, server_default=ORDER_NUMBER_SEQUENCE.next_value(), unique=True)
    payer_client_id: Mapped[int] = mapped_column(ForeignKey("clients.id", ondelete="RESTRICT"), index=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("sessions.id", ondelete="RESTRICT"), index=True)
    price_option_id: Mapped[int | None] = mapped_column(ForeignKey("price_options.id", ondelete="RESTRICT"))
    # NULL - ещё не выбрал «Физлицо / Юрлицо».
    payer_type: Mapped[PayerType | None] = mapped_column(str_enum(PayerType, "payer_type"))
    # К оплате, в копейках; зафиксировано при записи. NULL - у проведения не было цены («стоимость уточняется»).
    total_amount: Mapped[int | None] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(CHAR(3), server_default=text("'RUB'"))
    status: Mapped[OrderStatus] = mapped_column(
        str_enum(OrderStatus, "order_status"), server_default=OrderStatus.AWAITING_PAYMENT.value, index=True
    )
    due_at: Mapped[datetime]  # оплатить до: формирование заказа + PAYMENT_HOURS
    # Реквизиты юрлица - файл, который прислал клиент (Telegram file_id и имя файла).
    requisites_file_id: Mapped[str | None] = mapped_column(String(256))
    requisites_file_name: Mapped[str | None] = mapped_column(String(256))
    requisites_received_at: Mapped[datetime | None]
    invoice_sent_at: Mapped[datetime | None]
    # Напоминания фоновой задачи: отметка - чтобы после перезапуска бота не прислать то же ещё раз.
    payment_reminded_at: Mapped[datetime | None]  # через 24 часа без оплаты - клиенту и админам
    overdue_notified_at: Mapped[datetime | None]  # срок вышел - админам «просрочено» (бронь снимает админ)
    paid_at: Mapped[datetime | None]
    cancelled_at: Mapped[datetime | None]
    cancel_reason: Mapped[str | None] = mapped_column(String(512))
    admin_comment: Mapped[str | None] = mapped_column(Text)


class Payment(TimestampMixin, Base):
    """Поступившая сумма по заказу. Ручная оплата (отметил админ) сразу succeeded."""

    __tablename__ = "payments"
    __table_args__ = (CheckConstraint("amount > 0", name="amount_positive"),)

    id: Mapped[IntPK]
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="RESTRICT"), index=True)
    amount: Mapped[int] = mapped_column(Integer)  # копейки
    currency: Mapped[str] = mapped_column(CHAR(3), server_default=text("'RUB'"))
    method: Mapped[PaymentMethod] = mapped_column(str_enum(PaymentMethod, "payment_method"))
    provider: Mapped[PaymentProvider] = mapped_column(
        str_enum(PaymentProvider, "payment_provider"), server_default=PaymentProvider.MANUAL.value
    )
    state: Mapped[PaymentState] = mapped_column(
        str_enum(PaymentState, "payment_state"), server_default=PaymentState.SUCCEEDED.value
    )
    # Онлайн-оплата (после договора): номер платежа у провайдера и ключ от повторного создания.
    provider_payment_id: Mapped[str | None] = mapped_column(String(128), unique=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(64), unique=True)
    paid_at: Mapped[datetime | None]
    # Telegram ID админа, отметившего оплату (таблица admins пока не используется).
    created_by_telegram_id: Mapped[int | None] = mapped_column(BigInteger)
    comment: Mapped[str | None] = mapped_column(Text)


class Advance(CreatedAtMixin, Base):
    """Строка журнала аванса клиента: + зачисление, - списание. Журнал не правится: ошибку исправляет новая строка."""

    __tablename__ = "advances"
    __table_args__ = (CheckConstraint("amount <> 0", name="amount_not_zero"),)

    id: Mapped[IntPK]
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id", ondelete="RESTRICT"), index=True)
    # Заказ, с которого пришли деньги или в который ушли. Правка админа - без заказа.
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id", ondelete="RESTRICT"), index=True)
    amount: Mapped[int] = mapped_column(Integer)  # копейки
    currency: Mapped[str] = mapped_column(CHAR(3), server_default=text("'RUB'"))
    reason: Mapped[AdvanceReason] = mapped_column(str_enum(AdvanceReason, "advance_reason"))
    created_by_telegram_id: Mapped[int] = mapped_column(BigInteger)  # аванс меняет только админ
    comment: Mapped[str | None] = mapped_column(Text)
