"""orders, payments, advances; enrollments.order_id

Revision ID: 20568b109f4b
Revises: c7d2e91f4a58
Create Date: 2026-10-20 08:15:45.146162

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '20568b109f4b'
down_revision: Union[str, Sequence[str], None] = 'c7d2e91f4a58'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


ORDER_NUMBER_SEQUENCE = sa.Sequence('order_number_seq', start=100001, minvalue=100001, maxvalue=999999)


def upgrade() -> None:
    """Заказы, платежи и аванс клиента (решение 18, полная версия, этап 1).

    Только добавляет: новые таблицы и одну колонку enrollments.order_id (NULL у всех прежних записей), так что
    на рабочей базе с данными миграция ничего не меняет в существующих строках.
    """
    # Номер заказа - шесть цифр по порядку с 100001; последовательность принадлежит колонке и удаляется вместе с ней.
    op.execute(sa.schema.CreateSequence(ORDER_NUMBER_SEQUENCE))
    op.create_table('orders',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=True), nullable=False),
    sa.Column('number', sa.Integer(), server_default=sa.text("nextval('order_number_seq')"), nullable=False),
    sa.Column('payer_client_id', sa.BigInteger(), nullable=False),
    sa.Column('session_id', sa.BigInteger(), nullable=False),
    sa.Column('price_option_id', sa.BigInteger(), nullable=True),
    sa.Column('payer_type', sa.String(length=32), nullable=True),
    sa.Column('total_amount', sa.Integer(), nullable=True),
    sa.Column('currency', sa.CHAR(length=3), server_default=sa.text("'RUB'"), nullable=False),
    sa.Column('status', sa.String(length=32), server_default='awaiting_payment', nullable=False),
    sa.Column('due_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('requisites_file_id', sa.String(length=256), nullable=True),
    sa.Column('requisites_file_name', sa.String(length=256), nullable=True),
    sa.Column('requisites_received_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('invoice_sent_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('payment_reminded_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('overdue_notified_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('paid_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('cancelled_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('cancel_reason', sa.String(length=512), nullable=True),
    sa.Column('admin_comment', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("payer_type IN ('individual', 'organization')", name=op.f('ck_orders_payer_type')),
    sa.CheckConstraint("status IN ('awaiting_payment', 'partially_paid', 'paid', 'cancelled')", name=op.f('ck_orders_order_status')),
    sa.CheckConstraint('number BETWEEN 100000 AND 999999', name=op.f('ck_orders_number_six_digits')),
    sa.CheckConstraint('total_amount >= 0', name=op.f('ck_orders_total_amount_non_negative')),
    sa.ForeignKeyConstraint(['payer_client_id'], ['clients.id'], name=op.f('fk_orders_payer_client_id_clients'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['price_option_id'], ['price_options.id'], name=op.f('fk_orders_price_option_id_price_options'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['session_id'], ['sessions.id'], name=op.f('fk_orders_session_id_sessions'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_orders')),
    sa.UniqueConstraint('number', name=op.f('uq_orders_number'))
    )
    op.execute('ALTER SEQUENCE order_number_seq OWNED BY orders.number')
    op.create_index(op.f('ix_orders_payer_client_id'), 'orders', ['payer_client_id'], unique=False)
    op.create_index(op.f('ix_orders_session_id'), 'orders', ['session_id'], unique=False)
    op.create_index(op.f('ix_orders_status'), 'orders', ['status'], unique=False)
    op.create_table('advances',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=True), nullable=False),
    sa.Column('client_id', sa.BigInteger(), nullable=False),
    sa.Column('order_id', sa.BigInteger(), nullable=True),
    sa.Column('amount', sa.Integer(), nullable=False),
    sa.Column('currency', sa.CHAR(length=3), server_default=sa.text("'RUB'"), nullable=False),
    sa.Column('reason', sa.String(length=32), nullable=False),
    sa.Column('created_by_telegram_id', sa.BigInteger(), nullable=False),
    sa.Column('comment', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("reason IN ('cancelled_order', 'applied_to_order', 'refund', 'correction')", name=op.f('ck_advances_advance_reason')),
    sa.CheckConstraint('amount <> 0', name=op.f('ck_advances_amount_not_zero')),
    sa.ForeignKeyConstraint(['client_id'], ['clients.id'], name=op.f('fk_advances_client_id_clients'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['order_id'], ['orders.id'], name=op.f('fk_advances_order_id_orders'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_advances'))
    )
    op.create_index(op.f('ix_advances_client_id'), 'advances', ['client_id'], unique=False)
    op.create_index(op.f('ix_advances_order_id'), 'advances', ['order_id'], unique=False)
    op.create_table('payments',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=True), nullable=False),
    sa.Column('order_id', sa.BigInteger(), nullable=False),
    sa.Column('amount', sa.Integer(), nullable=False),
    sa.Column('currency', sa.CHAR(length=3), server_default=sa.text("'RUB'"), nullable=False),
    sa.Column('method', sa.String(length=32), nullable=False),
    sa.Column('provider', sa.String(length=32), server_default='manual', nullable=False),
    sa.Column('state', sa.String(length=32), server_default='succeeded', nullable=False),
    sa.Column('provider_payment_id', sa.String(length=128), nullable=True),
    sa.Column('idempotency_key', sa.String(length=64), nullable=True),
    sa.Column('paid_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_by_telegram_id', sa.BigInteger(), nullable=True),
    sa.Column('comment', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("method IN ('invoice', 'cash', 'card_online', 'sbp', 'advance')", name=op.f('ck_payments_payment_method')),
    sa.CheckConstraint("provider IN ('manual', 'yookassa')", name=op.f('ck_payments_payment_provider')),
    sa.CheckConstraint("state IN ('pending', 'succeeded', 'canceled')", name=op.f('ck_payments_payment_state')),
    sa.CheckConstraint('amount > 0', name=op.f('ck_payments_amount_positive')),
    sa.ForeignKeyConstraint(['order_id'], ['orders.id'], name=op.f('fk_payments_order_id_orders'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_payments')),
    sa.UniqueConstraint('idempotency_key', name=op.f('uq_payments_idempotency_key')),
    sa.UniqueConstraint('provider_payment_id', name=op.f('uq_payments_provider_payment_id'))
    )
    op.create_index(op.f('ix_payments_order_id'), 'payments', ['order_id'], unique=False)
    op.add_column('enrollments', sa.Column('order_id', sa.BigInteger(), nullable=True))
    op.create_index(op.f('ix_enrollments_order_id'), 'enrollments', ['order_id'], unique=False)
    op.create_foreign_key(op.f('fk_enrollments_order_id_orders'), 'enrollments', 'orders', ['order_id'], ['id'], ondelete='RESTRICT')


def downgrade() -> None:
    op.drop_constraint(op.f('fk_enrollments_order_id_orders'), 'enrollments', type_='foreignkey')
    op.drop_index(op.f('ix_enrollments_order_id'), table_name='enrollments')
    op.drop_column('enrollments', 'order_id')
    op.drop_index(op.f('ix_payments_order_id'), table_name='payments')
    op.drop_table('payments')
    op.drop_index(op.f('ix_advances_order_id'), table_name='advances')
    op.drop_index(op.f('ix_advances_client_id'), table_name='advances')
    op.drop_table('advances')
    op.drop_index(op.f('ix_orders_status'), table_name='orders')
    op.drop_index(op.f('ix_orders_session_id'), table_name='orders')
    op.drop_index(op.f('ix_orders_payer_client_id'), table_name='orders')
    op.drop_table('orders')
    op.execute('DROP SEQUENCE IF EXISTS order_number_seq')  # обычно уже удалена вместе с колонкой (OWNED BY)
