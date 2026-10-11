"""sessions: dates are optional, archive instead of is_visible; programs.show_without_dates removed

Revision ID: c7d2e91f4a58
Revises: a1f47c2d9e60
Create Date: 2026-10-11 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'c7d2e91f4a58'
down_revision: Union[str, Sequence[str], None] = 'a1f47c2d9e60'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Афиша — только проведения (решение 16): проведение бывает без дат («даты уточняются»), вместо «скрыть»
    у него архив; курс — запись библиотеки и сам в афишу не попадает.

    Только структура: старые данные не переносятся — рабочей базы ещё нет, dev-база пересоздаётся.
    """
    op.add_column('sessions', sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True))
    op.drop_column('sessions', 'is_visible')
    op.alter_column('sessions', 'start_date', existing_type=sa.Date(), nullable=True)
    op.alter_column('sessions', 'end_date', existing_type=sa.Date(), nullable=True)
    op.create_check_constraint(op.f('ck_sessions_dates_together'), 'sessions',
                               '(start_date IS NULL) = (end_date IS NULL)')
    op.drop_column('programs', 'show_without_dates')


def downgrade() -> None:
    op.add_column('programs', sa.Column('show_without_dates', sa.Boolean(), server_default=sa.text('true'),
                                        nullable=False))
    op.drop_constraint(op.f('ck_sessions_dates_together'), 'sessions', type_='check')
    # В старой схеме проведения без дат не бывает: без этой строки откат споткнулся бы о NOT NULL.
    op.execute("UPDATE sessions SET start_date = created_at::date, end_date = created_at::date WHERE start_date IS NULL")
    op.alter_column('sessions', 'end_date', existing_type=sa.Date(), nullable=False)
    op.alter_column('sessions', 'start_date', existing_type=sa.Date(), nullable=False)
    op.add_column('sessions', sa.Column('is_visible', sa.Boolean(), server_default=sa.text('false'),
                                        nullable=False))
    op.drop_column('sessions', 'archived_at')
