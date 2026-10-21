"""orders.cancel_choice, enrollments.enrolled_by_client_id

Revision ID: 5d3a9c7e1b20
Revises: 20568b109f4b
Create Date: 2026-10-21 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5d3a9c7e1b20'
down_revision: Union[str, Sequence[str], None] = '20568b109f4b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Отмена записи самим клиентом (полная версия, этап 1, шаг 3).

    Только добавляет две колонки, NULL у всех прежних строк:
    - orders.cancel_choice - что клиент выбрал для уже внесённой оплаты: перенос, аванс или возврат;
    - enrollments.enrolled_by_client_id - кто записал коллегу из пары (нужно и в листе ожидания, где заказа нет).
    """
    op.add_column('orders', sa.Column('cancel_choice', sa.String(length=32), nullable=True))
    op.create_check_constraint(op.f('ck_orders_cancel_choice'), 'orders',
                               "cancel_choice IN ('transfer', 'advance', 'refund')")
    op.add_column('enrollments', sa.Column('enrolled_by_client_id', sa.BigInteger(), nullable=True))
    op.create_index(op.f('ix_enrollments_enrolled_by_client_id'), 'enrollments', ['enrolled_by_client_id'],
                    unique=False)
    op.create_foreign_key(op.f('fk_enrollments_enrolled_by_client_id_clients'), 'enrollments', 'clients',
                          ['enrolled_by_client_id'], ['id'], ondelete='RESTRICT')


def downgrade() -> None:
    op.drop_constraint(op.f('fk_enrollments_enrolled_by_client_id_clients'), 'enrollments', type_='foreignkey')
    op.drop_index(op.f('ix_enrollments_enrolled_by_client_id'), table_name='enrollments')
    op.drop_column('enrollments', 'enrolled_by_client_id')
    op.drop_constraint(op.f('ck_orders_cancel_choice'), 'orders', type_='check')
    op.drop_column('orders', 'cancel_choice')
