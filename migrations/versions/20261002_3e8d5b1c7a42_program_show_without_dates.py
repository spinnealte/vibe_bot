"""programs.show_without_dates: hide a course without dates from the schedule

Revision ID: 3e8d5b1c7a42
Revises: 9c4f1a7e2b35
Create Date: 2026-10-02 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '3e8d5b1c7a42'
down_revision: Union[str, Sequence[str], None] = '9c4f1a7e2b35'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Курс без ближайших проведений виден в афише как «даты уточняются»; админ может скрыть (решение 14)."""
    op.add_column('programs', sa.Column('show_without_dates', sa.Boolean(), server_default=sa.text('true'),
                                        nullable=False))


def downgrade() -> None:
    op.drop_column('programs', 'show_without_dates')
