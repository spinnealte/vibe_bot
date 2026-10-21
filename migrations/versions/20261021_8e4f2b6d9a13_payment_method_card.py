"""payments.method: card

Revision ID: 8e4f2b6d9a13
Revises: 5d3a9c7e1b20
Create Date: 2026-10-21 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '8e4f2b6d9a13'
down_revision: Union[str, Sequence[str], None] = '5d3a9c7e1b20'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OLD_METHODS = "'invoice', 'cash', 'card_online', 'sbp', 'advance'"
NEW_METHODS = "'invoice', 'cash', 'card', 'card_online', 'sbp', 'advance'"


def upgrade() -> None:
    """Способ оплаты «картой или по СБП», который отмечает админ (полная версия, этап 1, шаг 4).

    Только расширяет список допустимых значений payments.method - прежние строки остаются как были.
    """
    op.drop_constraint(op.f('ck_payments_payment_method'), 'payments', type_='check')
    op.create_check_constraint(op.f('ck_payments_payment_method'), 'payments', f"method IN ({NEW_METHODS})")


def downgrade() -> None:
    # Платежи «card» не пропустит старое ограничение - отмену на базе с такими платежами придётся решать руками.
    op.drop_constraint(op.f('ck_payments_payment_method'), 'payments', type_='check')
    op.create_check_constraint(op.f('ck_payments_payment_method'), 'payments', f"method IN ({OLD_METHODS})")
