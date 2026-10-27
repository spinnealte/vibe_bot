"""payments: online payment link, status check, provider fake

Revision ID: b7c3e5a1f924
Revises: 8e4f2b6d9a13
Create Date: 2026-10-26 18:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'b7c3e5a1f924'
down_revision: Union[str, Sequence[str], None] = '8e4f2b6d9a13'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OLD_PROVIDERS = "'manual', 'yookassa'"
NEW_PROVIDERS = "'manual', 'yookassa', 'fake'"


def upgrade() -> None:
    """Каркас онлайн-оплаты (полная версия, этап 1, шаг 7).

    Только добавляет: три пустые колонки у payments (ссылка на оплату, время последней проверки статуса, причина
    отказа от провайдера) и значение 'fake' у payments.provider - тестовая оплата без ЮKassa. Прежние строки
    не меняются.
    """
    op.add_column('payments', sa.Column('confirmation_url', sa.String(length=512), nullable=True))
    op.add_column('payments', sa.Column('checked_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('payments', sa.Column('cancel_reason', sa.String(length=64), nullable=True))
    op.drop_constraint(op.f('ck_payments_payment_provider'), 'payments', type_='check')
    op.create_check_constraint(op.f('ck_payments_payment_provider'), 'payments', f"provider IN ({NEW_PROVIDERS})")


def downgrade() -> None:
    # Платежи «fake» не пропустит старое ограничение - на базе с ними отмену придётся решать руками.
    op.drop_constraint(op.f('ck_payments_payment_provider'), 'payments', type_='check')
    op.create_check_constraint(op.f('ck_payments_payment_provider'), 'payments', f"provider IN ({OLD_PROVIDERS})")
    op.drop_column('payments', 'cancel_reason')
    op.drop_column('payments', 'checked_at')
    op.drop_column('payments', 'confirmation_url')
