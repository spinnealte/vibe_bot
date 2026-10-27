"""orders.invoice_channel

Revision ID: c4d8f1a6e357
Revises: b7c3e5a1f924
Create Date: 2026-10-27 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c4d8f1a6e357'
down_revision: Union[str, Sequence[str], None] = 'b7c3e5a1f924'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Как ушёл счёт клиенту (полная версия, этап 1; решение 27.10.2026): через бот или отмечен вручную.

    Добавляет одну колонку. До неё отметка invoice_sent_at ставилась только после отправки файла через бота -
    таким заказам сразу проставляется 'bot', остальные строки не меняются.
    """
    op.add_column('orders', sa.Column('invoice_channel', sa.String(length=32), nullable=True))
    op.create_check_constraint(op.f('ck_orders_invoice_channel'), 'orders', "invoice_channel IN ('bot', 'manual')")
    op.execute("UPDATE orders SET invoice_channel = 'bot' WHERE invoice_sent_at IS NOT NULL")


def downgrade() -> None:
    op.drop_constraint(op.f('ck_orders_invoice_channel'), 'orders', type_='check')
    op.drop_column('orders', 'invoice_channel')
